"""Checkout: turns a cart into a receipt."""
from shop import pricing

TAX_RATE = "0.10"


def checkout(cart) -> dict:
    """Return a receipt. Uses pricing.calc_total so the coupon is applied exactly once."""
    subtotal = pricing.calc_total(cart.items, coupon=cart.coupon)
    tax = pricing.calc_total_tax(subtotal, TAX_RATE)
    return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax}
