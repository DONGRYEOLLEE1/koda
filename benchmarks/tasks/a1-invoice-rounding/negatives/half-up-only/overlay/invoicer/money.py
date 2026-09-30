"""Partial fix used by selftest: rounding mode fixed, floats still binary-expanded (must FAIL)."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_decimal(value) -> Decimal:
    return Decimal(value)


def to_money(value) -> Decimal:
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value) -> str:
    return f"{to_money(value):,.2f}"
