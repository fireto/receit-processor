import pytest

from backend import fmt


def test_eur():
    assert fmt.eur(1234) == "12,34 €"
    assert fmt.eur(5) == "0,05 €"
    assert fmt.eur(-50) == "−0,50 €"
    assert fmt.eur(None) == "—"


@pytest.mark.parametrize("text,cents", [("12,34", 1234), ("12.34", 1234), (" 7 ", 700),
                                        ("0,5", 50), ("1 234,50", 123450), ("12,345", None),
                                        ("", None), ("abc", None), ("-3", None)])
def test_parse_cents(text, cents):
    assert fmt.parse_cents(text) == cents


def test_amount_for_an_input_field():
    assert fmt.amount(1234) == "12,34"
    assert fmt.amount(None) == ""


def test_bgdate():
    assert fmt.bgdate("2026-10-14") == "14.10.2026"
    assert fmt.bgdate(None) == ""


def test_local_time_of_a_utc_stamp():
    assert fmt.local("2026-10-14T16:05:00+00:00") == "14.10 19:05"


def test_qty():
    assert fmt.qty(1.0, "pcs") == "1 бр"
    assert fmt.qty(0.535, "kg") == "0,535 кг"
    assert fmt.qty(None, None) == ""
