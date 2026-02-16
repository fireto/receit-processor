"""Configuration: categories, payment methods, column mapping, constants."""

from dataclasses import dataclass
from typing import Optional

VERSION_MAJOR = 1
VERSION_MINOR = 3
VERSION = f"{VERSION_MAJOR}.{VERSION_MINOR}"

# Bulgarian Lev to Euro fixed rate (currency board, pre-2026)
BGN_PER_EUR = 1.95583

CATEGORIES = [
    "Храна",
    "Оборотни стоки",
    "Стоки за дома",
    "Забавления",
    "Козметика",
    "Гориво",
    "Дрехи и обувки",
    "Разходи квартира",
    "Балчик",
    "Варна",
    "Провадия",
    "Подаръци",
    "Техсол",
    "Абонаментни сметки",
    "Кредитни карти",
    "Здравни",
    "Лора",
    "Бебе",
    "Разни",
    "Разходи апартамент",
]

PAYMENT_METHODS = [
    "ВиртуаленPOS",
    "Cash",
    "Diners",
    "ePay",
    "PayPal",
    "RaiCard",
    "Revolut",
    "FIB 0889",
    "Ваучери за храна",
    "ОББ",
    "Bulbank 4416",
]

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
