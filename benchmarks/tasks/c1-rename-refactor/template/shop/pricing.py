"""Order pricing.

``calc_total`` is the single source of truth for order totals; everything else (cart, checkout,
reports) must call it instead of summing prices itself.
"""
from decimal import Decimal

__all__ = ["COUPONS", "calc_total", "calc_total_tax"]

CENT = Decimal("0.01")
COUPONS = {"WELCOME10": Decimal("0.10"), "VIP25": Decimal("0.25")}


def calc_total(items, *, coupon=None) -> Decimal:
    """Sum ``price * qty`` over ``items`` and apply an optional coupon code."""
    total = sum((Decimal(str(i["price"])) * i["qty"] for i in items), Decimal("0"))
    if coupon:
        rate = COUPONS.get(coupon)
        if rate is None:
            raise ValueError(f"unknown coupon: {coupon}")
        total -= total * rate
    return total.quantize(CENT)


def calc_total_tax(total, rate) -> Decimal:
    """Tax on an already computed total. Unrelated to the coupon logic of calc_total."""
    return (Decimal(str(total)) * Decimal(str(rate))).quantize(CENT)
