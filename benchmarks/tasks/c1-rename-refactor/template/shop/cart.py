"""Shopping cart."""
from .pricing import calc_total


class Cart:
    def __init__(self):
        self.items = []
        self.coupon = None

    def add(self, name, price, qty=1):
        self.items.append({"name": name, "price": price, "qty": qty})
        return self

    def apply_coupon(self, code):
        self.coupon = code
        return self

    def total(self):
        return calc_total(self.items, coupon=self.coupon)
