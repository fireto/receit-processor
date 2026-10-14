"""The structured-output JSON schema and its conversion to a Reading.

Amounts are integer euro cents in the schema itself, so no float ever carries money.
The schema sticks to the subset every provider enforces: object/array/string/integer/
number, `enum`, nullable via a type array, every property required,
`additionalProperties: false`.
"""

from __future__ import annotations

import re
from datetime import datetime

from backend.reading.models import Discount, Line, Reading

QUALITY_ISSUES = ("blurry", "cut_top", "cut_bottom", "glare", "not_a_receipt")
DEFAULT_CATEGORY = "Разни"


class ReadingError(ValueError):
    """The model's output could not be turned into a Reading."""


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_NSTR = {"type": ["string", "null"]}
_INT = {"type": "integer"}


def build_schema(categories: list[str]) -> dict:
    category = {"type": "string"}
    if categories:
        category["enum"] = list(categories)
    line = _obj({
        "name": _STR,
        "qty": {"type": "number"},
        "unit": {"type": "string", "enum": ["pcs", "kg"]},
        "unit_price_cents": _INT,
        "total_cents": _INT,
        "discount_cents": _INT,
        "vat_group": _NSTR,
    })
    return _obj({
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "time": {"type": ["string", "null"], "description": "HH:MM"},
        "total_cents": _INT,
        "eik": _NSTR,
        "store_name": _STR,
        "store_address": _NSTR,
        "payment": {"type": "string", "enum": ["cash", "card", "unknown"]},
        "card_last4": _NSTR,
        "category": category,
        "notes": _STR,
        "lines": {"type": "array", "items": line},
        "receipt_discounts": {"type": "array", "items": _obj({"name": _STR, "cents": _INT})},
        "quality": {"type": "array", "items": {"type": "string", "enum": list(QUALITY_ISSUES)}},
    })


def _cents(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReadingError(f"{what}: not a number of cents: {value!r}")
    if isinstance(value, float):
        if not value.is_integer():
            raise ReadingError(f"{what}: cents must be whole: {value!r}")
        return int(value)
    return value


def _date(value) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ReadingError(f"date: unreadable {value!r}")


def _time(value) -> str | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def normalise_eik(value) -> str | None:
    """ЕИК/ЗДДС → digits only, without the "BG" prefix (as the PoC did)."""
    if not value:
        return None
    text = re.sub(r"^BG\s*", "", str(value).strip(), flags=re.IGNORECASE)
    return re.sub(r"\D", "", text) or None


def _card_last4(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else None


def _category(value, categories: list[str]) -> str:
    value = str(value or "").strip()
    if not categories or value in categories:
        return value
    return DEFAULT_CATEGORY if DEFAULT_CATEGORY in categories else categories[0]


def to_reading(data: dict, categories: list[str]) -> Reading:
    """Convert the provider's structured output to a Reading (raises ReadingError)."""
    try:
        lines = [
            Line(
                name=str(item["name"]).strip(),
                qty=float(item["qty"]),
                unit="kg" if item["unit"] == "kg" else "pcs",
                unit_price_cents=_cents(item["unit_price_cents"], "unit_price_cents"),
                total_cents=_cents(item["total_cents"], "line total_cents"),
                discount_cents=abs(_cents(item.get("discount_cents") or 0, "discount_cents")),
                vat_group=_text(item.get("vat_group")),
            )
            for item in data["lines"]
        ]
        discounts = [
            Discount(name=str(d["name"]).strip(), cents=_cents(d["cents"], "discount cents"))
            for d in data["receipt_discounts"]
        ]
        quality: list[str] = []
        for issue in data.get("quality") or []:
            if issue in QUALITY_ISSUES and issue not in quality:
                quality.append(issue)
        payment = data.get("payment")
        return Reading(
            date=_date(data["date"]),
            time=_time(data.get("time")),
            total_cents=_cents(data["total_cents"], "total_cents"),
            eik=normalise_eik(data.get("eik")),
            store_name=str(data.get("store_name") or "").strip(),
            store_address=_text(data.get("store_address")),
            payment=payment if payment in ("cash", "card") else None,
            card_last4=_card_last4(data.get("card_last4")),
            category=_category(data.get("category"), categories),
            notes=str(data.get("notes") or "").strip(),
            lines=lines,
            receipt_discounts=discounts,
            quality=quality,
        )
    except ReadingError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ReadingError(f"unexpected output shape: {e!r}") from e

