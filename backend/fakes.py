"""Stand-ins for the model reader and the Google Sheet.

Used by the tests and by local development runs (RECEIPTS_READER=fake,
RECEIPTS_SHEET=fake): nothing here calls a model or the real Sheet. The reading types
mirror the interface of `backend.reading` (duck-typed: the worker reads attributes).
"""

import time
from collections import Counter
from dataclasses import dataclass, field

from backend.config import SHEET_COLUMNS, ReceiptData


@dataclass
class Line:
    name: str
    total_cents: int
    qty: float = 1.0
    unit: str = "pcs"
    unit_price_cents: int | None = None
    discount_cents: int = 0
    vat_group: str | None = "Б"


@dataclass
class Discount:
    name: str
    cents: int


@dataclass
class Reading:
    date: str | None = "2026-10-14"
    time: str | None = "18:42"
    total_cents: int | None = 1234
    eik: str | None = "131129282"
    store_name: str | None = "Kaufland България"
    store_address: str | None = "София, ж.к. Младост"
    payment: str | None = "card"
    card_last4: str | None = "0889"
    category: str | None = "Храна"
    notes: str | None = "хляб, мляко, сирене"
    lines: list[Line] = field(default_factory=lambda: [
        Line("Хляб Добруджа", 139, unit_price_cents=139),
        Line("Мляко Верея 3%", 395, unit_price_cents=395, discount_cents=50),
        Line("Сирене краве", 750, qty=0.5, unit="kg", unit_price_cents=1500),
    ])
    receipt_discounts: list[Discount] = field(default_factory=list)
    quality: list[str] = field(default_factory=list)


@dataclass
class Attempt:
    provider: str = "fake"
    model: str = "fake-1"
    started_at: str = "2026-10-14T16:00:00+00:00"
    seconds: float = 1.0
    input_tokens: int = 1500
    output_tokens: int = 400
    cost_usd: float = 0.004
    outcome: str = "ok"
    raw: dict | None = None
    error: str | None = None


@dataclass
class Outcome:
    status: str = "ok"
    reading: Reading | None = field(default_factory=Reading)
    attempts: list[Attempt] = field(default_factory=lambda: [Attempt()])
    next_start_at: int | None = 1


def dev_read(photos, *, categories, spent_this_month_usd, start_at=0, qr_total_cents=None):
    """The dev-mode reader: a plausible receipt after a short pause."""
    time.sleep(1.5)
    return Outcome()


def dev_decode_qr(image: bytes) -> dict | None:
    return None


class SheetError(RuntimeError):
    pass


class FakeSheet:
    """An in-memory Sheet with the GoogleSheet adapter's interface. Row 1 is the header."""

    def __init__(self, categories=("Храна", "Козметика", "Аптека", "Гориво", "Разни"),
                 payment_methods=("Cash", "FIB 0889", "Revolut", "Bulbank 4416",
                                  "Ваучери за храна"),
                 rows=()):
        self._categories = list(categories)
        self._payment_methods = list(payment_methods)
        self.rows: list[list[str]] = [list(SHEET_COLUMNS)] + [list(r) for r in rows]
        self.fail = False  # every call raises SheetError while set
        self.calls: list[tuple] = []

    def _call(self, *what):
        self.calls.append(what)
        if self.fail:
            raise SheetError(f"sheet unavailable: {what[0]}")

    def categories(self) -> list[str]:
        self._call("categories")
        return list(self._categories)

    def payment_methods(self) -> list[str]:
        self._call("payment_methods")
        return list(self._payment_methods)

    def category_for_eik(self, eik: str) -> str | None:
        self._call("category_for_eik", eik)
        b, c = SHEET_COLUMNS.index("БУЛСТАТ"), SHEET_COLUMNS.index("Категория")
        found = Counter(r[c] for r in self.rows[1:] if len(r) > b and r[b] == eik and r[c])
        return found.most_common(1)[0][0] if found else None

    def append(self, data: ReceiptData) -> int:
        self._call("append")
        self.rows.append(data.to_sheet_row())
        return len(self.rows)

    def update(self, row: int, data: ReceiptData) -> None:
        self._call("update", row)
        old, new = self.rows[row - 1], data.to_sheet_row()
        for i, name in enumerate(SHEET_COLUMNS):
            if name in ("Дата", "Категория", "Цена €", "Плащане", "Пояснения", "БУЛСТАТ"):
                old[i] = new[i]

    def delete(self, row: int) -> None:
        self._call("delete", row)
        del self.rows[row - 1]

    def row(self, row: int) -> dict[str, str]:
        self._call("row", row)
        return dict(zip(SHEET_COLUMNS, self.rows[row - 1])) if row <= len(self.rows) else {}
