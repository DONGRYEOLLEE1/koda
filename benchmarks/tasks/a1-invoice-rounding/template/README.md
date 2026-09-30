# invoicer

Small invoicing library used by the billing service.

```python
from invoicer import Invoice, LineItem

inv = Invoice([LineItem("widget", 2.675, 2), LineItem("cable", "4.10")], discount_rate="0.1", tax_rate=0.08)
inv.total()      # Decimal
print(inv.summary())
```

## Money rules

These rules are the contract with the finance team. Every amount returned by the library follows them.

1. Inputs (prices and rates) may be `int`, `str`, `float` or `Decimal`. A `float` means the decimal number
   it is written as (its shortest `repr`, e.g. `2.675` is exactly 2.675), never its binary expansion.
2. Money is `decimal.Decimal` with two decimal places. Rounding is **half up** (0.005 rounds to 0.01),
   applied at every step:
   - line amount = unit price x quantity, rounded;
   - subtotal = sum of line amounts;
   - discount = subtotal x discount rate, rounded;
   - tax = (subtotal - discount) x tax rate, rounded.
3. total = subtotal - discount + tax.
4. Rates themselves are never rounded.

## Tests

    python -m pytest
