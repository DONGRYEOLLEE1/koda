from decimal import Decimal

from shop.reports import daily_revenue, order_value

ORDERS = [
    {"items": [{"name": "mug", "price": 9.99, "qty": 2}], "coupon": "WELCOME10"},
    {"items": [{"name": "tea", "price": "4.50", "qty": 3}]},
]


def test_order_value():
    assert order_value(ORDERS[1]) == Decimal("13.50")


def test_daily_revenue():
    assert daily_revenue(ORDERS) == Decimal("31.48")
