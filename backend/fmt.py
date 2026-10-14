"""Bulgarian display formats: 12,34 €, 14.10.2026, 0,535 кг."""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

SOFIA = ZoneInfo("Europe/Sofia")
_AMOUNT = re.compile(r"^(\d+)(?:[.,](\d{1,2}))?$")


def eur(cents: int | None) -> str:
    if cents is None:
        return "—"
    sign = "−" if cents < 0 else ""
    return f"{sign}{abs(cents) / 100:.2f}".replace(".", ",") + " €"


def amount(cents: int | None) -> str:
    """For an input field: 12,34."""
    return "" if cents is None else f"{cents / 100:.2f}".replace(".", ",")


def parse_cents(text: str) -> int | None:
    """'12,34' / '12.34' / '1 234,5' → cents; None when it is not a plain amount."""
    m = _AMOUNT.match(re.sub(r"\s", "", text or ""))
    if not m:
        return None
    return int(m[1]) * 100 + int((m[2] or "0").ljust(2, "0"))


def bgdate(value: str | date | None) -> str:
    if not value:
        return ""
    return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")


def local(stamp: str | None) -> str:
    """A UTC ISO stamp as Sofia 'dd.mm HH:MM'."""
    if not stamp:
        return ""
    return datetime.fromisoformat(stamp).astimezone(SOFIA).strftime("%d.%m %H:%M")


def qty(value: float | None, unit: str | None) -> str:
    if value is None:
        return ""
    label = {"kg": "кг", "pcs": "бр"}.get(unit or "pcs", "")
    return f"{value:g}".replace(".", ",") + (f" {label}" if label else "")
