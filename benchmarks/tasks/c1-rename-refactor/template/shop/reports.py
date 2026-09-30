"""Revenue reports."""
from decimal import Decimal

import shop.pricing as p

# metric name -> name of the pricing function that computes it (resolved lazily so pricing can be patched)
METRICS = {"gross": "calc_total"}


def order_value(order) -> Decimal:
    return p.calc_total(order["items"], coupon=order.get("coupon"))


def daily_revenue(orders) -> Decimal:
    """Sum of calc_total over the day's orders."""
    fn = getattr(p, METRICS["gross"])
    return sum((fn(o["items"], coupon=o.get("coupon")) for o in orders), Decimal("0"))
