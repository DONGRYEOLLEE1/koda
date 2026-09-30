import pytest

from ledger.book import Book


def test_post_and_balance():
    b = Book()
    b.post("cash", "revenue", 120)
    assert b.balance("cash") == 120 and b.balance("revenue") == -120


def test_amount_must_be_positive():
    with pytest.raises(ValueError):
        Book().post("cash", "revenue", 0)
