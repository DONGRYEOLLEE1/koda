from decimal import Decimal

import pytest

from invoicer import Invoice, LineItem


def test_simple_invoice():
    inv = Invoice([LineItem("pen", "1.50", 2), LineItem("pad", "3.25")])
    assert inv.subtotal() == Decimal("6.25")
    assert inv.total() == Decimal("6.25")


def test_discount_and_tax():
    inv = Invoice([LineItem("chair", "100.00")], discount_rate="0.1", tax_rate="0.08")
    assert inv.discount() == Decimal("10.00")
    assert inv.tax() == Decimal("7.20")
    assert inv.total() == Decimal("97.20")


def test_negative_qty_rejected():
    with pytest.raises(ValueError):
        LineItem("x", "1.00", -1).amount()


def test_reported_rounding_bug():
    # Customer report: a 2.675 item is billed 2.67 instead of 2.68.
    assert LineItem("widget", 2.675).amount() == Decimal("2.68")
