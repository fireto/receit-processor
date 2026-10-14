"""Scoring a model's readings against the expected values (DESIGN.md "Model comparison").

A model is reliable when: header fields (date, total, ЕИК, payment) exact on 100 % of the
readable receipts; ≥ 98 % of lines with the right price and quantity and a recognisable
name; the self-check passes on ≥ 90 % of the receipts; every bad photo is flagged; median
< 30 s per receipt.
"""

from __future__ import annotations

import difflib
import re
import statistics
from dataclasses import dataclass, field

from backend.reading.models import Line, Reading, SelfCheck
from backend.reading.schema import normalise_eik

NAME_SIMILARITY = 0.6
HEADER_FIELDS = ("date", "total", "eik", "payment")
MIN_LINE_ACCURACY = 0.98
MIN_SELF_CHECK_RATE = 0.90
MAX_MEDIAN_SECONDS = 30.0


@dataclass
class ReceiptScore:
    receipt: str
    model: str
    readable: bool | None  # None: no expected values
    header: dict[str, bool] | None  # None: not scored
    lines_ok: int
    lines_denominator: int  # max(expected lines, read lines)
    self_check_ok: bool
    quality_flagged: bool
    seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name).casefold())


def name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio()


def _amounts_equal(expected: dict, actual: Line) -> bool:
    return (abs(float(expected["qty"]) - actual.qty) < 0.0005
            and int(expected["unit_price_cents"]) == actual.unit_price_cents
            and int(expected["total_cents"]) == actual.total_cents
            and abs(int(expected.get("discount_cents") or 0)) == actual.discount_cents)


def count_ok_lines(expected: list[dict], actual: list[Line]) -> int:
    """Greedy in printed order: each expected line takes the most similar unused line."""
    used: set[int] = set()
    ok = 0
    for exp in expected:
        best, best_key = None, None
        for j, act in enumerate(actual):
            if j in used:
                continue
            sim = name_similarity(exp["name"], act.name)
            if sim < NAME_SIMILARITY:
                continue
            key = (_amounts_equal(exp, act), sim)
            if best_key is None or key > best_key:
                best, best_key = j, key
        if best is not None:
            used.add(best)
            ok += best_key[0]
    return ok


def score_receipt(receipt: str, model: str, expected: dict | None, reading: Reading | None,
                  self_check: SelfCheck | None, *, seconds: float, input_tokens: int,
                  output_tokens: int, cost_usd: float, error: str | None) -> ReceiptScore:
    expected = expected or {}
    has_values = any(k in expected for k in ("total_cents", "date", "lines"))
    bad = bool(expected.get("quality"))
    readable = False if bad else (True if has_values else None)
    header = None
    lines_ok = lines_den = 0
    if readable:
        exp_lines = expected.get("lines") or []
        if reading is None:
            header = {f: False for f in HEADER_FIELDS}
            lines_den = len(exp_lines)
        else:
            header = {
                "date": reading.date == expected.get("date"),
                "total": reading.total_cents == expected.get("total_cents"),
                "eik": reading.eik == normalise_eik(expected.get("eik")),
                "payment": reading.payment == expected.get("payment"),
            }
            lines_ok = count_ok_lines(exp_lines, reading.lines)
            lines_den = max(len(exp_lines), len(reading.lines))
    return ReceiptScore(
        receipt=receipt, model=model, readable=readable, header=header, lines_ok=lines_ok,
        lines_denominator=lines_den, self_check_ok=bool(self_check and self_check.ok),
        quality_flagged=bool(reading and reading.quality), seconds=seconds,
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd, error=error)


@dataclass
class ModelSummary:
    model: str
    receipts: int
    errors: int
    header_accuracy: float | None
    field_accuracy: dict[str, float]
    line_accuracy: float | None
    self_check_rate: float | None
    bad_flagged: tuple[int, int]
    false_flags: int
    median_seconds: float
    max_seconds: float
    input_tokens: int
    output_tokens: int
    cost_total: float
    cost_per_receipt: float
    reliable: bool | None  # None: no expected values to judge by
    reasons: list[str] = field(default_factory=list)


def _pct(x: float) -> str:
    return f"{x * 100:.1f} %"


def summarise(model: str, scores: list[ReceiptScore]) -> ModelSummary:
    readable = [s for s in scores if s.readable]
    not_bad = [s for s in scores if s.readable is not False]
    bad = [s for s in scores if s.readable is False]
    seconds = [s.seconds for s in scores] or [0.0]

    header_acc = (sum(all(s.header.values()) for s in readable) / len(readable)
                  if readable else None)
    field_acc = ({f: sum(s.header[f] for s in readable) / len(readable) for f in HEADER_FIELDS}
                 if readable else {})
    den = sum(s.lines_denominator for s in readable)
    line_acc = sum(s.lines_ok for s in readable) / den if den else None
    check_rate = sum(s.self_check_ok for s in not_bad) / len(not_bad) if not_bad else None
    flagged = (sum(s.quality_flagged for s in bad), len(bad))
    median = statistics.median(seconds)
    cost_total = sum(s.cost_usd for s in scores)

    reasons = []
    if header_acc is not None and header_acc < 1.0:
        reasons.append(f"header fields exact on {_pct(header_acc)} (needs 100 %)")
    if line_acc is not None and line_acc < MIN_LINE_ACCURACY:
        reasons.append(f"lines right on {_pct(line_acc)} (needs ≥ 98 %)")
    if check_rate is not None and check_rate < MIN_SELF_CHECK_RATE:
        reasons.append(f"self-check passes on {_pct(check_rate)} (needs ≥ 90 %)")
    if flagged[0] < flagged[1]:
        reasons.append(f"bad photos flagged {flagged[0]}/{flagged[1]}")
    if median >= MAX_MEDIAN_SECONDS:
        reasons.append(f"median {median:.1f} seconds (needs < 30)")

    return ModelSummary(
        model=model, receipts=len(scores), errors=sum(1 for s in scores if s.error),
        header_accuracy=header_acc, field_accuracy=field_acc, line_accuracy=line_acc,
        self_check_rate=check_rate, bad_flagged=flagged,
        false_flags=sum(s.quality_flagged for s in readable), median_seconds=median,
        max_seconds=max(seconds), input_tokens=sum(s.input_tokens for s in scores),
        output_tokens=sum(s.output_tokens for s in scores), cost_total=cost_total,
        cost_per_receipt=cost_total / len(scores) if scores else 0.0,
        reliable=(not reasons) if readable else None, reasons=reasons)


def cheapest_reliable(summaries: list[ModelSummary]) -> ModelSummary | None:
    reliable = [s for s in summaries if s.reliable]
    return min(reliable, key=lambda s: s.cost_total) if reliable else None
