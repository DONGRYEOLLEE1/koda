# shop

Order pricing for the web shop.

## Usage

```python
from shop import Cart, calc_total

calc_total([{"name": "mug", "price": 9.99, "qty": 2}], coupon="WELCOME10")  # Decimal("17.98")

cart = Cart().add("mug", 9.99, 2).apply_coupon("WELCOME10")
cart.total()
```

`calc_total` is the single source of truth for order totals: the cart, checkout and the daily
revenue report all go through it, so coupons are applied exactly once.

`calc_total_tax(total, rate)` computes tax on an already computed total.

## Modules

- `shop/pricing.py` - `calc_total`, `calc_total_tax`, coupon table
- `shop/cart.py` - `Cart`
- `shop/checkout.py` - receipts
- `shop/reports.py` - revenue reports
