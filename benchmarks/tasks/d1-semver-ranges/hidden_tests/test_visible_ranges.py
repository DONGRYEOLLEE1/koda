from semverlite import satisfies


def test_caret_same_major():
    assert satisfies("1.4.0", "^1.2.3")
    assert not satisfies("2.0.0", "^1.2.3")
