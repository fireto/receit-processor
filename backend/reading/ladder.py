"""read_receipt: try the configured models cheapest first until a reading passes the
self-check.

Configuration (environment):
- READING_LADDER  comma-separated `provider:model[@effort]`, tried in order
- READING_BACKUP  one `provider:model[@effort]` tried once when a rung errors ("" = none)
- MONTHLY_CAP_USD no paid call once this month's estimate reaches it (default 2)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.reading.check import check
from backend.reading.models import Attempt, Reading, ReadOutcome, SelfCheck
from backend.reading.pricing import cost_usd
from backend.reading.prompt import build_system
from backend.reading.providers import ModelSpec, ProviderResult, get_provider
from backend.reading.qr import qr_total_cents
from backend.reading.schema import ReadingError, build_schema, to_reading

logger = logging.getLogger(__name__)

DEFAULT_LADDER = "claude:claude-haiku-5-5,claude:claude-sonnet-5-5"
DEFAULT_CAP_USD = 2.0


def ladder_config() -> list[ModelSpec]:
    text = os.environ.get("READING_LADDER") or DEFAULT_LADDER
    return [ModelSpec.parse(item) for item in text.split(",") if item.strip()]


def backup_config() -> ModelSpec | None:
    text = os.environ.get("READING_BACKUP", "").strip()
    return ModelSpec.parse(text) if text else None


def monthly_cap_usd() -> float:
    return float(os.environ.get("MONTHLY_CAP_USD") or DEFAULT_CAP_USD)


@dataclass
class AttemptResult:
    attempt: Attempt
    reading: Reading | None = None
    self_check: SelfCheck | None = None


def run_attempt(spec: ModelSpec, photos: list[bytes], categories: list[str],
                qr_total: int | None) -> AttemptResult:
    """One paid call to one model, converted and self-checked. Never raises."""
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    result: ProviderResult | None = None

    def attempt(outcome, error=None) -> Attempt:
        cost = 0.0
        if result is not None:
            cost = cost_usd(spec.model, result.input_tokens, result.output_tokens,
                            cache_read_tokens=result.cache_read_tokens,
                            cache_write_tokens=result.cache_write_tokens)
        return Attempt(
            provider=spec.provider, model=spec.model, started_at=started_at,
            seconds=round(time.monotonic() - t0, 3),
            input_tokens=result.input_tokens if result else 0,
            output_tokens=result.output_tokens if result else 0,
            cost_usd=cost, outcome=outcome, raw=result.data if result else None, error=error)

    try:
        result = get_provider(spec.provider).read(
            photos, build_system(categories), build_schema(categories),
            model=spec.model, effort=spec.effort)
        reading = to_reading(result.data, categories)
    except ReadingError as e:
        return AttemptResult(attempt("error", f"ReadingError: {e}"))
    except Exception as e:  # noqa: BLE001 — any provider failure → backup / next rung
        logger.warning("reading with %s failed: %s", spec, e)
        return AttemptResult(attempt("error", f"{type(e).__name__}: {e}"))
    if reading.quality:
        return AttemptResult(attempt("quality"), reading)
    self_check = check(reading, qr_total)
    return AttemptResult(attempt("ok" if self_check.ok else "check_failed"), reading, self_check)


def _best(candidates: list[AttemptResult]) -> AttemptResult:
    """Fewest self-check issues, then the smallest gap, then the later (stronger) rung."""
    indexed = list(enumerate(candidates))
    return min(indexed, key=lambda ic: (
        len(ic[1].self_check.issues),
        abs(ic[1].self_check.lines_sum_cents - ic[1].self_check.total_cents),
        -ic[0],
    ))[1]


def read_receipt(photos: list[bytes], *, categories: list[str], spent_this_month_usd: float,
                 start_at: int = 0, qr_total_cents: int | None = None) -> ReadOutcome:
    rungs = ladder_config()
    backup = backup_config()
    cap = monthly_cap_usd()
    qr_total = qr_total_cents if qr_total_cents is not None else _decode_qr_total(photos)

    attempts: list[Attempt] = []
    candidates: list[AttemptResult] = []
    last_tried: int | None = None
    capped = False

    def over_cap() -> bool:
        return spent_this_month_usd + sum(a.cost_usd for a in attempts) >= cap

    def next_start() -> int | None:
        nxt = start_at if last_tried is None else last_tried + 1
        return nxt if nxt < len(rungs) else None

    for index in range(start_at, len(rungs)):
        if over_cap():
            capped = True
            break
        last_tried = index
        res = run_attempt(rungs[index], photos, categories, qr_total)
        attempts.append(res.attempt)
        if res.attempt.outcome == "error" and backup is not None:
            if over_cap():
                capped = True
                break
            res = run_attempt(backup, photos, categories, qr_total)
            attempts.append(res.attempt)
        outcome = res.attempt.outcome
        if outcome == "quality":
            return ReadOutcome("retake", res.reading, attempts, next_start())
        if outcome == "ok":
            return ReadOutcome("ok", res.reading, attempts, next_start())
        if outcome == "check_failed":
            candidates.append(res)

    if capped:
        logger.warning("monthly spend cap %.2f USD reached; not escalating", cap)
    if candidates:
        return ReadOutcome("check", _best(candidates).reading, attempts, next_start())
    return ReadOutcome("failed", None, attempts, next_start(),
                       error="spend cap" if capped else "every provider failed")


def _decode_qr_total(photos: list[bytes]) -> int | None:
    # read_receipt's parameter shadows the module-level qr_total_cents
    return qr_total_cents(photos)
