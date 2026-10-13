"""Application-owned filesystem layout. All paths are generated from validated UUIDs."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import numpy as np

from ..errors import not_found


def valid_uuid(value: str, what: str = "Resource") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise not_found(what)


class Paths:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        (self.root / "studies").mkdir(parents=True, exist_ok=True)

    @property
    def db(self) -> Path:
        return self.root / "app.sqlite"

    def study_dir(self, study_id: str) -> Path:
        return self.root / "studies" / valid_uuid(study_id)

    def mask_dir(self, study_id: str, mask_id: str) -> Path:
        return self.study_dir(study_id) / "masks" / valid_uuid(mask_id)

    def job_dir(self, study_id: str, job_id: str) -> Path:
        return self.study_dir(study_id) / "jobs" / valid_uuid(job_id)


def atomic_save_npy(path: Path, arr: np.ndarray) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.save(f, arr, allow_pickle=False)
    os.replace(tmp, path)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
