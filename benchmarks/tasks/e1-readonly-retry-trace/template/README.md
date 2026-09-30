# weatherkit

Client library for the internal weather API.

```python
from weatherkit.api import WeatherAPI
from weatherkit.net.http import HttpClient

api = WeatherAPI(HttpClient(transport))
api.current("Seoul")
```

- Responses are cached in memory for a short time (`weatherkit/cache.py`).
- Network errors are retried with exponential backoff.
- Settings live in `weatherkit/settings.py`.
