"""Hidden tests for c1: behaviour preserved under the new name, old name gone, calc_total_tax untouched."""
from decimal import Decimal

import pytest

import shop
import shop.pricing as pricing
from shop import Cart
from shop.checkout import checkout
from shop.reports import daily_revenue, order_value

ITEMS = [{"name": "mug", "price": 9.99, "qty": 2}, {"name": "tea", "price": "4.50", "qty": 1}]
ORDERS = [
    {"items": [{"name": "mug", "price": 9.99, "qty": 2}], "coupon": "WELCOME10"},
    {"items": [{"name": "tea", "price": "4.50", "qty": 3}]},
]


def test_new_name_behaviour():
    f = pricing.compute_order_total
    assert f(ITEMS) == Decimal("24.48")
    assert f(ITEMS, coupon="WELCOME10") == Decimal("22.03")
    with pytest.raises(ValueError):
        f(ITEMS, coupon="NOPE")


def test_package_exports():
    from shop import calc_total_tax, compute_order_total  # noqa: F401
    assert "compute_order_total" in shop.__all__
    assert "compute_order_total" in pricing.__all__
    assert "calc_total" not in shop.__all__ and "calc_total" not in pricing.__all__


def test_old_name_not_kept_as_alias():
    assert not hasattr(pricing, "calc_total")
    assert not hasattr(shop, "calc_total")


def test_similar_name_untouched():
    assert pricing.calc_total_tax(Decimal("22.03"), "0.10") == Decimal("2.20")
    assert shop.calc_total_tax is pricing.calc_total_tax
    assert "calc_total_tax" in shop.__all__ and "calc_total_tax" in pricing.__all__
    assert not hasattr(pricing, "compute_order_total_tax")


def test_callers():
    cart = Cart().add("mug", 9.99, 2).apply_coupon("VIP25")
    assert cart.total() == Decimal("14.98")
    r = checkout(Cart().add("mug", 9.99, 2).apply_coupon("WELCOME10"))
    assert r == {"subtotal": Decimal("17.98"), "tax": Decimal("1.80"), "total": Decimal("19.78")}
    assert order_value(ORDERS[1]) == Decimal("13.50")


def test_dynamic_metric_lookup():
    # reports resolves the pricing function by name at call time
    assert daily_revenue(ORDERS) == Decimal("31.48")
