"""Structured-output schema, prompt and the dict → Reading conversion."""

import pytest

from backend.reading import Discount, Line, Reading
from backend.reading.schema import QUALITY_ISSUES, ReadingError, build_schema, to_reading
from backend.reading.prompt import PROMPT, build_system

CATEGORIES = ["Храна", "Аптека", "Разни"]


def raw(**over):
    data = {
        "date": "2026-10-12",
        "time": "18:42",
        "total_cents": 1234,
        "eik": "BG 131 129 282",
        "store_name": "Кауфланд България ЕООД енд Ко КД",
        "store_address": "София, бул. Цариградско шосе 1",
        "payment": "card",
        "card_last4": "****0889",
        "category": "Храна",
        "notes": "хляб, мляко и банани",
        "lines": [
            {"name": "ХЛЯБ ДОБРУДЖА", "qty": 1, "unit": "pcs", "unit_price_cents": 189,
             "total_cents": 189, "discount_cents": 0, "vat_group": "Б"},
            {"name": "БАНАНИ", "qty": 0.456, "unit": "kg", "unit_price_cents": 599,
             "total_cents": 273, "discount_cents": 0, "vat_group": "Б"},
            {"name": "МЛЯКО 3%", "qty": 2, "unit": "pcs", "unit_price_cents": 436,
             "total_cents": 872, "discount_cents": -50, "vat_group": None},
        ],
        "receipt_discounts": [{"name": "Lidl Plus купон", "cents": 50}],
        "quality": [],
    }
    data.update(over)
    return data


def test_schema_is_strict_and_lists_categories():
    schema = build_schema(CATEGORIES)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["category"]["enum"] == CATEGORIES
    line = schema["properties"]["lines"]["items"]
    assert line["additionalProperties"] is False
    assert set(line["required"]) == set(line["properties"])
    assert line["properties"]["total_cents"]["type"] == "integer"
    assert line["properties"]["unit"]["enum"] == ["pcs", "kg"]
    assert schema["properties"]["quality"]["items"]["enum"] == list(QUALITY_ISSUES)
    assert set(QUALITY_ISSUES) == {"blurry", "cut_top", "cut_bottom", "glare", "not_a_receipt"}


def test_schema_without_categories_takes_any_string():
    schema = build_schema([])
    assert "enum" not in schema["properties"]["category"]


def test_conversion_header():
    r = to_reading(raw(), CATEGORIES)
    assert isinstance(r, Reading)
    assert r.date == "2026-10-12"
    assert r.time == "18:42"
    assert r.total_cents == 1234
    assert r.eik == "131129282"
    assert r.payment == "card"
    assert r.card_last4 == "0889"
    assert r.category == "Храна"
    assert r.notes == "хляб, мляко и банани"
    assert r.quality == []


def test_conversion_lines_kg_and_discounts():
    r = to_reading(raw(), CATEGORIES)
    assert r.lines[1] == Line(name="БАНАНИ", qty=0.456, unit="kg", unit_price_cents=599,
                              total_cents=273, discount_cents=0, vat_group="Б")
    # a discount printed as a negative amount is stored as a positive reduction
    assert r.lines[2].discount_cents == 50
    assert r.lines[2].qty == 2.0 and isinstance(r.lines[2].qty, float)
    assert r.receipt_discounts == [Discount(name="Lidl Plus купон", cents=50)]


def test_cents_stay_exact_integers():
    r = to_reading(raw(total_cents=1999.0), CATEGORIES)
    assert r.total_cents == 1999 and isinstance(r.total_cents, int)
    with pytest.raises(ReadingError):
        to_reading(raw(total_cents=19.99), CATEGORIES)


def test_dates_and_times_are_normalised():
    assert to_reading(raw(date="12.10.2026"), CATEGORIES).date == "2026-10-12"
    assert to_reading(raw(time="18:42:07"), CATEGORIES).time == "18:42"
    assert to_reading(raw(time=""), CATEGORIES).time is None
    with pytest.raises(ReadingError):
        to_reading(raw(date="вчера"), CATEGORIES)


def test_payment_card_and_eik_edge_cases():
    r = to_reading(raw(payment="unknown", card_last4="08", eik="", store_address=""), CATEGORIES)
    assert r.payment is None
    assert r.card_last4 is None
    assert r.eik is None
    assert r.store_address is None


def test_unknown_category_falls_back_to_razni():
    assert to_reading(raw(category="Гориво"), CATEGORIES).category == "Разни"


def test_quality_issues_are_kept_and_deduplicated():
    r = to_reading(raw(quality=["blurry", "blurry", "glare", "ok"]), CATEGORIES)
    assert r.quality == ["blurry", "glare"]


def test_bad_shape_raises():
    with pytest.raises(ReadingError):
        to_reading({"date": "2026-10-12"}, CATEGORIES)


def test_prompt_is_stable_and_categories_come_after_it():
    system = build_system(CATEGORIES)
    assert system[0] == PROMPT
    assert "Храна" not in PROMPT
    assert "Храна" in system[1]
    for must in ["ЕИК", "ЗДДС", "Kaufland Card", "ОТСТЪПКА", "кг", "****0889", "Bulgarian"]:
        assert must in PROMPT
