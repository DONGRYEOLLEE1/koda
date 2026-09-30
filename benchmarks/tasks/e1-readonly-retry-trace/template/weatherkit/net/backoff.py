"""Generic retry helper with exponential backoff."""
import time

DEFAULT_ATTEMPTS = 7
DEFAULT_BASE_DELAY_S = 0.25
DEFAULT_MAX_DELAY_S = 12.0


def compute_delay(retry_index: int, base_delay: float, max_delay: float) -> float:
    """Delay before retry number ``retry_index`` (0 = the first retry)."""
    return min(max_delay, base_delay * (2 ** retry_index))


def retry_with_backoff(fn, *, attempts=DEFAULT_ATTEMPTS, base_delay=DEFAULT_BASE_DELAY_S,
                       max_delay=DEFAULT_MAX_DELAY_S, retry_on=(Exception,), sleep=time.sleep):
    """Call ``fn`` up to ``attempts`` times, sleeping between failed attempts."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            last_exc = exc
            if attempt == attempts - 1:
                break
            sleep(compute_delay(attempt, base_delay, max_delay))
    raise last_exc
