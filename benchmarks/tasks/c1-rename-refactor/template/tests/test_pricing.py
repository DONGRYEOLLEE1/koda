from decimal import Decimal

import pytest

from shop.pricing import calc_total, calc_total_tax

ITEMS = [{"name": "mug", "price": 9.99, "qty": 2}, {"name": "tea", "price": "4.50", "qty": 1}]


def test_calc_total_plain():
    assert calc_total(ITEMS) == Decimal("24.48")


def test_calc_total_with_coupon():
    assert calc_total(ITEMS, coupon="WELCOME10") == Decimal("22.03")


def test_calc_total_unknown_coupon():
    with pytest.raises(ValueError):
        calc_total(ITEMS, coupon="NOPE")


def test_calc_total_tax():
    assert calc_total_tax(Decimal("22.03"), "0.10") == Decimal("2.20")
