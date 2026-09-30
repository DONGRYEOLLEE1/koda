"""High-level API."""
from .cache import TTLCache
from .models import Current


class WeatherAPI:
    def __init__(self, client, cache=None):
        self.client = client
        self.cache = cache or TTLCache()

    def current(self, city: str) -> Current:
        key = ("current", city)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        result = Current.from_json(self.client.get("/v1/current", {"q": city}))
        self.cache.put(key, result)
        return result
