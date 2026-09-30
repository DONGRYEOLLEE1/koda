"""Invoice model."""
from dataclasses import dataclass, field
from decimal import Decimal

from .money import format_money, to_decimal, to_money


@dataclass
class LineItem:
    description: str
    unit_price: object
    qty: int = 1

    def amount(self) -> Decimal:
        if self.qty < 0:
            raise ValueError("qty must be >= 0")
        return to_money(to_decimal(self.unit_price) * self.qty)


@dataclass
class Invoice:
    items: list = field(default_factory=list)
    discount_rate: object = 0
    tax_rate: object = 0

    def subtotal(self) -> Decimal:
        return to_money(sum((item.amount() for item in self.items), Decimal("0")))

    def discount(self) -> Decimal:
        return to_money(self.subtotal() * to_decimal(self.discount_rate))

    def tax(self) -> Decimal:
        return to_money((self.subtotal() - self.discount()) * to_decimal(self.tax_rate))

    def total(self) -> Decimal:
        return self.subtotal() - self.discount() + self.tax()

    def summary(self) -> str:
        lines = [f"{item.description:<20} {item.qty:>3} x {format_money(item.unit_price):>10} = "
                 f"{format_money(item.amount()):>10}" for item in self.items]
        lines += [
            f"{'subtotal':<37} {format_money(self.subtotal()):>10}",
            f"{'discount':<37} {format_money(-self.discount()):>10}",
            f"{'tax':<37} {format_money(self.tax()):>10}",
            f"{'total':<37} {format_money(self.total()):>10}",
        ]
        return "\n".join(lines)
