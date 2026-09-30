from decimal import Decimal

from shop import Cart
from shop.checkout import checkout


def test_checkout_receipt():
    r = checkout(Cart().add("mug", 9.99, 2).apply_coupon("WELCOME10"))
    assert r == {"subtotal": Decimal("17.98"), "tax": Decimal("1.80"), "total": Decimal("19.78")}
