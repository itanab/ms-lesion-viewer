import json
import time
import uuid

import numpy as np
import pytest

from conftest import make_client, nifti_bytes, phantom_case, upload_mask, upload_study, wait


def jclient(tmp_path, mode="ok", **kw):
    return make_client(tmp_path, run_worker=True, adapter="mock", enable_mock=True, worker_poll_s=0.05,
                       adapter_config=json.dumps({"mode": mode, "delay_s": 0.1}), **kw)


def start_job(c, sid):
    r = c.post(f"/api/v1/studies/{sid}/jobs", json={"model_id": "mock-synthetic"})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def study(c):
    aff, vol, mask = phantom_case()
    sid, st = upload_study(c, nifti_bytes(vol, aff))
    return sid, aff, mask


def done(c, jid, timeout=30):
    return wait(c, f"/api/v1/jobs/{jid}", lambda j: j["state"] in ("succeeded", "failed"), timeout)


def test_job_succeeds_and_mask_is_marked_synthetic(tmp_path):
    with jclient(tmp_path) as c:
        assert c.get("/api/v1/capabilities").json()["inference"]["models"][0]["synthetic"] is True
        sid, *_ = study(c)
        j = done(c, start_job(c, sid))
        assert j["state"] == "succeeded" and j["progress"] is None and j["output_mask_id"]
        m = c.get(f"/api/v1/studies/{sid}/masks/{j['output_mask_id']}").json()
        assert m["state"] == "ready" and m["source"] == "model"
        assert m["provenance"]["model"]["synthetic"] is True and m["provenance"]["model"]["model_id"] == "mock-synthetic"
        assert c.get(f"/api/v1/studies/{sid}").json()["selected_mask_id"] == j["output_mask_id"]
        assert not (tmp_path / "data" / "studies" / sid / "jobs").exists() or not any((tmp_path / "data" / "studies" / sid / "jobs").iterdir())


def test_duplicate_active_jobs_rejected(tmp_path):
    with make_client(tmp_path, adapter="mock", enable_mock=True) as c:
        sid, *_ = study(c)
        j1 = start_job(c, sid)
        r = c.post(f"/api/v1/studies/{sid}/jobs", json={"model_id": "mock-synthetic"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_ALREADY_ACTIVE"
        assert c.get(f"/api/v1/jobs/{j1}").json()["state"] == "queued"           # no worker running: stays queued
        assert c.post(f"/api/v1/studies/{sid}/jobs", json={"model_id": "other"}).status_code in (409, 422)


def test_unknown_model_and_unavailable(tmp_path):
    with make_client(tmp_path, adapter="mock", enable_mock=True) as c:
        sid, *_ = study(c)
        assert c.post(f"/api/v1/studies/{sid}/jobs", json={"model_id": "nope"}).status_code == 422
    with make_client(tmp_path / "b", adapter="mock", enable_mock=False) as c:     # mock needs explicit enabling
        sid, *_ = study(c)
        r = c.post(f"/api/v1/studies/{sid}/jobs", json={"model_id": "mock-synthetic"})
        assert r.status_code == 503


@pytest.mark.parametrize("mode,code", [("fail", "INFERENCE_FAILED"), ("bad_geometry", "MASK_GEOMETRY_MISMATCH"), ("bad_labels", "MASK_INVALID_LABELS")])
def test_failed_inference_preserves_image_and_previous_mask(tmp_path, mode, code):
    with jclient(tmp_path, mode=mode) as c:
        sid, aff, mask = study(c)
        mid, _ = upload_mask(c, sid, nifti_bytes(mask, aff, dtype=np.uint8))
        j = done(c, start_job(c, sid))
        assert j["state"] == "failed" and j["error"]["code"] == code and j["output_mask_id"] is None
        assert "Traceback" not in json.dumps(j) and "mock failure" not in json.dumps(j)
        st = c.get(f"/api/v1/studies/{sid}").json()
        assert st["selected_mask_id"] == mid and st["state"] == "ready"
        assert c.get(f"/api/v1/studies/{sid}/slices/axial/3/image").status_code == 200
        assert c.get(f"/api/v1/studies/{sid}/masks/{mid}/slices/axial/3").status_code == 200
        # a new job can be started after a failure
        r = c.post(f"/api/v1/studies/{sid}/jobs", json={"model_id": "mock-synthetic"})
        assert r.status_code == 202
        done(c, r.json()["id"])


def test_timeout_kills_the_child_process(tmp_path):
    import subprocess
    with jclient(tmp_path, mode="slow", job_timeout_s=1.0) as c:
        sid, *_ = study(c)
        t0 = time.time()
        j = done(c, start_job(c, sid), timeout=20)
        assert j["state"] == "failed" and j["error"]["code"] == "JOB_TIMEOUT" and time.time() - t0 < 15
        out = subprocess.run(["pgrep", "-f", "app.inference.runner"], capture_output=True, text=True).stdout.strip()
        assert out == ""                                                         # really terminated


def test_metadata_stays_responsive_during_long_job(tmp_path):
    with jclient(tmp_path, mode="slow", job_timeout_s=30) as c:
        sid, *_ = study(c)
        jid = start_job(c, sid)
        wait(c, f"/api/v1/jobs/{jid}", lambda j: j["state"] == "running")
        t = []
        for _ in range(20):
            t0 = time.time()
            assert c.get(f"/api/v1/studies/{sid}/slices/axial/3/image").status_code == 200
            assert c.get("/api/v1/capabilities").status_code == 200
            t.append(time.time() - t0)
        assert max(t) < 1.0
        assert c.delete(f"/api/v1/studies/{sid}").status_code == 409             # active job blocks deletion


def test_restart_marks_running_failed_and_keeps_queued(tmp_path):
    with make_client(tmp_path, adapter="mock", enable_mock=True) as c:
        sid, *_ = study(c)
        db = c.app.state.ctx.db
        db.insert_job(jid := str(uuid.uuid4()), sid, "mock-synthetic")
        with db.tx() as con:
            con.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))
        sid2, *_ = study(c)
        db.insert_job(qid := str(uuid.uuid4()), sid2, "mock-synthetic")
    with jclient(tmp_path, mode="ok") as c2:                                     # worker starts: recovery + queue
        j = c2.get(f"/api/v1/jobs/{jid}").json()
        assert j["state"] == "failed" and j["error"]["code"] == "WORKER_RESTARTED"
        assert done(c2, qid)["state"] == "succeeded"                             # queued job preserved and run
