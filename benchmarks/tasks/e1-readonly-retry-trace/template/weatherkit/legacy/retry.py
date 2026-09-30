"""Deprecated retry helper kept for old plugins. Not used by HttpClient."""
import time


def simple_retry(fn, attempts=3, delay=1.0):
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(delay)
