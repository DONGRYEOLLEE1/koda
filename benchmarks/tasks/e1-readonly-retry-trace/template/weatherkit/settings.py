"""Runtime settings. Values here override the library defaults."""
API_BASE_URL = "https://api.weather.example"
REQUEST_TIMEOUT_S = 5.0

# retry policy for HttpClient
RETRY_ATTEMPTS = 4          # total attempts per request, first try included
RETRY_BASE_DELAY_S = 0.5
RETRY_MAX_DELAY_S = 8.0

CACHE_TTL_S = 300
