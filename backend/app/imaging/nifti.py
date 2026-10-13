"""NIfTI validation. Content is validated, never just the extension."""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

from ..config import Settings
from ..errors import AppError

UNIT_TO_MM = {"meter": 1000.0, "mm": 1.0, "micron": 0.001}
AMBIGUITY_TOL_MM = 0.1


@dataclass
class VolumeInfo:
    shape: tuple[int, int, int]
    dtype: str
    affine_mm: np.ndarray            # 4x4, millimetres
    affine_file: np.ndarray          # 4x4, in the file's own spatial unit
    unit_factor: float               # file unit -> mm
    spatial_unit: str
    affine_source: str               # sform | qform
    sform_code: int
    qform_code: int
    orientation: str                 # e.g. "RAS"
    spacing_mm: tuple[float, float, float]
    decompressed_bytes: int

    def to_json(self) -> dict:
        return {
            "shape": list(self.shape), "dtype": self.dtype,
            "affine": self.affine_mm.tolist(), "affine_file_units": self.affine_file.tolist(),
            "unit_factor_to_mm": self.unit_factor, "header_spatial_unit": self.spatial_unit,
            "affine_source": self.affine_source, "sform_code": self.sform_code, "qform_code": self.qform_code,
            "orientation": self.orientation, "spacing_mm": list(self.spacing_mm),
        }


def voxel_corners(shape, affine: np.ndarray) -> np.ndarray:
    """World (mm) positions of the 8 voxel-centre corners of a volume."""
    n = np.asarray(shape, dtype=float) - 1
    idx = np.array([[i, j, k, 1.0] for i in (0, n[0]) for j in (0, n[1]) for k in (0, n[2])])
    return (idx @ np.asarray(affine, float).T)[:, :3]


def max_corner_distance(shape_a, aff_a, shape_b, aff_b) -> float:
    return float(np.linalg.norm(voxel_corners(shape_a, aff_a) - voxel_corners(shape_b, aff_b), axis=1).max())


def _limit_error(msg: str, **d) -> AppError:
    return AppError(413, "SIZE_LIMIT_EXCEEDED", msg, d)


def _scan_container(path: Path, settings: Settings) -> tuple[bytes, int]:
    """Check magic bytes, stream-decompress with a hard bound, verify gzip integrity.

    Returns (first 1 KiB of decompressed content, decompressed size)."""
    name = path.name.lower()
    with open(path, "rb") as f:
        head = f.read(4)
        f.seek(0)
        if head[:4] == b"PK\x03\x04":
            raise AppError(415, "ARCHIVE_NOT_SUPPORTED", "ZIP archives are not supported. Upload one .nii or .nii.gz file.")
        if head[:2] == b"\x1f\x8b":
            d = zlib.decompressobj(31)
            total, header = 0, bytearray()
            try:
                while not d.eof:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    buf = chunk
                    while buf and not d.eof:
                        out = d.decompress(buf, 1 << 22)
                        total += len(out)
                        if len(header) < 1024:
                            header.extend(out[: 1024 - len(header)])
                        if total > settings.max_decompressed_bytes:
                            raise _limit_error("Decompressed file exceeds the configured size limit.",
                                               limit_bytes=settings.max_decompressed_bytes)
                        buf = d.unconsumed_tail
            except zlib.error:
                raise AppError(422, "CORRUPT_FILE", "The file is corrupt or not valid gzip data.")
            if not d.eof:
                raise AppError(422, "CORRUPT_FILE", "The gzip data is truncated.")
            content_head = bytes(header)
        else:
            size = path.stat().st_size
            if size > settings.max_decompressed_bytes:
                raise _limit_error("File exceeds the configured size limit.", limit_bytes=settings.max_decompressed_bytes)
            content_head = f.read(1024)
            total = size
    if len(content_head) >= 132 and content_head[128:132] == b"DICM":
        raise AppError(415, "DICOM_NOT_SUPPORTED", "DICOM is not supported in this POC. Convert the series to a single NIfTI file.")
    is_n1 = len(content_head) >= 348 and content_head[344:347] == b"n+1"
    is_n2 = len(content_head) >= 12 and content_head[4:7] == b"n+2"
    if not (is_n1 or is_n2):
        raise AppError(415, "NOT_NIFTI", "The file is not a single-file NIfTI image (.nii or .nii.gz).")
    if not (name.endswith(".nii") or name.endswith(".nii.gz")):
        raise AppError(415, "UNSUPPORTED_FORMAT", "Only .nii and .nii.gz files are supported.")
    return content_head, total


def inspect_nifti(path: Path, settings: Settings) -> VolumeInfo:
    _, total = _scan_container(path, settings)
    try:
        img = nib.load(str(path))
        hdr = img.header
        shape = tuple(int(s) for s in img.shape)
        dtype = hdr.get_data_dtype()
    except Exception:
        raise AppError(422, "INVALID_NIFTI", "The NIfTI header could not be parsed.")

    if len(shape) == 4:
        raise AppError(422, "UNSUPPORTED_4D", "4D time series are not supported. Select one 3D volume and upload it as a 3D NIfTI.")
    if len(shape) != 3:
        raise AppError(422, "UNSUPPORTED_DIMENSIONS", f"Expected a 3D volume, found {len(shape)} dimensions.")
    if any(s <= 0 for s in shape):
        raise AppError(422, "INVALID_DIMENSIONS", "Volume dimensions must be positive.")
    if dtype.kind == "c":
        raise AppError(422, "COMPLEX_NOT_SUPPORTED", "Complex-valued images are not supported.")
    if dtype.kind not in "iuf":
        raise AppError(422, "VECTOR_NOT_SUPPORTED", "Only real scalar-valued volumes are supported (no RGB/vector images).")

    n_vox = int(np.prod(shape, dtype=np.int64))
    if n_vox > settings.max_voxels:
        raise _limit_error("Volume has more voxels than the configured limit.", voxels=n_vox, limit=settings.max_voxels)
    if n_vox * 12 > settings.max_working_memory_bytes:
        raise _limit_error("Estimated working memory exceeds the configured limit.", limit_bytes=settings.max_working_memory_bytes)
    if total < int(hdr.get_data_offset()) + n_vox * dtype.itemsize:
        raise AppError(422, "CORRUPT_FILE", "The image payload is truncated.")

    unit = hdr.get_xyzt_units()[0]
    if unit not in UNIT_TO_MM:
        raise AppError(422, "INVALID_UNITS",
                       "The header's spatial unit is unknown. Set it to mm, meter or micron and upload again.",
                       {"header_unit": unit})
    factor = UNIT_TO_MM[unit]
    scale = np.diag([factor, factor, factor, 1.0])

    sform, s_code = hdr.get_sform(coded=True)
    qform, q_code = hdr.get_qform(coded=True)
    s_code = int(s_code) if sform is not None else 0
    q_code = int(q_code) if qform is not None else 0
    if s_code == 0 and q_code == 0:
        raise AppError(422, "NO_SPATIAL_TRANSFORM", "Neither sform nor qform is coded in the header; the image has no usable spatial transform.")

    def _check(a: np.ndarray, which: str) -> None:
        if a.shape != (4, 4) or not np.isfinite(a).all():
            raise AppError(422, "INVALID_AFFINE", f"The {which} affine contains non-finite values.")
        sing = np.linalg.svd(a[:3, :3], compute_uv=False)
        if sing.min() <= 1e-9 * max(sing.max(), 1e-12) or sing.min() <= 0:
            raise AppError(422, "INVALID_AFFINE", f"The {which} affine is not invertible.")

    if s_code:
        _check(np.asarray(sform, dtype=float), "sform")
    if q_code:
        _check(np.asarray(qform, dtype=float), "qform")
    if s_code and q_code:
        d = max_corner_distance(shape, scale @ sform, shape, scale @ qform)
        if d > AMBIGUITY_TOL_MM:
            raise AppError(422, "AMBIGUOUS_AFFINE",
                           "sform and qform are both coded and disagree; correct the header so they match.",
                           {"max_corner_difference_mm": round(d, 3)})
    aff_file, source = (np.asarray(sform, dtype=float), "sform") if s_code else (np.asarray(qform, dtype=float), "qform")
    aff_mm = scale @ aff_file
    spacing = tuple(float(x) for x in np.linalg.norm(aff_mm[:3, :3], axis=0))
    return VolumeInfo(
        shape=shape, dtype=str(dtype), affine_mm=aff_mm, affine_file=aff_file, unit_factor=factor,
        spatial_unit=unit, affine_source=source, sform_code=s_code, qform_code=q_code,
        orientation="".join(nib.aff2axcodes(aff_mm)), spacing_mm=spacing, decompressed_bytes=total,
    )


def load_data(path: Path) -> np.ndarray:
    """Load intensities as float32 with NIfTI scaling applied; rejects NaN/Inf."""
    try:
        data = np.asarray(nib.load(str(path)).get_fdata(dtype=np.float32))
    except Exception:
        raise AppError(422, "CORRUPT_FILE", "The image payload could not be read.")
    if not np.isfinite(data).all():
        raise AppError(422, "NONFINITE_DATA", "The image contains NaN or infinite values.")
    return data


def subvoxel_translation(mri_affine, mask_affine, tol: float = 1e-3):
    """If the mask affine differs from the MRI affine only by a translation of at most half a voxel along
    each axis, return that offset in voxel units; otherwise None."""
    a, b = np.asarray(mri_affine, float), np.asarray(mask_affine, float)
    if not np.allclose(a[:3, :3], b[:3, :3], rtol=1e-4, atol=1e-6):
        return None
    off = np.linalg.inv(a[:3, :3]) @ (b[:3, 3] - a[:3, 3])
    return off if bool((np.abs(off) <= 0.5 + tol).all()) else None
