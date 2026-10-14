"""Self-check: the lines add up to the total, and the QR total agrees."""

from backend.reading import Discount, Line, Reading
from backend.reading.check import check


def reading(total, lines, discounts=()):
    return Reading(
        date="2026-10-12", time=None, total_cents=total, eik=None, store_name="X",
        store_address=None, payment=None, card_last4=None, category="Храна", notes="",
        lines=[Line(name=f"L{i}", qty=1, unit="pcs", unit_price_cents=t, total_cents=t,
                    discount_cents=d) for i, (t, d) in enumerate(lines)],
        receipt_discounts=[Discount(name="D", cents=c) for c in discounts],
    )


def test_exact_sum_passes():
    sc = check(reading(1000, [(600, 0), (500, 100)]), None)
    assert sc.ok and sc.lines_sum_cents == 1000 and sc.total_cents == 1000
    assert sc.qr_ok is None and sc.issues == []


def test_one_cent_off_passes_two_cents_fail():
    assert check(reading(1001, [(1000, 0)]), None).ok
    assert check(reading(999, [(1000, 0)]), None).ok
    sc = check(reading(1002, [(1000, 0)]), None)
    assert not sc.ok and sc.issues


def test_receipt_discounts_are_subtracted():
    assert check(reading(900, [(1000, 0)], [100]), None).ok
    # a negative rounding raises the total
    assert check(reading(1002, [(1000, 0)], [-2]), None).ok
    assert not check(reading(1000, [(1000, 0)], [100]), None).ok


def test_qr_total_must_equal_total():
    sc = check(reading(1000, [(1000, 0)]), 1000)
    assert sc.ok and sc.qr_ok is True
    sc = check(reading(1000, [(1000, 0)]), 1001)
    assert not sc.ok and sc.qr_ok is False
    assert any("QR" in i for i in sc.issues)


def test_no_lines_fails():
    sc = check(reading(1000, []), None)
    assert not sc.ok
