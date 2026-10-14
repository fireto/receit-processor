"""Receipt reading: vision models with structured output, self-check and a model ladder.

Public interface (the app codes against exactly this):
    read_receipt(photos, *, categories, spent_this_month_usd, start_at=0,
                 qr_total_cents=None) -> ReadOutcome
    decode_qr(image) -> dict | None
    check(reading, qr_total_cents) -> SelfCheck
"""

from backend.reading.check import check
from backend.reading.ladder import read_receipt
from backend.reading.models import (
    Attempt,
    Discount,
    Line,
    Reading,
    ReadOutcome,
    SelfCheck,
)
from backend.reading.qr import decode_qr

__all__ = ["Attempt", "Discount", "Line", "Reading", "ReadOutcome", "SelfCheck", "check",
           "decode_qr", "read_receipt"]
