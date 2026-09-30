from decimal import Decimal

from shop import Cart, calc_total


def test_cart_total_matches_calc_total():
    cart = Cart().add("mug", 9.99, 2).apply_coupon("VIP25")
    assert cart.total() == calc_total(cart.items, coupon="VIP25") == Decimal("14.98")
