"""Connected components, exact Feret diameters (2D in-plane and 3D), lesion statistics."""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError

from .geometry import DisplayPlan, raster_to_ras

STRUCT_26 = np.ones((3, 3, 3), dtype=bool)
STRUCT_8 = np.ones((3, 3), dtype=bool)
METHOD = "pixel_cell_maximum_2d_feret"
METHOD_3D = "voxel_cell_maximum_3d_feret"
METHOD_VERSION = "1"


def label_native(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """26-connected labelling; IDs 1..N ordered by first occupied voxel in [x,y,z] lexicographic order."""
    lab, n = ndimage.label(mask > 0, structure=STRUCT_26, output=np.int32)
    if n == 0:
        return lab, 0
    flat = lab.ravel()
    occupied = np.flatnonzero(flat)
    ids, first = np.unique(flat[occupied], return_index=True)
    order = np.argsort(first, kind="stable")
    remap = np.zeros(n + 1, dtype=np.int32)
    remap[ids[order]] = np.arange(1, n + 1, dtype=np.int32)
    return remap[lab], int(n)


def _farthest_pair(pts: np.ndarray, chunk: int = 1500) -> tuple[float, int, int]:
    best, bi, bj = -1.0, 0, 0
    for s in range(0, len(pts), chunk):
        d2 = ((pts[s : s + chunk, None, :] - pts[None, :, :]) ** 2).sum(-1)
        k = int(np.argmax(d2))
        i, j = divmod(k, d2.shape[1])
        if d2[i, j] > best:
            best, bi, bj = float(d2[i, j]), s + i, j
    return float(np.sqrt(best)), bi, bj


def _hull_points(pts: np.ndarray, phys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    try:
        v = ConvexHull(phys).vertices
        return pts[v], phys[v]
    except QhullError:
        return pts, phys


def feret_2d(rows: np.ndarray, cols: np.ndarray, sc: float, sr: float):
    """Exact maximum Feret diameter of a union of pixel cells.

    The hull of the union depends only on the extreme cell corners of each pixel row, so only those
    4 corners per row are used (exact, bounded by footprint height). Returns (mm, (u0,v0), (u1,v1))."""
    order = np.argsort(rows, kind="stable")
    r, c = rows[order], cols[order]
    uniq, start = np.unique(r, return_index=True)
    cmin = np.minimum.reduceat(c, start)
    cmax = np.maximum.reduceat(c, start)
    pts = np.array([(u, v) for rr, lo, hi in zip(uniq, cmin, cmax) for u in (lo - 0.5, hi + 0.5) for v in (rr - 0.5, rr + 0.5)], float)
    pts = np.unique(pts, axis=0)
    hp, hv = _hull_points(pts, pts * np.array([sc, sr]))
    d, i, j = _farthest_pair(hv)
    return d, tuple(hp[i]), tuple(hp[j])


def feret_3d(vox: np.ndarray, affine: np.ndarray):
    """Exact maximum 3D Feret diameter (mm) of a union of voxel cells.

    vox: (n,3) integer voxel indices. The hull depends only on the extreme cell corners along each
    (i,j) column. Returns (mm, p0_mm, p1_mm)."""
    key = vox[:, 0].astype(np.int64) * 1_000_003 + vox[:, 1]
    order = np.argsort(key, kind="stable")
    v, k = vox[order], key[order]
    _, start = np.unique(k, return_index=True)
    kmin = np.minimum.reduceat(v[:, 2], start)
    kmax = np.maximum.reduceat(v[:, 2], start)
    ij = v[start, :2]
    corners = []
    for di in (-0.5, 0.5):
        for dj in (-0.5, 0.5):
            corners.append(np.column_stack([ij[:, 0] + di, ij[:, 1] + dj, kmin - 0.5]))
            corners.append(np.column_stack([ij[:, 0] + di, ij[:, 1] + dj, kmax + 0.5]))
    pts = np.unique(np.vstack(corners), axis=0)
    mm = pts @ affine[:3, :3].T + affine[:3, 3]
    _, hv = _hull_points(pts, mm)
    d, i, j = _farthest_pair(hv)
    return d, hv[i].tolist(), hv[j].tolist()


def lesion_3d_stats(labels: np.ndarray, n: int, affine: np.ndarray) -> list[dict]:
    """Per-candidate native-grid volume, equivalent sphere diameter and exact 3D Feret diameter."""
    out: list[dict] = []
    if n == 0:
        return out
    vox_mm3 = abs(float(np.linalg.det(affine[:3, :3])))
    objs = ndimage.find_objects(labels)
    for cid in range(1, n + 1):
        sl = objs[cid - 1]
        idx = np.argwhere(labels[sl] == cid) + np.array([s.start for s in sl])
        vol = idx.shape[0] * vox_mm3
        d3, p0, p1 = feret_3d(idx, affine)
        out.append({
            "candidate_id": cid, "voxel_count": int(idx.shape[0]), "volume_mm3": vol,
            "volume_ml": vol / 1000.0,
            "equivalent_sphere_diameter_mm": float((6.0 * vol / np.pi) ** (1.0 / 3.0)),
            "max_3d_diameter_mm": d3,
        })
    return out


def slice_footprints(raster: np.ndarray, plan: DisplayPlan, plane: str, index: int, spacing: tuple[float, float]) -> list[dict]:
    """Per-candidate 8-connected footprints on one display-slice raster of candidate IDs."""
    sc, sr = spacing
    if not raster.any():
        return []
    objs = ndimage.find_objects(raster)
    out: list[dict] = []
    for cid in np.unique(raster[raster > 0]):
        sl = objs[int(cid) - 1]
        lab, n = ndimage.label(raster[sl] == cid, structure=STRUCT_8)
        for k in range(1, n + 1):
            rr, cc = np.nonzero(lab == k)
            rr, cc = rr + sl[0].start, cc + sl[1].start
            d, p0, p1 = feret_2d(rr, cc, sc, sr)
            out.append({
                "candidate_id": int(cid), "footprint_id": k, "diameter_mm": d, "pixel_count": int(rr.size),
                "endpoints_raster": [[float(p0[0]), float(p0[1])], [float(p1[0]), float(p1[1])]],
                "endpoints_ras_mm": [raster_to_ras(plan, plane, index, *p0), raster_to_ras(plan, plane, index, *p1)],
                "label_anchor_raster": [float((p0[0] + p1[0]) / 2.0), float((p0[1] + p1[1]) / 2.0)],
            })
    return out
