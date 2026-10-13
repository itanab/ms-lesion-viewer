"""Child-process entry point: runs one prediction so a timed-out job can actually be killed."""
from __future__ import annotations

import json
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..config import Settings
from .protocol import InferenceRequest, load_adapter


def _write(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)


def main(job_dir: str) -> int:
    d = Path(job_dir)
    req = json.loads((d / "request.json").read_text())
    s = Settings.from_env()

    def stage(name: str) -> None:
        _write(d / "stage.json", {"stage": name})

    try:
        adapter = load_adapter(s.adapter, s.enable_mock, s.adapter_config)
        if adapter is None:
            _write(d / "error.json", {"code": "INFERENCE_UNAVAILABLE", "message": "No segmentation adapter is configured."})
            return 2
        res = adapter.predict(InferenceRequest(
            job_id=req["job_id"], image_path=Path(req["image_path"]), geometry=req["geometry"],
            sequence_label=req["sequence_label"], output_dir=d, device=req["device"], report_stage=stage))
        out = asdict(res)
        out["mask_path"] = str(res.mask_path)
        _write(d / "result.json", out)
        return 0
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())      # stays in the child's stderr, never in API responses
        _write(d / "error.json", {"code": "INFERENCE_FAILED", "message": "The segmentation model failed."})
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
