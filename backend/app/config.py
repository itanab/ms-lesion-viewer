"""Runtime settings, read from MSLV_* environment variables."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

MiB = 1024 * 1024


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    data_root: Path = Path("./data")
    max_upload_bytes: int = 256 * MiB
    max_decompressed_bytes: int = 512 * MiB
    max_voxels: int = 64_000_000  # spec default is 32M; raised so full-resolution open_ms_data scans (50M voxels) fit
    max_working_memory_bytes: int = 2048 * MiB
    slice_cache_bytes: int = 512 * MiB
    # Inference
    adapter: str = "none"  # "none" | "mock" | "package.module:factory"
    adapter_config: str = "{}"  # JSON string passed to the adapter factory
    enable_mock: bool = False  # mock adapter only runs with explicit demo/test config
    device: str = "cpu"
    job_timeout_s: float = 30 * 60
    worker_poll_s: float = 0.5
    run_worker: bool = True  # run the job-queue thread inside the API process
    # Demo
    demo_shape: tuple[int, int, int] = (128, 160, 96)
    demo_spacing: tuple[float, float, float] = (1.0, 1.0, 1.5)
    # Serving
    frontend_dist: Path | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        kw: dict = {}
        if "MSLV_DATA_ROOT" in e:
            kw["data_root"] = Path(e["MSLV_DATA_ROOT"])
        for env, field in [
            ("MSLV_MAX_UPLOAD_BYTES", "max_upload_bytes"),
            ("MSLV_MAX_DECOMPRESSED_BYTES", "max_decompressed_bytes"),
            ("MSLV_MAX_VOXELS", "max_voxels"),
            ("MSLV_MAX_WORKING_MEMORY_BYTES", "max_working_memory_bytes"),
            ("MSLV_SLICE_CACHE_BYTES", "slice_cache_bytes"),
        ]:
            if env in e:
                kw[field] = int(e[env])
        for env, field in [("MSLV_JOB_TIMEOUT_S", "job_timeout_s"), ("MSLV_WORKER_POLL_S", "worker_poll_s")]:
            if env in e:
                kw[field] = float(e[env])
        for env, field in [("MSLV_ADAPTER", "adapter"), ("MSLV_ADAPTER_CONFIG", "adapter_config"), ("MSLV_DEVICE", "device")]:
            if env in e:
                kw[field] = e[env]
        kw["enable_mock"] = _env_bool("MSLV_ENABLE_MOCK", False)
        kw["run_worker"] = _env_bool("MSLV_RUN_WORKER", True)
        if e.get("MSLV_FRONTEND_DIST"):
            kw["frontend_dist"] = Path(e["MSLV_FRONTEND_DIST"])
        return cls(**kw)

    def to_env(self) -> dict[str, str]:
        """Environment for the inference child process."""
        return {
            "MSLV_DATA_ROOT": str(self.data_root),
            "MSLV_ADAPTER": self.adapter,
            "MSLV_ADAPTER_CONFIG": self.adapter_config,
            "MSLV_ENABLE_MOCK": "1" if self.enable_mock else "0",
            "MSLV_DEVICE": self.device,
        }
