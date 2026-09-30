from weatherkit.api import WeatherAPI


class FakeClient:
    def __init__(self):
        self.calls = 0

    def get(self, path, params=None):
        self.calls += 1
        return {"city": params["q"], "temp_c": "21.5", "condition": "sunny"}


def test_current_is_cached():
    client = FakeClient()
    api = WeatherAPI(client)
    assert api.current("Seoul").temp_c == 21.5
    api.current("Seoul")
    assert client.calls == 1
