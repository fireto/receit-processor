"""Self-check of a reading: the amounts must add up and agree with the fiscal QR code."""

from backend.reading.models import Reading, SelfCheck

TOLERANCE_CENTS = 1


def _eur(cents: int) -> str:
    return f"{cents / 100:.2f}"


def check(reading: Reading, qr_total_cents: int | None) -> SelfCheck:
    lines_sum = sum(line.total_cents - line.discount_cents for line in reading.lines) - sum(
        d.cents for d in reading.receipt_discounts
    )
    issues = []
    if abs(lines_sum - reading.total_cents) > TOLERANCE_CENTS:
        issues.append(
            f"lines sum {_eur(lines_sum)} != total {_eur(reading.total_cents)}"
        )
    qr_ok = None
    if qr_total_cents is not None:
        qr_ok = qr_total_cents == reading.total_cents
        if not qr_ok:
            issues.append(f"QR total {_eur(qr_total_cents)} != total {_eur(reading.total_cents)}")
    return SelfCheck(ok=not issues, lines_sum_cents=lines_sum, total_cents=reading.total_cents,
                     qr_ok=qr_ok, issues=issues)
