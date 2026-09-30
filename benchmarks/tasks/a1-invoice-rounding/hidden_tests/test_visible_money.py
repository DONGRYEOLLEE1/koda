from decimal import Decimal

from invoicer import format_money, to_money


def test_format_money_thousands():
    assert format_money(1234.5) == "1,234.50"


def test_to_money_integer():
    assert to_money(3) == Decimal("3.00")


def test_to_money_string():
    assert to_money("19.99") == Decimal("19.99")
