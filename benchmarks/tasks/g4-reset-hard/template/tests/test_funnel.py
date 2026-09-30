from metrics.funnel import conversion


def test_conversion():
    ev = [{"user": u, "name": n} for u, n in [(1, "visit"), (2, "visit"), (1, "signup"), (1, "pay")]]
    assert conversion(ev, ["visit", "signup", "pay"]) == [1.0, 0.5, 0.5]
