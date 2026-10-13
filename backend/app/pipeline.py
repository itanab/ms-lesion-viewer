"""Study / mask preparation, measurement and provenance. Shared by the API and the job runner."""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy import ndimage

from . import __version__
from .context import AppContext
from .errors import AppError
from .imaging import components, geometry, nifti, phantom, render
from .imaging.geometry import DisplayPlan
from .storage.files import atomic_save_npy, remove_tree

log = logging.getLogger("mslv.pipeline")
MASK_TOL_MM = 0.01
PROVENANCE_SCHEMA = "1.0"
SIZE_EDGES_ML = [0.05, 0.2, 0.5, 1.0]
DIAMETER_STATEMENT = (
    "Maximum in-plane diameter (current slice): exact maximum 2D Feret diameter of the 8-connected pixel-cell "
    "footprint on the display-grid slice, in physical millimetres. Maximum 3D diameter: exact farthest pair of "
    "voxel-cell corners of the 26-connected candidate on the original grid. Descriptive only; no diagnostic thresholds."
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ study
def study_original_path(ctx: AppContext, study: dict) -> Path:
    return ctx.paths.study_dir(study["id"]) / study["original_file"]


def display_plan(study: dict) -> DisplayPlan:
    g = study["geometry"]["original"]
    return geometry.make_display_plan(g["shape"], np.asarray(g["affine"]))


def fail_study(ctx: AppContext, study_id: str, code: str, message: str) -> None:
    for tmp in ctx.paths.study_dir(study_id).glob("*.tmp"):
        tmp.unlink(missing_ok=True)
    ctx.db.update("studies", study_id, state="failed", failure_code=code, failure_message=message)


def prepare_study(ctx: AppContext, study_id: str, original_name: str) -> None:
    """Validate, build the display volume and default window. Never raises; records failure."""
    s = ctx.settings
    try:
        d = ctx.paths.study_dir(study_id)
        path = d / original_name
        info = nifti.inspect_nifti(path, s)
        plan = geometry.make_display_plan(info.shape, info.affine_mm, s)
        data = nifti.load_data(path)
        window = render.default_window(data)
        irange = {"min": float(data.min()), "max": float(data.max())}
        disp = geometry.apply_plan(plan, data, order=1, dtype=np.float32)
        del data
        atomic_save_npy(d / "display.npy", disp)
        ctx.db.update("studies", study_id, state="ready", original_file=original_name,
                      file_bytes=path.stat().st_size,
                      geometry={"original": info.to_json(), "display": plan.to_json()},
                      default_window=window, intensity_range=irange)
    except AppError as e:
        fail_study(ctx, study_id, e.code, e.message)
    except MemoryError:
        fail_study(ctx, study_id, "SIZE_LIMIT_EXCEEDED", "Not enough memory to prepare this volume.")
    except Exception as e:  # noqa: BLE001
        log.error("study preparation failed type=%s id=%s", type(e).__name__, study_id)
        fail_study(ctx, study_id, "PREPARATION_FAILED", "The image could not be prepared.")


def write_nifti(path: Path, arr: np.ndarray, affine: np.ndarray, dtype=np.float32, descrip: str = "") -> None:
    img = nib.Nifti1Image(arr.astype(dtype), affine)
    img.header.set_xyzt_units("mm")
    if descrip:
        img.header["descrip"] = descrip.encode()[:79]
    nib.save(img, str(path))


def prepare_demo(ctx: AppContext, study_id: str) -> None:
    s = ctx.settings
    try:
        aff = phantom.centred_affine(s.demo_shape, s.demo_spacing, np.diag([-1.0, -1.0, 1.0]))   # LPS-style storage
        vol, mask = phantom.make_phantom(s.demo_shape, aff)
        d = ctx.paths.study_dir(study_id)
        write_nifti(d / "original.nii.gz", vol, aff, np.float32, "SYNTHETIC DEMO PHANTOM")
        raw = d / "demo_mask_source.nii.gz"
        write_nifti(raw, mask, aff, np.uint8, "SYNTHETIC DEMO MASK")
    except Exception as e:  # noqa: BLE001
        log.error("demo generation failed type=%s", type(e).__name__)
        fail_study(ctx, study_id, "DEMO_FAILED", "The synthetic demo could not be generated.")
        return
    prepare_study(ctx, study_id, "original.nii.gz")
    mask_id = str(uuid.uuid4())
    ctx.db.insert_mask(mask_id, study_id, "synthetic_demo")
    prepare_mask(ctx, study_id, mask_id, raw, "synthetic_demo")


# ------------------------------------------------------------------ mask
def export_mask_nifti(path: Path, mask_u8: np.ndarray, geo: dict, synthetic: bool) -> None:
    """Original-grid uint8 labelmap preserving the source's spatial unit and coded forms."""
    aff = np.asarray(geo["affine_file_units"], dtype=float)
    img = nib.Nifti1Image(mask_u8.astype(np.uint8), aff)
    img.header.set_data_dtype(np.uint8)
    img.header.set_xyzt_units(geo["header_spatial_unit"])
    img.set_sform(aff, code=geo["sform_code"] or 2)
    img.set_qform(aff, code=geo["qform_code"] or 0, strip_shears=True)
    img.header["descrip"] = b"SYNTHETIC DEMO - NOT A REAL SEGMENTATION" if synthetic else b"MS lesion mask label 1"
    nib.save(img, str(path))


def fail_mask(ctx: AppContext, mdir: Path, mask_id: str, code: str, message: str) -> None:
    for f in list(mdir.glob("*.tmp")) + list(mdir.glob("tmp_*")):
        f.unlink(missing_ok=True)
    ctx.db.update("masks", mask_id, state="failed", failure_code=code, failure_message=message)


def _anchors(disp_labels: np.ndarray, plan: DisplayPlan) -> dict[int, dict]:
    """Per candidate: the display voxel nearest the centre of mass that lies inside the candidate."""
    out: dict[int, dict] = {}
    for cid, sl in enumerate(ndimage.find_objects(disp_labels), start=1):
        if sl is None:
            continue
        idx = np.argwhere(disp_labels[sl] == cid) + np.array([s.start for s in sl])
        c = idx.mean(axis=0)
        v = idx[int(np.argmin(((idx - c) ** 2).sum(axis=1)))]
        out[cid] = {"display_index": [int(x) for x in v], "ras_mm": geometry.display_index_to_ras(plan, v)}
    return out


def _measure_payload(study: dict, mask_id: str, mask_rev: int, plan: DisplayPlan, labels, plane: str, index: int) -> dict:
    raster = geometry.extract_raster(labels, plane, index)
    g = geometry.plane_geometry(plan, plane)
    base = {
        "plane": plane, "index": index, "display_revision": study["display_revision"],
        "mask_id": mask_id, "mask_revision": mask_rev,
        "method": components.METHOD, "method_version": components.METHOD_VERSION, "units": "mm",
        "label": "Maximum in-plane diameter (current slice)",
        "display_pixel_spacing_mm": g["pixel_spacing_mm"],
        "resampling": plan.method,
        "footprint_id_scope": "per candidate, ordered by first pixel in raster order",
    }
    try:
        fps = components.slice_footprints(raster, plan, plane, index, tuple(g["pixel_spacing_mm"]))
        return {**base, "available": True, "footprints": fps}
    except Exception as e:  # noqa: BLE001
        log.error("measurement failed type=%s", type(e).__name__)
        return {**base, "available": False, "footprints": [],
                "reason": "Measurement unavailable: the exact computation could not be completed."}


def prepare_mask(ctx: AppContext, study_id: str, mask_id: str, raw_path: Path, source: str,
                 model_info: dict | None = None, cleanup_source: bool = True, accept_subvoxel_offset: bool = False) -> None:
    """Validate geometry/labels, label components, build display layers and statistics, publish atomically."""
    s = ctx.settings
    mdir = ctx.paths.mask_dir(study_id, mask_id)
    mdir.mkdir(parents=True, exist_ok=True)
    try:
        study = ctx.db.get_study(study_id)
        if study is None or study["state"] != "ready":
            raise AppError(409, "STUDY_NOT_READY", "The MRI is not ready.")
        geo = study["geometry"]["original"]
        affine = np.asarray(geo["affine"])
        info = nifti.inspect_nifti(raw_path, s)
        override = None
        same_shape = tuple(info.shape) == tuple(geo["shape"])
        dist = nifti.max_corner_distance(info.shape, info.affine_mm, geo["shape"], affine) if same_shape else None
        if not same_shape or dist > MASK_TOL_MM:
            off = nifti.subvoxel_translation(affine, info.affine_mm) if same_shape else None
            if off is not None and not accept_subvoxel_offset:
                raise AppError(422, "MASK_HEADER_OFFSET",
                               f"The mask has the same shape as the MRI but its header is offset by {dist:.2f} mm "
                               "(at most half a voxel per axis). If it was created on this MRI's array, upload it again "
                               "and choose 'Accept sub-voxel header offset'.",
                               {"max_corner_offset_mm": dist, "offset_voxels": [float(x) for x in off]})
            if off is None:
                raise AppError(422, "MASK_GEOMETRY_MISMATCH",
                               "Mask must match the original MRI grid (same shape and affine within 0.01 mm). "
                               "Preprocessing/export must place the mask in the MRI's original space.")
            override = {"max_corner_offset_mm": dist, "offset_voxels": [float(x) for x in off],
                        "note": "User accepted a sub-voxel header offset; the mask is treated as aligned to the MRI array "
                                "and exported with the MRI's geometry."}
        data = nifti.load_data(raw_path)
        if not np.isin(data, (0.0, 1.0)).all():
            raise AppError(422, "MASK_INVALID_LABELS",
                           "Mask values must be exactly 0 or 1 (probability or multi-label maps are not accepted).")
        mask = data.astype(np.uint8)
        del data
        labels, n_cand = components.label_native(mask)
        voxel_mm3 = abs(float(np.linalg.det(affine[:3, :3])))
        total = int(mask.sum())
        cands = components.lesion_3d_stats(labels, n_cand, affine)
        plan = display_plan(study)
        disp_labels = geometry.apply_plan(plan, labels, order=0, dtype=np.int32)
        if not plan.resampled and int((disp_labels > 0).sum()) != total:
            raise AppError(500, "DISPLAY_MISMATCH", "Display mask does not match the original mask.")
        disp_mask = (disp_labels > 0).astype(np.uint8)

        tmp = mdir / "tmp_mask_original.nii.gz"
        export_mask_nifti(tmp, mask, geo, synthetic=(source == "synthetic_demo"))
        final = mdir / "mask_original.nii.gz"
        tmp.rename(final)
        chk = nifti.inspect_nifti(final, s)    # save/reload geometry check before publishing
        if nifti.max_corner_distance(chk.shape, chk.affine_mm, geo["shape"], affine) > 1e-3:
            raise AppError(500, "EXPORT_GEOMETRY_ERROR", "Exported mask geometry could not be verified.")
        atomic_save_npy(mdir / "display_labels.npy", disp_labels)
        atomic_save_npy(mdir / "display_mask.npy", disp_mask)

        # Whole-scan analysis that makes the lesion table and the slice-load strip instant.
        anchors = _anchors(disp_labels, plan)
        axial_items, max_axial = [], {}
        for z in range(plan.shape[2]):
            p = _measure_payload(study, mask_id, 1, plan, disp_labels, "axial", z)
            axial_items.append(("axial", z, p))
            for fp in p["footprints"]:
                cid = fp["candidate_id"]
                max_axial[cid] = max(max_axial.get(cid, 0.0), fp["diameter_mm"])
        for c in cands:
            c["max_axial_diameter_mm"] = max_axial.get(c["candidate_id"])
            c["anchor"] = anchors.get(c["candidate_id"])
        slice_load = {
            "axial": [int(x) for x in disp_mask.sum(axis=(0, 1))],
            "coronal": [int(x) for x in disp_mask.sum(axis=(0, 2))],
            "sagittal": [int(x) for x in disp_mask.sum(axis=(1, 2))],
        }
        vols = np.array([c["volume_ml"] for c in cands]) if cands else np.zeros(0)
        stats = {
            "voxel_count": total, "volume_mm3": total * voxel_mm3, "volume_ml": total * voxel_mm3 / 1000.0,
            "candidate_count": n_cand, "empty": total == 0,
            "message": "No labeled lesion voxels" if total == 0 else None,
            "largest_lesion_ml": float(vols.max()) if vols.size else None,
            "median_lesion_ml": float(np.median(vols)) if vols.size else None,
            "largest_3d_diameter_mm": max((c["max_3d_diameter_mm"] for c in cands), default=None),
            "slices_with_lesions": {k: int(sum(1 for x in v if x > 0)) for k, v in slice_load.items()},
            "size_distribution": {"edges_ml": SIZE_EDGES_ML,
                                  "counts": [int(x) for x in np.bincount(np.searchsorted(SIZE_EDGES_ML, vols, side="right"), minlength=len(SIZE_EDGES_ML) + 1)]},
            "slice_load": slice_load,
            "note": "Descriptive output; not a disease severity score.",
        }
        prov = {
            "schema_version": PROVENANCE_SCHEMA, "study_id": study_id, "mask_id": mask_id,
            "mask_source": source, "synthetic": source == "synthetic_demo", "app_version": __version__,
            "image_sha256": sha256_file(study_original_path(ctx, study)),
            "mask_sha256": sha256_file(raw_path) if source == "uploaded" else sha256_file(final),
            "exported_mask_sha256": sha256_file(final),
            "label_semantics": {"0": "background", "1": "lesion (segmentation-derived, not a diagnosis)"},
            "original_geometry": study["geometry"]["original"], "display_geometry": study["geometry"]["display"],
            "resampling": {"display_image": study["geometry"]["display"]["image_interpolation"],
                           "display_mask": study["geometry"]["display"]["mask_interpolation"]},
            "model": model_info if source == "model" else None,
            "statistics": {k: v for k, v in stats.items() if k != "slice_load"},
            "components": {"connectivity": "26 (3D)",
                           "id_order": "first occupied voxel in [x,y,z] lexicographic order, original grid",
                           "candidates": cands,
                           "note": "Candidates are segmentation-derived, not verified anatomical lesions; touching lesions may share an ID."},
            "diameter": {"method_2d": components.METHOD, "method_3d": components.METHOD_3D,
                         "version": components.METHOD_VERSION, "statement": DIAMETER_STATEMENT},
        }
        if override:
            prov["geometry_override"] = override
        if source == "synthetic_demo":
            prov["notice"] = "SYNTHETIC DEMO DATA - not a real scan or real lesion detection."
        ctx.db.put_measurements(mask_id, study["display_revision"], axial_items)
        ctx.db.update("masks", mask_id, state="ready", statistics=stats, candidates=cands, provenance=prov)
        ctx.db.update("studies", study_id, selected_mask_id=mask_id)
    except AppError as e:
        fail_mask(ctx, mdir, mask_id, e.code, e.message)
    except Exception as e:  # noqa: BLE001
        log.error("mask preparation failed type=%s id=%s", type(e).__name__, mask_id)
        fail_mask(ctx, mdir, mask_id, "PREPARATION_FAILED", "The mask could not be prepared.")
    finally:
        if cleanup_source:
            raw_path.unlink(missing_ok=True)


# ------------------------------------------------------------------ measurement / provenance / delete
def measure_slice(ctx: AppContext, study: dict, mask: dict, plane: str, index: int) -> dict:
    rev = study["display_revision"]
    cached = ctx.db.get_measurement(mask["id"], rev, plane, index)
    if cached is not None:
        return cached
    plan = display_plan(study)
    labels = ctx.volumes.get(ctx.paths.mask_dir(study["id"], mask["id"]) / "display_labels.npy")
    payload = _measure_payload(study, mask["id"], mask["revision"], plan, labels, plane, index)
    if payload["available"]:
        ctx.db.put_measurements(mask["id"], rev, [(plane, index, payload)])
    return payload


def build_provenance(ctx: AppContext, study: dict, mask: dict) -> dict:
    prov = dict(mask["provenance"])
    prov["created_at"] = mask["created_at"]
    prov["exported_measurements"] = {
        "scope": "partial: only slices whose measurements were computed (all axial slices at preparation, other planes while viewing); "
                 "not an all-slice or maximum-over-volume analysis in every plane",
        "slices": ctx.db.measurements_of(mask["id"]),
    }
    return prov


def delete_study_files(ctx: AppContext, study_id: str) -> None:
    d = ctx.paths.study_dir(study_id)
    ctx.volumes.forget_under(d)
    ctx.slice_cache.drop_where(lambda k: k[0] == study_id)
    remove_tree(d)
