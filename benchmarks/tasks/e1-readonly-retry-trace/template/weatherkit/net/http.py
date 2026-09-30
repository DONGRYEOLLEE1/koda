"""HTTP client with retries."""
from .. import settings
from .backoff import retry_with_backoff


class HttpClient:
    def __init__(self, transport, base_url=None):
        self.transport = transport
        self.base_url = base_url or settings.API_BASE_URL

    def _send(self, path, params):
        return self.transport.request("GET", self.base_url + path, params=params,
                                      timeout=settings.REQUEST_TIMEOUT_S)

    def get(self, path, params=None):
        return retry_with_backoff(
            lambda: self._send(path, params or {}),
            attempts=settings.RETRY_ATTEMPTS,
            base_delay=settings.RETRY_BASE_DELAY_S,
            max_delay=settings.RETRY_MAX_DELAY_S,
            retry_on=(TimeoutError, ConnectionError),
        )
