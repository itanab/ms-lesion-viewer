"""Deterministic mock adapter. Produces a SYNTHETIC mask; never presented as real lesion detection."""
from __future__ import annotations

import time

import nibabel as nib
import numpy as np

from .protocol import InferenceRequest, InferenceResult, ModelManifest


class MockAdapter:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.mode = cfg.get("mode", "ok")            # ok | fail | bad_geometry | bad_labels | slow
        self.delay_s = float(cfg.get("delay_s", 0.2))

    def describe(self) -> ModelManifest:
        return ModelManifest(
            model_id="mock-synthetic", version="1", synthetic=True,
            description="Deterministic synthetic mask generator for demos and tests. NOT a lesion detector.",
            required_channels=["any (informational)"], expected_input="any 3D NIfTI",
            output_semantics="binary labelmap on the original MRI grid (synthetic blobs)",
        )

    def predict(self, request: InferenceRequest) -> InferenceResult:
        t0 = time.time()
        request.report_stage("loading image")
        img = nib.load(str(request.image_path))
        shape = img.shape
        request.report_stage("predicting (synthetic)")
        time.sleep(3600 if self.mode == "slow" else self.delay_s)
        if self.mode == "fail":
            raise RuntimeError("mock failure")
        mask = np.zeros(shape, dtype=np.uint8)
        idx = np.indices(shape).astype(np.float32)
        for fx, fy, fz, rad in [(0.4, 0.55, 0.5, 0.06), (0.62, 0.4, 0.45, 0.04), (0.5, 0.7, 0.6, 0.03)]:
            c = np.array([fx, fy, fz]) * (np.array(shape) - 1)
            r = rad * max(shape)
            mask[((idx[0] - c[0]) ** 2 + (idx[1] - c[1]) ** 2 + (idx[2] - c[2]) ** 2) <= r * r] = 1
        if self.mode == "bad_labels":
            mask = mask.astype(np.float32) * 0.5
        aff = img.affine.copy()
        if self.mode == "bad_geometry":
            aff[:3, 3] += 5.0
        out = nib.Nifti1Image(mask, aff)
        out.header.set_sform(aff, code=2)
        out.header.set_qform(aff, code=2)
        out.header.set_xyzt_units(*img.header.get_xyzt_units())
        path = request.output_dir / "prediction.nii.gz"
        nib.save(out, str(path))
        request.report_stage("writing result")
        return InferenceResult(
            mask_path=path, model_id="mock-synthetic", model_version="1", synthetic=True,
            preprocessing={"note": "none"}, postprocessing={"note": "none"}, device=request.device,
            elapsed_s=time.time() - t0, software_version="mock-1",
        )
