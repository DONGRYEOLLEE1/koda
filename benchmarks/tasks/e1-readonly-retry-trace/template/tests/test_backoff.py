import pytest

from weatherkit.net.backoff import compute_delay, retry_with_backoff


def test_compute_delay_caps():
    assert compute_delay(10, 1.0, 5.0) == 5.0


def test_retry_eventually_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError
        return "ok"

    assert retry_with_backoff(flaky, attempts=5, sleep=lambda s: None) == "ok"
    assert len(calls) == 3


def test_retry_gives_up():
    with pytest.raises(ConnectionError):
        retry_with_backoff(lambda: (_ for _ in ()).throw(ConnectionError()), attempts=2, sleep=lambda s: None)
