"""Versioned REST API under /api/v1."""
from __future__ import annotations

import math
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import APP_NAME, __version__, pipeline
from ..context import AppContext
from ..errors import AppError, not_found
from ..imaging import geometry, render
from ..storage.files import valid_uuid
from ..worker import adapter_status

router = APIRouter(prefix="/api/v1")
CHUNK = 1 << 20


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


# ----------------------------------------------------------------- helpers
def _plane(plane: str) -> str:
    if plane not in geometry.PLANES:
        raise AppError(422, "INVALID_PLANE", "Plane must be axial, coronal or sagittal.")
    return plane


def _get_study(ctx: AppContext, study_id: str, ready: bool = False) -> dict:
    study = ctx.db.get_study(valid_uuid(study_id, "Study"))
    if study is None:
        raise not_found("Study")
    if ready and study["state"] != "ready":
        raise AppError(409, "STUDY_NOT_READY", "The MRI is not ready yet.", {"state": study["state"]})
    return study


def _get_mask(ctx: AppContext, study_id: str, mask_id: str, ready: bool = False) -> tuple[dict, dict]:
    study = _get_study(ctx, study_id)
    mask = ctx.db.get_mask(study["id"], valid_uuid(mask_id, "Mask"))
    if mask is None:
        raise not_found("Mask")
    if ready and mask["state"] != "ready":
        raise AppError(409, "MASK_NOT_READY", "The mask is not ready yet.", {"state": mask["state"]})
    return study, mask


def _check_revision(study: dict, display_revision: Optional[int]) -> None:
    if display_revision is not None and display_revision != study["display_revision"]:
        raise AppError(409, "STALE_DISPLAY_REVISION", "The display revision is out of date; reload the case.",
                       {"current": study["display_revision"]})


def _failure(row: dict) -> dict | None:
    return {"code": row["failure_code"], "message": row["failure_message"]} if row.get("failure_code") else None


def mask_summary(m: dict, detail: bool = False) -> dict:
    d = {"id": m["id"], "state": m["state"], "source": m["source"], "synthetic": m["source"] == "synthetic_demo",
         "revision": m["revision"], "created_at": m["created_at"], "failure": _failure(m)}
    if m["state"] == "failed" and m["failure_code"] == "MASK_HEADER_OFFSET":
        d["failure"]["can_override"] = True
    if m["state"] == "ready":
        stats = dict(m["statistics"])
        if not detail:
            stats.pop("slice_load", None)
        d["statistics"] = stats
        if detail:
            d["candidates"] = m["candidates"]
            d["provenance"] = {k: m["provenance"].get(k) for k in ("mask_source", "synthetic", "model", "mask_sha256", "image_sha256", "notice")}
    return d


def study_summary(s: dict) -> dict:
    return {"id": s["id"], "alias": s["alias"], "patient_alias": s["patient_alias"], "timepoint_label": s["timepoint_label"],
            "scan_date": s["scan_date"], "sequence_label": s["sequence_label"], "state": s["state"],
            "is_demo": bool(s["is_demo"]), "created_at": s["created_at"], "failure": _failure(s),
            "selected_mask_id": s["selected_mask_id"]}


def study_detail(ctx: AppContext, s: dict) -> dict:
    d = study_summary(s)
    d["upload_label"] = s["upload_label"]
    d["masks"] = [mask_summary(m) for m in ctx.db.masks_of(s["id"])]
    if s["state"] == "ready":
        plan = pipeline.display_plan(s)
        orig = s["geometry"]["original"]
        d.update({
            "file_bytes": s["file_bytes"], "display_revision": s["display_revision"],
            "default_window": s["default_window"], "intensity_range": s["intensity_range"],
            "original": {k: orig[k] for k in ("shape", "dtype", "affine", "affine_source", "orientation", "spacing_mm", "header_spatial_unit")},
            "display": {**s["geometry"]["display"],
                        "planes": {p: geometry.plane_geometry(plan, p) for p in geometry.PLANES}},
        })
    return d


async def _save_upload(ctx: AppContext, up: UploadFile, dest_dir: Path, stem: str, request: Request) -> Path:
    """Stream to disk with a hard byte cap; the stored name is generated, never user-derived."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > ctx.settings.max_upload_bytes + 1_000_000:
        raise AppError(413, "SIZE_LIMIT_EXCEEDED", "Upload exceeds the configured size limit.",
                       {"limit_bytes": ctx.settings.max_upload_bytes})
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f"{stem}.upload.tmp"
    n, head = 0, b""
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = await up.read(CHUNK)
                if not chunk:
                    break
                if not head:
                    head = chunk[:2]
                n += len(chunk)
                if n > ctx.settings.max_upload_bytes:
                    raise AppError(413, "SIZE_LIMIT_EXCEEDED", "Upload exceeds the configured size limit.",
                                   {"limit_bytes": ctx.settings.max_upload_bytes})
                f.write(chunk)
        if n == 0:
            raise AppError(422, "EMPTY_FILE", "The uploaded file is empty.")
        final = dest_dir / (f"{stem}.nii.gz" if head == b"\x1f\x8b" else f"{stem}.nii")
        tmp.replace(final)
        return final
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _label(value: Optional[str], n: int = 80) -> Optional[str]:
    if value is None:
        return None
    v = "".join(ch for ch in value.strip() if ch.isprintable())[:n]
    return v or None


# ----------------------------------------------------------------- system
@router.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME, "version": __version__}


@router.get("/capabilities")
def capabilities(request: Request):
    ctx = ctx_of(request)
    s = ctx.settings
    adapter, reason = adapter_status(s)
    return {
        "app": APP_NAME, "version": __version__, "notice": "Research POC - not for diagnostic use",
        "formats": [".nii", ".nii.gz"],
        "limits": {"max_upload_bytes": s.max_upload_bytes, "max_decompressed_bytes": s.max_decompressed_bytes,
                   "max_voxels": s.max_voxels, "max_working_memory_bytes": s.max_working_memory_bytes},
        "inference": {"available": adapter is not None, "reason": reason, "device": s.device,
                      "models": [adapter.describe().to_json()] if adapter else []},
        "demo": {"available": True, "label": "Synthetic data - not a real scan"},
    }


# ----------------------------------------------------------------- studies
@router.post("/studies", status_code=202)
async def create_study(request: Request, image: UploadFile = File(...), alias: Optional[str] = Form(None),
                       sequence_label: str = Form("FLAIR"), patient_alias: Optional[str] = Form(None),
                       timepoint_label: Optional[str] = Form(None), scan_date: Optional[str] = Form(None)):
    ctx = ctx_of(request)
    sid = str(uuid.uuid4())
    d = ctx.paths.study_dir(sid)
    try:
        path = await _save_upload(ctx, image, d, "original", request)
    except BaseException:
        pipeline.remove_tree(d)
        raise
    ctx.db.insert_study(sid, _label(alias), _label(sequence_label) or "FLAIR", _label(image.filename, 100),
                        patient_alias=_label(patient_alias), timepoint_label=_label(timepoint_label),
                        scan_date=_label(scan_date, 10))
    ctx.executor.submit(pipeline.prepare_study, ctx, sid, path.name)
    return {"id": sid, "state": "preparing"}


@router.post("/demo", status_code=202)
def create_demo(request: Request):
    ctx = ctx_of(request)
    sid = str(uuid.uuid4())
    ctx.paths.study_dir(sid).mkdir(parents=True, exist_ok=True)
    ctx.db.insert_study(sid, "Synthetic demo", "FLAIR", None, is_demo=True)
    ctx.executor.submit(pipeline.prepare_demo, ctx, sid)
    return {"id": sid, "state": "preparing"}


@router.get("/studies")
def list_studies(request: Request, cursor: Optional[str] = None, limit: int = Query(25, ge=1, le=100)):
    ctx = ctx_of(request)
    args: tuple = ()
    where = ""
    if cursor:
        try:
            ts, _, cid = cursor.partition("|")
            valid_uuid(cid)
        except AppError:
            raise AppError(422, "INVALID_CURSOR", "Invalid cursor.")
        where, args = "WHERE (created_at < ? OR (created_at = ? AND id < ?))", (ts, ts, cid)
    rows = ctx.db.all(f"SELECT * FROM studies {where} ORDER BY created_at DESC, id DESC LIMIT ?", (*args, limit + 1))
    nxt = None
    if len(rows) > limit:
        rows = rows[:limit]
        nxt = f"{rows[-1]['created_at']}|{rows[-1]['id']}"
    return {"items": [study_summary(r) for r in rows], "next_cursor": nxt}


@router.get("/studies/{study_id}")
def get_study(study_id: str, request: Request):
    ctx = ctx_of(request)
    return study_detail(ctx, _get_study(ctx, study_id))


@router.delete("/studies/{study_id}", status_code=204)
def delete_study(study_id: str, request: Request):
    ctx = ctx_of(request)
    study = _get_study(ctx, study_id)
    if ctx.db.has_active_work(study["id"]):
        raise AppError(409, "STUDY_BUSY", "This case is being prepared or segmented; try again when it finishes.")
    with ctx.db.tx() as c:
        c.execute("DELETE FROM studies WHERE id=?", (study["id"],))
    pipeline.delete_study_files(ctx, study["id"])
    return Response(status_code=204)


def _finite(v: Optional[float], name: str) -> Optional[float]:
    if v is not None and not math.isfinite(v):
        raise AppError(422, "INVALID_WINDOW", f"{name} must be a finite number.")
    return v


@router.get("/studies/{study_id}/slices/{plane}/{index}/image")
def image_slice(study_id: str, plane: str, index: int, request: Request, center: Optional[float] = None,
                width: Optional[float] = None, display_revision: Optional[int] = None):
    ctx = ctx_of(request)
    plane = _plane(plane)
    study = _get_study(ctx, study_id, ready=True)
    _check_revision(study, display_revision)
    plan = pipeline.display_plan(study)
    geometry.check_index(plan, plane, index)
    w = study["default_window"]
    c = _finite(center, "center") if center is not None else w["center"]
    wd = _finite(width, "width") if width is not None else w["width"]
    key = (study["id"], "img", study["display_revision"], plane, index, round(c, 4), round(wd, 4))
    png = ctx.slice_cache.get(key)
    if png is None:
        vol = ctx.volumes.get(ctx.paths.study_dir(study["id"]) / "display.npy")
        png = render.gray_png(render.window_to_u8(geometry.extract_raster(vol, plane, index), c, wd))
        ctx.slice_cache.put(key, png)
    return Response(png, media_type="image/png")


# ----------------------------------------------------------------- masks
@router.post("/studies/{study_id}/masks", status_code=202)
async def upload_mask(study_id: str, request: Request, mask: UploadFile = File(...),
                      accept_subvoxel_offset: bool = Form(False)):
    ctx = ctx_of(request)
    study = _get_study(ctx, study_id, ready=True)
    mid = str(uuid.uuid4())
    mdir = ctx.paths.mask_dir(study["id"], mid)
    try:
        path = await _save_upload(ctx, mask, mdir, "source_upload", request)
    except BaseException:
        pipeline.remove_tree(mdir)
        raise
    ctx.db.insert_mask(mid, study["id"], "uploaded")
    ctx.executor.submit(pipeline.prepare_mask, ctx, study["id"], mid, path, "uploaded",
                        accept_subvoxel_offset=accept_subvoxel_offset)
    return {"id": mid, "state": "preparing"}


@router.get("/studies/{study_id}/masks/{mask_id}")
def get_mask(study_id: str, mask_id: str, request: Request):
    _, mask = _get_mask(ctx_of(request), study_id, mask_id)
    return mask_summary(mask, detail=True)


@router.get("/studies/{study_id}/masks/{mask_id}/slices/{plane}/{index}")
def mask_slice(study_id: str, mask_id: str, plane: str, index: int, request: Request, display_revision: Optional[int] = None):
    ctx = ctx_of(request)
    plane = _plane(plane)
    study, mask = _get_mask(ctx, study_id, mask_id, ready=True)
    _check_revision(study, display_revision)
    plan = pipeline.display_plan(study)
    geometry.check_index(plan, plane, index)
    key = (study["id"], "mask", mask["id"], mask["revision"], study["display_revision"], plane, index)
    png = ctx.slice_cache.get(key)
    if png is None:
        vol = ctx.volumes.get(ctx.paths.mask_dir(study["id"], mask["id"]) / "display_mask.npy")
        png = render.mask_png(geometry.extract_raster(vol, plane, index))
        ctx.slice_cache.put(key, png)
    return Response(png, media_type="image/png")


@router.get("/studies/{study_id}/masks/{mask_id}/slices/{plane}/{index}/measurements")
def mask_measurements(study_id: str, mask_id: str, plane: str, index: int, request: Request, display_revision: Optional[int] = None):
    ctx = ctx_of(request)
    plane = _plane(plane)
    study, mask = _get_mask(ctx, study_id, mask_id, ready=True)
    _check_revision(study, display_revision)
    geometry.check_index(pipeline.display_plan(study), plane, index)
    return JSONResponse(pipeline.measure_slice(ctx, study, mask, plane, index))


@router.get("/studies/{study_id}/masks/{mask_id}/download")
def download_mask(study_id: str, mask_id: str, request: Request):
    ctx = ctx_of(request)
    study, mask = _get_mask(ctx, study_id, mask_id, ready=True)
    path = ctx.paths.mask_dir(study["id"], mask["id"]) / "mask_original.nii.gz"
    prefix = "SYNTHETIC_DEMO_mask" if mask["source"] == "synthetic_demo" else "lesion_mask"
    return FileResponse(path, media_type="application/gzip", filename=f"{prefix}_{mask['id'][:8]}.nii.gz")


@router.get("/studies/{study_id}/masks/{mask_id}/provenance")
def mask_provenance(study_id: str, mask_id: str, request: Request):
    ctx = ctx_of(request)
    study, mask = _get_mask(ctx, study_id, mask_id, ready=True)
    return JSONResponse(pipeline.build_provenance(ctx, study, mask),
                        headers={"Content-Disposition": f'attachment; filename="provenance_{mask["id"][:8]}.json"'})


# ----------------------------------------------------------------- jobs
class JobRequest(BaseModel):
    model_id: str


def job_payload(j: dict) -> dict:
    return {"id": j["id"], "study_id": j["study_id"], "model_id": j["model_id"], "state": j["state"],
            "stage": j["stage"], "progress": None,
            "error": {"code": j["error_code"], "message": j["error_message"]} if j["error_code"] else None,
            "output_mask_id": j["output_mask_id"], "created_at": j["created_at"], "started_at": j["started_at"],
            "finished_at": j["finished_at"], "polling_url": f"/api/v1/jobs/{j['id']}"}


@router.post("/studies/{study_id}/jobs", status_code=202)
def create_job(study_id: str, body: JobRequest, request: Request):
    ctx = ctx_of(request)
    study = _get_study(ctx, study_id, ready=True)
    adapter, reason = adapter_status(ctx.settings)
    if adapter is None:
        raise AppError(503, "INFERENCE_UNAVAILABLE", reason or "Segmentation is unavailable.")
    if body.model_id != adapter.describe().model_id:
        raise AppError(422, "UNKNOWN_MODEL", "That model is not configured.")
    jid = str(uuid.uuid4())
    try:
        ctx.db.insert_job(jid, study["id"], body.model_id)
    except sqlite3.IntegrityError:
        raise AppError(409, "JOB_ALREADY_ACTIVE", "A segmentation job is already queued or running for this case.")
    return job_payload(ctx.db.get_job(jid))


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    j = ctx_of(request).db.get_job(valid_uuid(job_id, "Job"))
    if j is None:
        raise not_found("Job")
    return job_payload(j)
