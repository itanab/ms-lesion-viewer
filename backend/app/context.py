from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .cache import ByteLRU, VolumeCache
from .config import Settings
from .storage.db import Database
from .storage.files import Paths


@dataclass
class AppContext:
    settings: Settings
    paths: Paths
    db: Database
    slice_cache: ByteLRU
    volumes: VolumeCache = field(default_factory=VolumeCache)
    # One preparation at a time keeps aggregate working memory within the configured bound.
    executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="prep"))

    @classmethod
    def create(cls, settings: Settings) -> "AppContext":
        paths = Paths(settings.data_root)
        return cls(settings, paths, Database(paths.db), ByteLRU(settings.slice_cache_bytes))
