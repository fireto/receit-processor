"""Configuration: column mapping, constants."""

from dataclasses import dataclass
from typing import Optional

VERSION_MAJOR = 1
VERSION_MINOR = 4
VERSION_PATCH = 1
VERSION = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}"

# Bulgarian Lev to Euro fixed rate (currency board, pre-2026)
BGN_PER_EUR = 1.95583

# Default fallback category (must exist in the Категории named range)
DEFAULT_CATEGORY = "Разни"

# Google Sheet column order
SHEET_COLUMNS = [
    "Дата",
    "Категория",
    "Цена лв",
    "Цена €",
    "GGBG лв",
    "Плащане",
    "Допълн. такса",
    "Payback",
    "Пояснения",
    "БУЛСТАТ",
]


@dataclass
class ReceiptData:
    date: str  # DD.MM.YYYY
    total_eur: float
    category: str
    payment_method: Optional[str] = None
    notes: str = ""
    bulstat: Optional[str] = None
    card_last4: Optional[str] = None  # not stored in sheet, used for matching

    @property
    def total_bgn(self) -> float:
        return round(self.total_eur * BGN_PER_EUR, 2)

    def to_sheet_row(self) -> list:
        """Format as a list matching SHEET_COLUMNS order."""
        return [
            self.date,
            self.category,
            f"{self.total_bgn:.2f}".replace(".", ","),
            f"{self.total_eur:.2f}".replace(".", ","),
            "",  # GGBG лв — filled manually
            self.payment_method or "",
            "",  # Допълн. такса
            "",  # Payback
            self.notes,
            self.bulstat or "",
        ]
