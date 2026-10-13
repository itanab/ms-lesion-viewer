"""Persistent job-queue consumer (a thread inside the API process). One job at a time."""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .context import AppContext
from .db_helpers import finish_job  # noqa: F401  (re-export for tests)
from .inference.protocol import load_adapter
from .pipeline import prepare_mask, study_original_path

log = logging.getLogger("mslv.worker")


class JobWorker:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.ctx.db.fail_interrupted_jobs()
        self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:      # do not leave an orphaned inference process
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.ctx.db.claim_job()
                if job:
                    self._run(job)
                    continue
            except Exception as e:  # noqa: BLE001
                log.error("worker loop error type=%s", type(e).__name__)
            self._stop.wait(self.ctx.settings.worker_poll_s)

    def _fail(self, job_id: str, code: str, message: str) -> None:
        finish_job(self.ctx, job_id, "failed", error_code=code, error_message=message)

    def _run(self, job: dict) -> None:
        ctx, s = self.ctx, self.ctx.settings
        jid, sid = job["id"], job["study_id"]
        jdir = ctx.paths.job_dir(sid, jid)
        try:
            study = ctx.db.get_study(sid)
            if not study or study["state"] != "ready":
                return self._fail(jid, "STUDY_NOT_READY", "The MRI is not ready.")
            jdir.mkdir(parents=True, exist_ok=True)
            (jdir / "request.json").write_text(json.dumps({
                "job_id": jid, "image_path": str(study_original_path(ctx, study)),
                "geometry": study["geometry"]["original"], "sequence_label": study["sequence_label"], "device": s.device}))
            env = {**os.environ, **s.to_env()}
            proc = subprocess.Popen([sys.executable, "-m", "app.inference.runner", str(jdir)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self._proc = proc
            t0, last = time.time(), None
            while proc.poll() is None:
                if self._stop.is_set():
                    return self._fail(jid, "WORKER_STOPPED", "The worker was stopped.")
                if time.time() - t0 > s.job_timeout_s:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    return self._fail(jid, "JOB_TIMEOUT", "The segmentation timed out and was stopped.")
                try:
                    st = json.loads((jdir / "stage.json").read_text())["stage"]
                    if st != last:
                        last = st
                        ctx.db.update("jobs", jid, stage=st)
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(0.1)
            if proc.returncode != 0 or not (jdir / "result.json").exists():
                try:
                    err = json.loads((jdir / "error.json").read_text())
                except (OSError, ValueError):
                    err = {"code": "INFERENCE_FAILED", "message": "The segmentation model failed."}
                return self._fail(jid, err["code"], err["message"])
            res = json.loads((jdir / "result.json").read_text())
            ctx.db.update("jobs", jid, stage="validating mask")
            mask_id = str(uuid.uuid4())
            ctx.db.insert_mask(mask_id, sid, "model")
            prepare_mask(ctx, sid, mask_id, Path(res["mask_path"]), "model",
                         model_info={k: v for k, v in res.items() if k != "mask_path"}, cleanup_source=False)
            m = ctx.db.get_mask(sid, mask_id)
            if m["state"] != "ready":
                return self._fail(jid, m["failure_code"] or "MASK_INVALID", m["failure_message"] or "The model output was rejected.")
            finish_job(ctx, jid, "succeeded", output_mask_id=mask_id)
        except Exception as e:  # noqa: BLE001
            log.error("job failed type=%s id=%s", type(e).__name__, jid)
            self._fail(jid, "JOB_FAILED", "The job failed unexpectedly.")
        finally:
            shutil.rmtree(jdir, ignore_errors=True)


def adapter_status(settings) -> tuple[object | None, str | None]:
    """(adapter, reason_unavailable) for capabilities and job creation."""
    try:
        a = load_adapter(settings.adapter, settings.enable_mock, settings.adapter_config)
    except Exception:  # noqa: BLE001
        return None, "The configured segmentation adapter could not be loaded."
    if a is None:
        return None, "No segmentation model is configured. Upload a mask or open the synthetic demo."
    return a, None
