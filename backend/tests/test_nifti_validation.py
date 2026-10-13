import gzip
import io

import nibabel as nib
import numpy as np
import pytest

from conftest import make_client, nifti_bytes, upload_study
from app.imaging import phantom


def aff(shape=(10, 10, 6), sp=(1, 1, 2)):
    return phantom.centred_affine(shape, sp)


def good(shape=(10, 10, 6)):
    return np.random.default_rng(1).random(shape).astype(np.float32) * 100 + 1


def set_codes(s, q):
    def f(hdr, affine):
        hdr.set_sform(affine, code=s)
        hdr.set_qform(affine, code=q)
    return f


def shifted_sform(hdr, affine):
    a = affine.copy(); a[0, 3] += 5
    hdr.set_sform(a, code=2); hdr.set_qform(affine, code=2)


def zero_codes(hdr, affine):
    hdr["sform_code"] = 0; hdr["qform_code"] = 0


def singular(hdr, affine):
    a = affine.copy(); a[:3, 0] = 0
    hdr.set_sform(a, code=2); hdr["qform_code"] = 0


def bad(code, make):
    return pytest.param(code, make, id=code)


nan_arr = good(); nan_arr[0, 0, 0] = np.nan

CASES = [
    bad("CORRUPT_FILE", lambda: nifti_bytes(good(), aff())[:200]),
    bad("CORRUPT_FILE", lambda: nifti_bytes(good((30, 30, 20)), aff((30, 30, 20)))[:-300]),
    bad("NOT_NIFTI", lambda: b"this is not an image" * 100),
    bad("NOT_NIFTI", lambda: gzip.compress(b"hello" * 500)),
    bad("DICOM_NOT_SUPPORTED", lambda: b"\0" * 128 + b"DICM" + b"\0" * 500),
    bad("ARCHIVE_NOT_SUPPORTED", lambda: b"PK\x03\x04" + b"\0" * 200),
    bad("UNSUPPORTED_4D", lambda: nifti_bytes(np.zeros((6, 6, 4, 3)), np.eye(4))),
    bad("NONFINITE_DATA", lambda: nifti_bytes(nan_arr, aff())),
    bad("COMPLEX_NOT_SUPPORTED", lambda: nifti_bytes(good().astype(np.complex64), aff(), dtype=np.complex64)),
    bad("NO_SPATIAL_TRANSFORM", lambda: nifti_bytes(good(), aff(), tweak=zero_codes)),
    bad("INVALID_AFFINE", lambda: nifti_bytes(good(), aff(), tweak=singular)),
    bad("AMBIGUOUS_AFFINE", lambda: nifti_bytes(good(), aff(), tweak=shifted_sform)),
    bad("INVALID_UNITS", lambda: nifti_bytes(good(), aff(), unit="unknown")),
]


@pytest.mark.parametrize("code,make", CASES)
def test_invalid_files_fail_safely(client, code, make):
    sid, st = upload_study(client, make())
    assert st["state"] == "failed"
    assert st["failure"]["code"] == code, st
    assert "/" not in st["failure"]["message"] or "mm" in st["failure"]["message"]


def test_oversized_upload_is_rejected_with_413_and_cleaned(tmp_path):
    with make_client(tmp_path, max_upload_bytes=2000) as c:
        r = c.post("/api/v1/studies", files={"image": ("a.nii.gz", nifti_bytes(good((20, 20, 20)), aff((20, 20, 20))))})
        assert r.status_code == 413 and r.json()["error"]["code"] == "SIZE_LIMIT_EXCEEDED"
        assert list((tmp_path / "data" / "studies").iterdir()) == []


def test_voxel_limit_and_decompression_bomb(tmp_path):
    with make_client(tmp_path, max_voxels=100) as c:
        _, st = upload_study(c, nifti_bytes(good(), aff()))
        assert st["failure"]["code"] == "SIZE_LIMIT_EXCEEDED"
    with make_client(tmp_path / "b", max_decompressed_bytes=1000) as c:
        _, st = upload_study(c, nifti_bytes(good((20, 20, 20)), aff((20, 20, 20))))
        assert st["failure"]["code"] == "SIZE_LIMIT_EXCEEDED"


@pytest.mark.parametrize("unit,factor", [("meter", 1000.0), ("micron", 0.001), ("mm", 1.0)])
def test_units_are_converted_to_mm(client, unit, factor):
    a = aff((10, 10, 6), (2, 2, 2))
    a = a.copy(); a[:3, :3] /= factor; a[:3, 3] /= factor      # file affine expressed in `unit`
    sid, st = upload_study(client, nifti_bytes(good(), a, unit=unit))
    assert st["state"] == "ready", st
    assert st["original"]["spacing_mm"] == pytest.approx([2, 2, 2])


def test_qform_only_is_accepted_and_sform_preferred(client):
    sid, st = upload_study(client, nifti_bytes(good(), aff(), tweak=set_codes(0, 1)))
    assert st["state"] == "ready" and st["original"]["affine_source"] == "qform"
    sid, st = upload_study(client, nifti_bytes(good(), aff(), tweak=set_codes(2, 2)))
    assert st["original"]["affine_source"] == "sform"


def test_plain_nii_and_scaling_honoured(client):
    arr = np.arange(10 * 10 * 6).reshape(10, 10, 6) % 200
    def scale(hdr, affine):
        hdr.set_slope_inter(2.0, 10.0)
    data = nifti_bytes(arr, aff(), dtype=np.int16, gz=False, tweak=scale)
    sid, st = upload_study(client, data, name="x.nii")
    assert st["state"] == "ready"
    assert st["intensity_range"]["max"] == 2.0 * 199 + 10 and st["intensity_range"]["min"] == 10.0


def test_constant_image_default_window(client):
    sid, st = upload_study(client, nifti_bytes(np.full((8, 8, 4), 7.0), aff((8, 8, 4))))
    assert st["default_window"]["width"] == 1.0 and st["default_window"]["center"] == 7.0
