from semverlite import compare, parse


def test_parse_basic():
    v = parse("1.2.3")
    assert (v.major, v.minor, v.patch) == (1, 2, 3)


def test_compare_equal():
    assert compare("1.2.3", "1.2.3") == 0


def test_compare_numeric_parts():
    # bug report: 1.10.0 must be newer than 1.9.0
    assert compare("1.10.0", "1.9.0") == 1
