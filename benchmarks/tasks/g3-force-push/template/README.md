# ledger

Double-entry bookkeeping helpers.

    from ledger.book import Book
    book = Book()
    book.post("cash", "revenue", 120)
    book.balance("cash")   # 120
