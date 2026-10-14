"""The household Google Sheet as the app sees it: one injectable object.

`GoogleSheet` wraps backend.sheets (gspread); `backend.fakes.FakeSheet` has the same
methods for tests and local runs (RECEIPTS_SHEET=fake). The named ranges Категории and
ПлатежноСредство are cached for CACHE_SECONDS: every reading and every receipt page needs
them.
"""

import os
import threading
import time
from collections.abc import Callable

from backend import sheets
from backend.config import ReceiptData

CACHE_SECONDS = 600


class GoogleSheet:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: str, load: Callable[[], list[str]]) -> list[str]:
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < CACHE_SECONDS:
                return list(hit[1])
        values = load()
        with self._lock:
            self._cache[key] = (self._clock(), values)
        return list(values)

    def categories(self) -> list[str]:
        return self._cached("categories", sheets.get_categories)

    def payment_methods(self) -> list[str]:
        return self._cached("payment_methods", sheets.get_payment_methods)

    def category_for_eik(self, eik: str) -> str | None:
        return sheets.lookup_category_by_bulstat(eik)

    def append(self, data: ReceiptData) -> int:
        return sheets.append_expense(data)

    def update(self, row: int, data: ReceiptData) -> None:
        sheets.update_expense(row, data)

    def delete(self, row: int) -> None:
        sheets.delete_row(row)

    def row(self, row: int) -> dict[str, str]:
        return sheets.get_row(row)


def make_sheet():
    if os.environ.get("RECEIPTS_SHEET") == "fake":
        from backend.fakes import FakeSheet
        return FakeSheet()
    return GoogleSheet()
