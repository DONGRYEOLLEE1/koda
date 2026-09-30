"""Money helpers.

All monetary values are ``decimal.Decimal`` with two decimal places. The rounding policy is described in
README.md ("Money rules").
"""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_decimal(value) -> Decimal:
    """Convert a price or rate (int, str, float or Decimal) to Decimal without rounding.

    A float is read by its shortest repr (``2.675`` -> ``Decimal("2.675")``), not its binary expansion.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def to_money(value) -> Decimal:
    """Convert to Decimal and round to cents, half up."""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value) -> str:
    """Format a money value with thousands separators, e.g. ``1234.5`` -> ``"1,234.50"``."""
    return f"{to_money(value):,.2f}"
