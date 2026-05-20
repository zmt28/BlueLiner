"""
Tiny bounded cache: LRU eviction at `maxsize`, optional per-entry TTL.

The per-state geo caches (trout GeoDataFrames, assembled rivers, NLDI
flowlines, USGS medians) used to be plain dicts that only ever grew --
the direct cause of the 512MB free-tier OOM once the viewport feature
started fanning out to many states. `LruTtl` caps them: it behaves
enough like a dict for the existing call sites (`in`, `get`, `[]`,
`clear`) while evicting the least-recently-used entry past `maxsize` and
dropping entries older than `ttl` (when set).
"""

import time
from collections import OrderedDict


class LruTtl:
    def __init__(self, maxsize: int, ttl: float | None = None):
        self.maxsize = maxsize
        self.ttl = ttl
        self._d: "OrderedDict[object, tuple[float, object]]" = OrderedDict()

    def _fresh(self, key):
        """Return the live entry value or pop+miss. None means absent."""
        entry = self._d.get(key)
        if entry is None:
            return None
        ts, value = entry
        if self.ttl is not None and (time.monotonic() - ts) > self.ttl:
            self._d.pop(key, None)
            return None
        return (value,)  # wrap so a stored None/{} isn't confused with miss

    def __contains__(self, key) -> bool:
        return self._fresh(key) is not None

    def get(self, key, default=None):
        hit = self._fresh(key)
        if hit is None:
            return default
        self._d.move_to_end(key)
        return hit[0]

    def __getitem__(self, key):
        hit = self._fresh(key)
        if hit is None:
            raise KeyError(key)
        self._d.move_to_end(key)
        return hit[0]

    def put(self, key, value) -> None:
        self._d[key] = (time.monotonic(), value)
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)  # evict least-recently-used

    __setitem__ = put

    def __len__(self) -> int:
        return len(self._d)

    def clear(self) -> None:
        self._d.clear()
