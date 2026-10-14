"""Plain dataclasses shared by the reading module and its callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["ok", "check", "retake", "failed"]
AttemptOutcome = Literal["ok", "check_failed", "quality", "error"]


@dataclass
class Line:
    name: str
    qty: float
    unit: str  # "pcs" | "kg"
    unit_price_cents: int
    total_cents: int
    discount_cents: int = 0  # positive: the item discount printed under it
    vat_group: str | None = None


@dataclass
class Discount:
    name: str
    cents: int  # positive reduces the total


@dataclass
class Reading:
    date: str  # ISO yyyy-mm-dd
    time: str | None  # "HH:MM"
    total_cents: int
    eik: str | None  # digits only, no "BG"
    store_name: str
    store_address: str | None
    payment: str | None  # "cash" | "card" | None
    card_last4: str | None
    category: str
    notes: str
    lines: list[Line] = field(default_factory=list)
    receipt_discounts: list[Discount] = field(default_factory=list)
    quality: list[str] = field(default_factory=list)  # [] = ok


@dataclass
class SelfCheck:
    ok: bool
    lines_sum_cents: int  # Σ(line total − discount) − Σ receipt discounts
    total_cents: int
    qr_ok: bool | None  # None: no QR total known
    issues: list[str] = field(default_factory=list)


@dataclass
class Attempt:
    provider: str
    model: str
    started_at: str  # ISO UTC
    seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    outcome: AttemptOutcome
    raw: dict | None = None
    error: str | None = None


@dataclass
class ReadOutcome:
    status: Status
    reading: Reading | None
    attempts: list[Attempt]
    next_start_at: int | None
    error: str | None = None  # e.g. "spend cap" when nothing could be read
