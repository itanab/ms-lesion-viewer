"""Small thread-safe caches with byte bounds."""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np


class ByteLRU:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._d: OrderedDict = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key, value: bytes) -> None:
        with self._lock:
            if key in self._d:
                self._size -= len(self._d.pop(key))
            self._d[key] = value
            self._size += len(value)
            while self._size > self.max_bytes and self._d:
                _, old = self._d.popitem(last=False)
                self._size -= len(old)

    def drop_where(self, pred) -> None:
        with self._lock:
            for k in [k for k in self._d if pred(k)]:
                self._size -= len(self._d.pop(k))

    @property
    def size(self) -> int:
        return self._size


class VolumeCache:
    """Bounded cache of memory-mapped .npy volumes."""

    def __init__(self, max_items: int = 8):
        self.max_items = max_items
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path) -> np.ndarray:
        key = str(path)
        with self._lock:
            arr = self._d.get(key)
            if arr is not None:
                self._d.move_to_end(key)
                return arr
        arr = np.load(path, mmap_mode="r", allow_pickle=False)
        with self._lock:
            self._d[key] = arr
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)
        return arr

    def forget_under(self, directory: Path) -> None:
        prefix = str(directory)
        with self._lock:
            for k in [k for k in self._d if k.startswith(prefix)]:
                del self._d[k]
