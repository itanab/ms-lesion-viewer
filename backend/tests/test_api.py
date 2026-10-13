import io
import uuid

import nibabel as nib
import numpy as np
import pytest
from PIL import Image

from conftest import make_client, nifti_bytes, phantom_case, upload_mask, upload_study, wait
from app.imaging import phantom


def png(r):
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    return np.array(Image.open(io.BytesIO(r.content)))


def test_upload_opens_and_persists_across_restart(tmp_path):
    aff, vol, mask = phantom_case()
    with make_client(tmp_path) as c:
        sid, st = upload_study(c, nifti_bytes(vol, aff), alias="case a", patient_alias="P1", timepoint_label="baseline", scan_date="2026-01-02")
        assert st["state"] == "ready" and st["alias"] == "case a" and st["patient_alias"] == "P1" and st["scan_date"] == "2026-01-02"
        planes = st["display"]["planes"]
        assert planes["axial"]["slice_count"] == vol.shape[2]
        assert planes["axial"]["slice_count"] // 2 == 15            # initial cursor = integer midpoint
        img = png(c.get(f"/api/v1/studies/{sid}/slices/axial/15/image?display_revision=1"))
        assert img.shape == (planes["axial"]["height"], planes["axial"]["width"]) and img.max() > 100
        assert c.get("/api/v1/studies/" + sid).headers["cache-control"] == "no-store"
    with make_client(tmp_path) as c2:                                # service restart
        assert c2.get(f"/api/v1/studies/{sid}").json()["state"] == "ready"
        assert [i["id"] for i in c2.get("/api/v1/studies").json()["items"]] == [sid]
        png(c2.get(f"/api/v1/studies/{sid}/slices/coronal/10/image"))


def test_window_level_only_changes_image_and_validation(ready_study):
    c, sid, st, *_ = ready_study
    a = png(c.get(f"/api/v1/studies/{sid}/slices/axial/15/image?center=200&width=400"))
    b = png(c.get(f"/api/v1/studies/{sid}/slices/axial/15/image?center=100&width=50"))
    assert not np.array_equal(a, b)
    for q in ("width=0", "width=-3", "width=nan", "center=inf&width=3"):
        r = c.get(f"/api/v1/studies/{sid}/slices/axial/15/image?{q}")
        assert r.status_code == 422, q
    assert c.get(f"/api/v1/studies/{sid}/slices/axial/15/image?display_revision=9").status_code == 409
    assert c.get(f"/api/v1/studies/{sid}/slices/axial/999/image").status_code == 422
    assert c.get(f"/api/v1/studies/{sid}/slices/axial/-1/image").status_code == 422
    assert c.get(f"/api/v1/studies/{sid}/slices/oblique/1/image").status_code == 422
    assert c.get(f"/api/v1/studies/{uuid.uuid4()}/slices/axial/1/image").json()["error"]["code"] == "NOT_FOUND"


def test_mask_alignment_in_every_plane(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    mid, m = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.uint8))
    assert m["state"] == "ready" and m["source"] == "uploaded"
    assert m["statistics"]["voxel_count"] == int(mask.sum())
    # mask pixels must sit where the MRI shows lesion intensity (>= 330 in the phantom)
    for plane in ("axial", "coronal", "sagittal"):
        pl = st["display"]["planes"][plane]
        for idx in range(pl["slice_count"]):
            mk = png(c.get(f"/api/v1/studies/{sid}/masks/{mid}/slices/{plane}/{idx}"))
            if mk[..., 3].any():
                im = png(c.get(f"/api/v1/studies/{sid}/slices/{plane}/{idx}/image?center=200&width=400"))
                lesion_px = mk[..., 3] > 0
                assert (mk[..., 0][lesion_px] == 255).all() and (mk[..., 1][lesion_px] == 128).all()
                assert im[lesion_px].mean() > im[~lesion_px & (im > 0)].mean()          # lesion is bright in MRI
                break
        else:
            pytest.fail(f"no lesion pixels in {plane}")


def test_shifted_same_shape_mask_is_rejected(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    shifted = aff.copy(); shifted[0, 3] += 1.0
    mid, m = upload_mask(c, sid, nifti_bytes(mask, shifted, dtype=np.uint8))
    assert m["state"] == "failed" and m["failure"]["code"] == "MASK_GEOMETRY_MISMATCH"
    assert c.get(f"/api/v1/studies/{sid}").json()["selected_mask_id"] is None
    r = c.get(f"/api/v1/studies/{sid}/masks/{mid}/slices/axial/3")
    assert r.status_code == 409


@pytest.mark.parametrize("bad,code", [("prob", "MASK_INVALID_LABELS"), ("multi", "MASK_INVALID_LABELS")])
def test_invalid_mask_values(ready_study, bad, code):
    c, sid, st, aff, vol, mask = ready_study
    arr = mask.astype(np.float32) * (0.5 if bad == "prob" else 2)
    mid, m = upload_mask(c, sid, nifti_bytes(arr, aff))
    assert m["state"] == "failed" and m["failure"]["code"] == code


def test_wrong_shape_mask_and_other_binary_types(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    mid, m = upload_mask(c, sid, nifti_bytes(mask[:-1], aff, dtype=np.uint8))
    assert m["failure"]["code"] == "MASK_GEOMETRY_MISMATCH"
    mid, m = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.float32))     # exact 0/1 floats are fine
    assert m["state"] == "ready"


def test_empty_mask_is_valid(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    mid, m = upload_mask(c, sid, nifti_bytes(np.zeros_like(mask), aff, dtype=np.uint8))
    assert m["state"] == "ready" and m["statistics"]["voxel_count"] == 0
    assert m["statistics"]["message"] == "No labeled lesion voxels" and m["statistics"]["candidate_count"] == 0
    meas = c.get(f"/api/v1/studies/{sid}/masks/{mid}/slices/axial/5/measurements").json()
    assert meas["footprints"] == [] and meas["available"]


def test_statistics_candidates_and_whole_scan_measures(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    mid, m = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.uint8))
    vox = abs(np.linalg.det(aff[:3, :3]))
    s = m["statistics"]
    assert s["volume_mm3"] == pytest.approx(mask.sum() * vox) and s["volume_ml"] == pytest.approx(mask.sum() * vox / 1000)
    cands = m["candidates"]
    assert sum(x["voxel_count"] for x in cands) == mask.sum()
    assert s["candidate_count"] == len(cands) and 1 <= len(cands) <= 5
    for x in cands:
        assert x["max_3d_diameter_mm"] >= x["max_axial_diameter_mm"] > 0
        assert x["max_3d_diameter_mm"] >= x["equivalent_sphere_diameter_mm"] * 0.99
        assert x["anchor"] and len(x["anchor"]["display_index"]) == 3
    assert len(s["slice_load"]["axial"]) == st["display"]["planes"]["axial"]["slice_count"]
    assert sum(s["slice_load"]["axial"]) == mask.sum()
    assert sum(s["size_distribution"]["counts"]) == len(cands)


def test_measurement_matches_known_lesion_size(client):
    shape, sp = (40, 40, 12), (0.5, 2.0, 3.0)
    aff = phantom.centred_affine(shape, sp)
    m = np.zeros(shape, np.uint8); m[10:16, 20:23, 4:6] = 1       # 6 x 3 voxels in-plane, 2 slices
    vol = np.ones(shape, np.float32) * 10
    sid, st = upload_study(client, nifti_bytes(vol, aff))
    mid, mk = upload_mask(client, sid, nifti_bytes(m, aff, dtype=np.uint8))
    r = client.get(f"/api/v1/studies/{sid}/masks/{mid}/slices/axial/4/measurements?display_revision=1").json()
    f = r["footprints"][0]
    assert f["diameter_mm"] == pytest.approx(np.hypot(6 * 0.5, 3 * 2.0), abs=1e-3)
    assert r["method"] == "pixel_cell_maximum_2d_feret" and r["units"] == "mm"
    p0, p1 = (np.array(e) for e in f["endpoints_ras_mm"])
    assert np.linalg.norm(p0 - p1) == pytest.approx(f["diameter_mm"], abs=1e-6)
    cand = mk["candidates"][0]
    assert cand["max_3d_diameter_mm"] == pytest.approx(np.sqrt(3 ** 2 + 6 ** 2 + 6 ** 2), abs=1e-3)
    # coronal footprints computed on demand agree with the physical box
    rc = client.get(f"/api/v1/studies/{sid}/masks/{mid}/slices/coronal/0/measurements").json()
    assert rc["plane"] == "coronal"


def test_foreign_mask_and_traversal_ids(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    mid, _ = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.uint8))
    sid2, st2 = upload_study(c, nifti_bytes(vol, aff))
    for suffix in ("", "/download", "/provenance", "/slices/axial/3", "/slices/axial/3/measurements"):
        assert c.get(f"/api/v1/studies/{sid2}/masks/{mid}{suffix}").status_code == 404, suffix
    for bad in ("..%2F..%2Fapp.sqlite", "....//etc/passwd", "not-a-uuid", "%2e%2e"):
        assert c.get(f"/api/v1/studies/{bad}").status_code in (404, 422)
        assert c.get(f"/api/v1/studies/{sid}/masks/{bad}/download").status_code in (404, 422)


def test_export_roundtrip_and_provenance(ready_study, tmp_path):
    c, sid, st, aff, vol, mask = ready_study
    mid, m = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.float32))
    c.get(f"/api/v1/studies/{sid}/masks/{mid}/slices/coronal/20/measurements")
    r = c.get(f"/api/v1/studies/{sid}/masks/{mid}/download")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    f = tmp_path / "m.nii.gz"; f.write_bytes(r.content)
    img = nib.load(str(f))
    assert img.get_data_dtype() == np.uint8 and np.array_equal(np.asarray(img.dataobj), mask)
    assert np.allclose(img.affine, aff, atol=1e-5) and img.shape == mask.shape
    p = c.get(f"/api/v1/studies/{sid}/masks/{mid}/provenance").json()
    assert p["mask_source"] == "uploaded" and p["model"] is None and p["synthetic"] is False
    assert len(p["mask_sha256"]) == 64 and len(p["image_sha256"]) == 64
    assert p["diameter"]["method_2d"] == "pixel_cell_maximum_2d_feret"
    assert "partial" in p["exported_measurements"]["scope"]
    assert any(x["plane"] == "coronal" for x in p["exported_measurements"]["slices"])
    assert "scan.nii" not in str(p) and "/" not in p["study_id"]


def test_error_schema_everywhere(client):
    for url in ("/api/v1/nope", "/api/v1/studies/zzz", f"/api/v1/jobs/{uuid.uuid4()}"):
        e = client.get(url).json()["error"]
        assert set(e) == {"code", "message", "details", "request_id"}
    e = client.get("/api/v1/studies?limit=500").json()["error"]
    assert e["code"] == "VALIDATION_ERROR"
    assert "Traceback" not in str(e)


def test_delete_clears_everything_and_blocks_when_busy(tmp_path):
    aff, vol, mask = phantom_case()
    with make_client(tmp_path) as c:
        sid, st = upload_study(c, nifti_bytes(vol, aff))
        mid, _ = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.uint8))
        c.get(f"/api/v1/studies/{sid}/slices/axial/3/image")
        d = tmp_path / "data" / "studies" / sid
        assert d.exists()
        # a queued job makes the study busy
        c.app.state.ctx.db.insert_job(str(uuid.uuid4()), sid, "m")
        assert c.delete(f"/api/v1/studies/{sid}").status_code == 409
        with c.app.state.ctx.db.tx() as con:
            con.execute("DELETE FROM jobs")
        assert c.delete(f"/api/v1/studies/{sid}").status_code == 204
        assert not d.exists()
        assert c.get(f"/api/v1/studies/{sid}").status_code == 404
        db = c.app.state.ctx.db
        assert db.one("SELECT COUNT(*) AS n FROM masks")["n"] == 0
        assert db.one("SELECT COUNT(*) AS n FROM measurements")["n"] == 0


def test_demo_is_labelled_synthetic_and_works_without_model(client):
    sid = client.post("/api/v1/demo").json()["id"]
    st = wait(client, f"/api/v1/studies/{sid}", lambda s: s["state"] != "preparing" and s["masks"] and s["masks"][0]["state"] != "preparing")
    assert st["is_demo"] and st["masks"][0]["synthetic"] and st["masks"][0]["source"] == "synthetic_demo"
    mid = st["masks"][0]["id"]
    assert st["selected_mask_id"] == mid
    d = client.get(f"/api/v1/studies/{sid}/masks/{mid}/download")
    assert "SYNTHETIC" in d.headers["content-disposition"]
    assert client.get(f"/api/v1/studies/{sid}/masks/{mid}/provenance").json()["synthetic"] is True
    caps = client.get("/api/v1/capabilities").json()
    assert caps["inference"]["available"] is False and caps["demo"]["available"]
    r = client.post(f"/api/v1/studies/{sid}/jobs", json={"model_id": "x"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "INFERENCE_UNAVAILABLE"


def test_failed_new_mask_preserves_previous(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    good_id, _ = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.uint8))
    bad_id, bm = upload_mask(c, sid, nifti_bytes(mask.astype(np.float32) * 0.3, aff))
    assert bm["state"] == "failed"
    cur = c.get(f"/api/v1/studies/{sid}").json()
    assert cur["selected_mask_id"] == good_id
    assert c.get(f"/api/v1/studies/{sid}/masks/{good_id}/slices/axial/5").status_code == 200


def test_pagination(client):
    aff, vol, mask = phantom_case(shape=(8, 8, 4))
    ids = [upload_study(client, nifti_bytes(vol, aff))[0] for _ in range(3)]
    p1 = client.get("/api/v1/studies?limit=2").json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = client.get(f"/api/v1/studies?limit=2&cursor={p1['next_cursor']}").json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None
    assert {i["id"] for i in p1["items"] + p2["items"]} == set(ids)


def test_half_voxel_header_offset_needs_explicit_acceptance_and_is_recorded(ready_study):
    c, sid, st, aff, vol, mask = ready_study
    off = aff.copy(); off[:3, 3] += 0.5 * np.array([1.0, 1.5, 2.5])          # half a voxel on each axis
    mid, m = upload_mask(c, sid, nifti_bytes(mask, off, dtype=np.uint8))
    assert m["state"] == "failed" and m["failure"]["code"] == "MASK_HEADER_OFFSET" and m["failure"]["can_override"]
    r = c.post(f"/api/v1/studies/{sid}/masks", files={"mask": ("m.nii.gz", nifti_bytes(mask, off, dtype=np.uint8))},
               data={"accept_subvoxel_offset": "true"})
    mid2 = r.json()["id"]
    m2 = wait(c, f"/api/v1/studies/{sid}/masks/{mid2}", lambda x: x["state"] != "preparing")
    assert m2["state"] == "ready" and m2["statistics"]["voxel_count"] == int(mask.sum())
    p = c.get(f"/api/v1/studies/{sid}/masks/{mid2}/provenance").json()
    assert p["geometry_override"]["max_corner_offset_mm"] > 0.5
    # more than half a voxel (or a rotation) is never overridable
    far = aff.copy(); far[0, 3] += 0.8
    r = c.post(f"/api/v1/studies/{sid}/masks", files={"mask": ("m.nii.gz", nifti_bytes(mask, far, dtype=np.uint8))},
               data={"accept_subvoxel_offset": "true"})
    m3 = wait(c, f"/api/v1/studies/{sid}/masks/{r.json()['id']}", lambda x: x["state"] != "preparing")
    assert m3["failure"]["code"] == "MASK_GEOMETRY_MISMATCH"
