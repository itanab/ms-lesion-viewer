import io
import time
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.imaging import phantom
from app.main import create_app


def nifti_bytes(arr, affine, dtype=np.float32, unit="mm", gz=True, tweak=None) -> bytes:
    """Serialise a NIfTI. `tweak(header, affine)` edits the header bytes AFTER nibabel's own save fixes."""
    import gzip as _gz
    import tempfile
    img = nib.Nifti1Image(np.asarray(arr).astype(dtype), affine)
    img.header.set_xyzt_units(unit)
    f = Path(tempfile.mkdtemp()) / "x.nii"
    nib.save(img, str(f))
    raw = bytearray(f.read_bytes())
    if tweak:
        hdr = nib.Nifti1Header.from_fileobj(io.BytesIO(bytes(raw[:348])), check=False)
        tweak(hdr, affine)
        buf = io.BytesIO()
        hdr.write_to(buf)
        raw[:348] = buf.getvalue()[:348]
    return _gz.compress(bytes(raw)) if gz else bytes(raw)


def make_client(tmp_path, **kw):
    s = Settings(data_root=tmp_path / "data", run_worker=kw.pop("run_worker", False), **kw)
    return TestClient(create_app(s))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


def wait(client, url, done, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(url).json()
        if done(r):
            return r
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting on {url}: {r}")


def upload_study(client, data: bytes, name="scan.nii.gz", **form):
    r = client.post("/api/v1/studies", files={"image": (name, data)}, data=form)
    assert r.status_code == 202, r.text
    sid = r.json()["id"]
    return sid, wait(client, f"/api/v1/studies/{sid}", lambda s: s["state"] != "preparing")


def upload_mask(client, sid, data: bytes, name="mask.nii.gz"):
    r = client.post(f"/api/v1/studies/{sid}/masks", files={"mask": (name, data)})
    assert r.status_code == 202, r.text
    mid = r.json()["id"]
    return mid, wait(client, f"/api/v1/studies/{sid}/masks/{mid}", lambda m: m["state"] != "preparing")


SHAPE = (40, 48, 30)
SPACING = (1.0, 1.5, 2.5)


def phantom_case(direction=None, shape=SHAPE, spacing=SPACING):
    aff = phantom.centred_affine(shape, spacing, direction)
    vol, mask = phantom.make_phantom(shape, aff)
    return aff, vol, mask


@pytest.fixture
def ready_study(client):
    aff, vol, mask = phantom_case()
    sid, st = upload_study(client, nifti_bytes(vol, aff))
    assert st["state"] == "ready", st
    return client, sid, st, aff, vol, mask
