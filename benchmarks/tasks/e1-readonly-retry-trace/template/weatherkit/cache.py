"""Tiny in-memory TTL cache."""
import time

from . import settings


class TTLCache:
    def __init__(self, ttl=None, clock=time.monotonic):
        self.ttl = settings.CACHE_TTL_S if ttl is None else ttl
        self.clock = clock
        self._data = {}

    def get(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if self.clock() >= expires:
            del self._data[key]
            return None
        return value

    def put(self, key, value):
        self._data[key] = (value, self.clock() + self.ttl)
