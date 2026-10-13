"""Segmentation adapter boundary. The app never imports training code or downloads weights."""
from __future__ import annotations

import importlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol


@dataclass
class ModelManifest:
    model_id: str
    version: str
    description: str
    required_channels: list[str]            # e.g. ["FLAIR"]; this release supports exactly one channel
    expected_input: str
    output_semantics: str
    supported_devices: list[str] = field(default_factory=lambda: ["cpu"])
    synthetic: bool = False

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class InferenceRequest:
    job_id: str
    image_path: Path                        # validated original MRI
    geometry: dict                          # original geometry (shape, affine in mm, ...)
    sequence_label: str
    output_dir: Path
    device: str
    report_stage: Callable[[str], None]


@dataclass
class InferenceResult:
    mask_path: Path                         # original-grid binary mask
    model_id: str
    model_version: str
    checkpoint_sha256: str | None = None
    preprocessing: dict = field(default_factory=dict)
    postprocessing: dict = field(default_factory=dict)
    probability_threshold: float | None = None
    device: str = "cpu"
    elapsed_s: float = 0.0
    software_version: str = ""
    synthetic: bool = False


class SegmentationAdapter(Protocol):
    def describe(self) -> ModelManifest: ...
    def predict(self, request: InferenceRequest) -> InferenceResult: ...


def load_adapter(spec: str, enable_mock: bool, config_json: str = "{}") -> SegmentationAdapter | None:
    """'none' -> None; 'mock' (only with explicit enable) -> mock; 'pkg.module:factory' -> factory(config)."""
    if spec in ("", "none"):
        return None
    cfg = json.loads(config_json or "{}")
    if spec == "mock":
        if not enable_mock:
            return None
        from .mock import MockAdapter
        return MockAdapter(cfg)
    mod_name, _, attr = spec.partition(":")
    factory = getattr(importlib.import_module(mod_name), attr or "create_adapter")
    return factory(cfg)
