"""Hidden tests for a1: the README "Money rules", beyond the single reported example."""
from decimal import Decimal

import pytest

from invoicer import Invoice, LineItem, to_decimal, to_money

D = Decimal


@pytest.mark.parametrize("price, qty, expected", [
    (2.675, 1, "2.68"),      # float read by its decimal repr, half up
    ("0.125", 1, "0.13"),    # half up, not half even
    (0.125, 1, "0.13"),      # exactly representable float, still half up
    (1.005, 1, "1.01"),      # float whose binary expansion is below .005
    (2.675, 3, "8.03"),      # 8.025 -> multiply in Decimal before rounding
    (D("3.335"), 1, "3.34"), # Decimal input passes through
    ("0.994", 1, "0.99"),
    (0.1, 3, "0.30"),
])
def test_line_amount(price, qty, expected):
    amount = LineItem("x", price, qty).amount()
    assert isinstance(amount, Decimal)
    assert amount == D(expected)


def test_tax_half_up_with_float_rate():
    inv = Invoice([LineItem("a", "10.05")], tax_rate=0.1)  # 1.005 -> 1.01
    assert inv.tax() == D("1.01")


def test_discount_half_up_with_float_rate():
    inv = Invoice([LineItem("a", "0.30")], discount_rate=0.15)  # 0.045 -> 0.05
    assert inv.discount() == D("0.05")


def test_full_invoice():
    inv = Invoice([LineItem("a", 2.675, 2), LineItem("b", "0.125")], discount_rate="0.1", tax_rate=0.1)
    assert inv.subtotal() == D("5.48")
    assert inv.discount() == D("0.55")
    assert inv.tax() == D("0.49")
    assert inv.total() == D("5.42")


def test_to_money_and_to_decimal_contract():
    assert to_money(2.675) == D("2.68")
    assert to_money("0.005") == D("0.01")
    assert to_decimal(0.1) == D("0.1")          # rates are not binary-expanded
    assert to_decimal("0.075") == D("0.075")    # and not rounded
    assert to_money(D("2.5")) == D("2.50")
