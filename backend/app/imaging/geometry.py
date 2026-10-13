"""Display-grid construction and the plane/raster contract.

Display grid: array D[x, y, z], axes RAS+ (index increases toward patient Right, Anterior,
Superior), millimetres, axis-aligned, affine = diag(sx, sy, sz) + origin.

Raster contract (radiological): for a 2D slice s[a, b] taken from D,
    raster[row, col] = s[na-1-col, nb-1-row]   i.e.  s[::-1, ::-1].T
Axial    s=D[:,:,k]  axes (x,y): left = patient Right, top = Anterior
Coronal  s=D[:,j,:]  axes (x,z): left = patient Right, top = Superior
Sagittal s=D[i,:,:]  axes (y,z): left = Anterior,      top = Superior
Pixel centres are integer raster coordinates; cell edges are at half-integers.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from nibabel.orientations import apply_orientation, inv_ornt_aff, io_orientation
from scipy import ndimage

from ..config import Settings
from ..errors import AppError
from .nifti import voxel_corners

PLANES = ("axial", "coronal", "sagittal")
PLANE_AXES = {"axial": (0, 1, 2), "coronal": (0, 2, 1), "sagittal": (1, 2, 0)}   # (horizontal, vertical, slice) data axes
EDGE_LABELS = {
    "axial": {"left": "R", "right": "L", "top": "A", "bottom": "P"},
    "coronal": {"left": "R", "right": "L", "top": "S", "bottom": "I"},
    "sagittal": {"left": "A", "right": "P", "top": "S", "bottom": "I"},
}
AXIS_NAME = "xyz"
AXIS_TOL = 1e-4


@dataclass
class DisplayPlan:
    shape: tuple[int, int, int]
    affine: np.ndarray                 # diag(spacing) + origin, RAS+
    spacing: tuple[float, float, float]
    resampled: bool
    method: str
    ornt: np.ndarray | None = None
    orig_shape: tuple[int, int, int] | None = None
    orig_affine: np.ndarray | None = None

    def to_json(self) -> dict:
        return {
            "shape": list(self.shape), "affine": self.affine.tolist(), "spacing_mm": list(self.spacing),
            "resampled": self.resampled, "method": self.method,
            "image_interpolation": "linear" if self.resampled else "none",
            "mask_interpolation": "nearest" if self.resampled else "none",
            "axis_order": "display array is [x, y, z] in RAS+: x->Right, y->Anterior, z->Superior",
        }


def _is_axis_aligned(aff: np.ndarray) -> bool:
    cols = np.linalg.norm(aff[:3, :3], axis=0)
    if (cols == 0).any():
        return False
    big = (np.abs(aff[:3, :3]) / cols[None, :]) > AXIS_TOL
    return bool((big.sum(axis=0) == 1).all() and (big.sum(axis=1) == 1).all())


def make_display_plan(shape, affine_mm: np.ndarray, settings: Settings | None = None) -> DisplayPlan:
    shape = tuple(int(s) for s in shape)
    affine_mm = np.asarray(affine_mm, dtype=float)
    if _is_axis_aligned(affine_mm):
        ornt = io_orientation(affine_mm)
        new_aff = affine_mm @ inv_ornt_aff(ornt, shape)
        out = [0, 0, 0]
        for i, (a, _) in enumerate(ornt):          # input axis i becomes output axis ornt[i, 0]
            out[int(a)] = shape[i]
        new_shape = tuple(out)
        spacing = tuple(float(x) for x in np.linalg.norm(new_aff[:3, :3], axis=0))
        aff = np.diag([*spacing, 1.0])
        aff[:3, 3] = new_aff[:3, 3]
        return DisplayPlan(new_shape, aff, spacing, False, "orientation_permutation", ornt, shape, affine_mm)

    corners = voxel_corners(shape, affine_mm)
    lo, hi = corners.min(axis=0), corners.max(axis=0)
    s = float(np.linalg.norm(affine_mm[:3, :3], axis=0).min())
    dims = tuple(int(np.ceil((hi[i] - lo[i]) / s - 1e-6)) + 1 for i in range(3))
    n_vox = int(np.prod(dims, dtype=np.int64))
    if settings is not None and (n_vox > settings.max_voxels or n_vox * 12 > settings.max_working_memory_bytes):
        raise AppError(413, "SIZE_LIMIT_EXCEEDED",
                       "Resampling this oblique volume to an isotropic display grid would exceed the configured limits.",
                       {"display_voxels": n_vox, "limit": settings.max_voxels})
    aff = np.diag([s, s, s, 1.0])
    aff[:3, 3] = lo
    return DisplayPlan(dims, aff, (s, s, s), True, "linear_resample_isotropic", None, shape, affine_mm)


def apply_plan(plan: DisplayPlan, vol: np.ndarray, order: int = 1, dtype=np.float32) -> np.ndarray:
    """Map an original-grid volume onto the display grid. Use order=0 for masks/labels."""
    if not plan.resampled:
        return np.ascontiguousarray(apply_orientation(vol, plan.ornt)).astype(dtype, copy=False)
    inv = np.linalg.inv(plan.orig_affine)
    m = inv[:3, :3] @ plan.affine[:3, :3]
    off = inv[:3, :3] @ plan.affine[:3, 3] + inv[:3, 3]
    out = np.zeros(plan.shape, dtype=dtype)
    ndimage.affine_transform(vol, m, offset=off, output_shape=plan.shape, output=out, order=order,
                             mode="constant", cval=0, prefilter=False)
    return out


def plane_geometry(plan: DisplayPlan, plane: str) -> dict:
    a, b, c = PLANE_AXES[plane]
    sp, origin, n = plan.spacing, plan.affine[:3, 3], plan.shape
    return {
        "width": int(n[a]), "height": int(n[b]),
        "pixel_spacing_mm": [float(sp[a]), float(sp[b])],       # [column spacing, row spacing]
        "slice_count": int(n[c]), "slice_axis": AXIS_NAME[c],
        "slice_origin_mm": float(origin[c]), "slice_step_mm": float(sp[c]),
        "edge_labels": EDGE_LABELS[plane],
    }


def check_index(plan: DisplayPlan, plane: str, index: int) -> None:
    n = plan.shape[PLANE_AXES[plane][2]]
    if not (0 <= index < n):
        raise AppError(422, "INVALID_SLICE_INDEX", f"Slice index must be in [0, {n - 1}].", {"max_index": n - 1})


def extract_raster(vol: np.ndarray, plane: str, index: int) -> np.ndarray:
    """2D screen raster (rows x cols) following the module contract."""
    if plane == "axial":
        s = vol[:, :, index]
    elif plane == "coronal":
        s = vol[:, index, :]
    else:
        s = vol[index, :, :]
    return np.ascontiguousarray(s[::-1, ::-1].T)


def raster_to_ras(plan: DisplayPlan, plane: str, index: int, u: float, v: float) -> list[float]:
    """Raster coordinate (u=column, v=row; pixel centres integer) -> RAS mm."""
    a, b, c = PLANE_AXES[plane]
    o, sp, n = plan.affine[:3, 3], plan.spacing, plan.shape
    p = [0.0, 0.0, 0.0]
    p[a] = float(o[a] + (n[a] - 1 - u) * sp[a])
    p[b] = float(o[b] + (n[b] - 1 - v) * sp[b])
    p[c] = float(o[c] + index * sp[c])
    return p


def display_index_to_ras(plan: DisplayPlan, idx) -> list[float]:
    return [float(x) for x in plan.affine[:3, 3] + np.asarray(idx, float) * np.asarray(plan.spacing)]
