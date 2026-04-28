"""Thread-safe LRU cache for detection results.

The HTTP service used to keep an `OrderedDict` plus ad-hoc cache helpers
inline in `service.py`. Two reasons to factor it out:

1. **Thread safety.** FastAPI runs sync endpoints in the default
   threadpool (~40 workers). Concurrent `move_to_end` / `popitem` on a
   plain `OrderedDict` can raise `RuntimeError: dictionary changed size
   during iteration`. A single `threading.Lock` makes the operations
   atomic without measurable cost (we hold it for tens of microseconds).

2. **Single source of truth.** The HTTP layer only needs `get` / `put` /
   `clear` / `stats`. Putting them behind a small class makes it trivial
   to swap to Redis later without touching `service.py` further.

Cache key strategy: `blake2b(digest_size=16)` over a tagged concatenation
of `text`, `min_score` and the postpass flag. Faster than SHA-256 (about
2–3× on CPython) and far below collision-relevant volumes for any
realistic on-prem deploy.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Generic, TypeVar

from .filter import Span

T = TypeVar("T")


def detect_cache_key(text: str, min_score: float, ru_postpass_on: bool) -> str:
    # Tagged concat avoids collisions where the boundary between fields could
    # be ambiguous; `\x1f` is ASCII Unit Separator, never appears in normal
    # input. We round min_score to 4 decimals so equivalent floats hash to the
    # same key regardless of how the client formatted them.
    payload = (
        f"{text}\x1f"
        f"{round(min_score, 4):.4f}\x1f"
        f"{int(bool(ru_postpass_on))}"
    ).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


class LRUCache(Generic[T]):
    """Bounded LRU with a lock around every mutation."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._data: OrderedDict[str, T] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def get(self, key: str) -> tuple[T | None, bool]:
        """Return (value, hit). On miss returns (None, False)."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._hits += 1
                return self._data[key], True
            self._misses += 1
            return None, False

    def put(self, key: str, value: T) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "capacity": self._capacity,
                "hits": self._hits,
                "misses": self._misses,
            }


SpanListCache = LRUCache[list[Span]]
