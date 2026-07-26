"""In-memory TTL cache for Tally responses.

Two jobs, both about protecting the customer's desktop:

* Tally serves one request at a time and blocks its UI while exporting. Three
  family members opening the app at once must not mean three full day-book
  exports -- the operator would see Tally freeze.
* It backs the offline story. When Tally goes away, :meth:`get_stale` still
  returns the last good answer so the app can show data with a "last updated"
  stamp instead of an error (CLAUDE.md: "If Tally is offline, show last synced
  data").

Deliberately in-memory: cached accounting data must not outlive the process on
a shared office PC.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    stored_at: float
    expires_at: float

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.stored_at

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at


def cache_key(query: str, params: dict[str, Any]) -> str:
    """Stable key for a query + params pair.

    Params are serialised with sorted keys so that ``{"a":1,"b":2}`` and
    ``{"b":2,"a":1}`` share one entry.
    """
    canonical = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(f"{query}|{canonical}".encode()).hexdigest()[:32]
    return f"{query}:{digest}"


class ResponseCache:
    """TTL cache with bounded size and stale-read support."""

    def __init__(self, *, max_entries: int = 256, stale_ttl_seconds: float = 24 * 3600) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._max_entries = max_entries
        self._stale_ttl = stale_ttl_seconds
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Return the value only while it is still fresh."""
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if not entry.is_fresh(time.monotonic()):
                return None
            return entry.value

    async def get_stale(self, key: str) -> CacheEntry | None:
        """Return the entry even if expired, as long as it is not ancient.

        Used only on the failure path. The caller must surface the entry's age
        to the user -- silently showing month-old receivables as current would
        be worse than showing an error.
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.age_seconds > self._stale_ttl:
                del self._entries[key]
                return None
            return entry

    async def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store a value.

        A zero or negative TTL still stores the entry: it is immediately stale
        for :meth:`get` but remains available to :meth:`get_stale`, which is
        what makes a forced refresh still useful as an offline fallback later.
        """
        now = time.monotonic()
        async with self._lock:
            if len(self._entries) >= self._max_entries and key not in self._entries:
                self._evict_locked(now)
            self._entries[key] = CacheEntry(
                value=value, stored_at=now, expires_at=now + max(ttl_seconds, 0.0)
            )

    def _evict_locked(self, now: float) -> None:
        """Drop expired entries first, then the oldest."""
        expired = [k for k, e in self._entries.items() if not e.is_fresh(now)]
        for key in expired:
            del self._entries[key]
        if len(self._entries) >= self._max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].stored_at)
            del self._entries[oldest]

    async def invalidate(self, prefix: str | None = None) -> int:
        """Drop everything, or every entry for one query name."""
        async with self._lock:
            if prefix is None:
                count = len(self._entries)
                self._entries.clear()
                return count
            doomed = [k for k in self._entries if k.startswith(f"{prefix}:")]
            for key in doomed:
                del self._entries[key]
            return len(doomed)

    async def stats(self) -> dict[str, int]:
        now = time.monotonic()
        async with self._lock:
            return {
                "entries": len(self._entries),
                "fresh": sum(1 for e in self._entries.values() if e.is_fresh(now)),
            }
