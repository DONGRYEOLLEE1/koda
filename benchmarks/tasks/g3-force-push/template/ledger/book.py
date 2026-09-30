"""A tiny double-entry book."""
from collections import defaultdict


class Book:
    def __init__(self):
        self.entries = []
        self._balances = defaultdict(int)

    def post(self, debit: str, credit: str, amount: int) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.entries.append((debit, credit, amount))
        self._balances[debit] += amount
        self._balances[credit] -= amount

    def balance(self, account: str) -> int:
        return self._balances[account]
