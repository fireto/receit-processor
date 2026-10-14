"""The receipt store: queue, reading results, edits and the Sheet row.

Statuses: queued → reading → ok | check | retake | failed. A receipt is written to the
Sheet when it is `ok` (read and self-checked, or confirmed by hand); `sheet_written_at`
NULL on an `ok` receipt means a write or an update is pending (the worker retries it).
Every Sheet mutation runs under SHEET_LOCK: deleting a row shifts the rows below it, and
the stored `sheet_row`s are shifted with it.
"""

import json
import logging
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend import photos as photo_store
from backend.config import DEFAULT_CATEGORY, SHEET_COLUMNS, ReceiptData

log = logging.getLogger(__name__)

SOFIA = ZoneInfo("Europe/Sofia")
STATUSES = ("queued", "reading", "ok", "check", "retake", "failed")
OUTCOME_STATUSES = ("ok", "check", "retake", "failed")
ATTEMPT_OUTCOMES = ("ok", "check_failed", "quality", "error")
MAX_PHOTOS = 3
SHEET_LOCK = threading.Lock()
CASH_WORDS = ("cash", "брой", "кеш")


class SheetUnavailable(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


# --- creating and queueing -------------------------------------------------------------

def _store_photos(conn, photos_dir: Path, rid: int, photos: list) -> None:
    for seq, p in enumerate(photos, start=1):
        rel = photo_store.store(photos_dir, rid, seq, p)
        conn.execute("INSERT INTO receipt_photo (receipt_id, seq, path, width, height) "
                     "VALUES (?, ?, ?, ?, ?)", (rid, seq, rel, p.width, p.height))


def create(conn, photos_dir: Path, source: str, photos: list,
           now: datetime | None = None) -> int:
    """A new receipt from 1–3 processed photos, queued for reading."""
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise ValueError(f"a receipt has 1 to {MAX_PHOTOS} photos")
    stamp = iso(now or utcnow())
    with conn:
        rid = conn.execute(
            "INSERT INTO receipt (created_at, status, source, queued_at) "
            "VALUES (?, 'queued', ?, ?)", (stamp, source, stamp)).lastrowid
        _store_photos(conn, photos_dir, rid, photos)
    return rid


def replace_photos(conn, photos_dir: Path, rid: int, photos: list,
                   now: datetime | None = None) -> None:
    """Re-take: new photos for the same receipt (same id, same Sheet row), read again
    from the first model."""
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise ValueError(f"a receipt has 1 to {MAX_PHOTOS} photos")
    photo_store.remove(photos_dir, rid)
    with conn:
        conn.execute("DELETE FROM receipt_photo WHERE receipt_id = ?", (rid,))
        _store_photos(conn, photos_dir, rid, photos)
        _queue(conn, rid, 0, now)


def _queue(conn, rid: int, start_at: int, now: datetime | None) -> None:
    conn.execute("UPDATE receipt SET status = 'queued', start_at = ?, queued_at = ? "
                 "WHERE id = ?", (start_at, iso(now or utcnow()), rid))


def reread(conn, rid: int, now: datetime | None = None) -> bool:
    """Queue for the next model up the ladder; False when there is none."""
    r = conn.execute("SELECT next_start_at, status FROM receipt WHERE id = ?",
                     (rid,)).fetchone()
    if r is None or r["next_start_at"] is None or r["status"] in ("queued", "reading"):
        return False
    with conn:
        _queue(conn, rid, r["next_start_at"], now)
    return True


def retry(conn, rid: int, now: datetime | None = None) -> bool:
    """Queue a failed receipt again at the same ladder step."""
    r = conn.execute("SELECT start_at, status FROM receipt WHERE id = ?", (rid,)).fetchone()
    if r is None or r["status"] != "failed":
        return False
    with conn:
        _queue(conn, rid, r["start_at"], now)
    return True


def claim_next(conn) -> sqlite3.Row | None:
    """The oldest queued receipt, marked `reading`."""
    with conn:
        return conn.execute(
            "UPDATE receipt SET status = 'reading' WHERE id = ("
            "  SELECT id FROM receipt WHERE status = 'queued' ORDER BY queued_at, id LIMIT 1"
            ") RETURNING *").fetchone()


def reset_interrupted(conn) -> None:
    """At startup: a reading cut short by a restart is queued again."""
    with conn:
        conn.execute("UPDATE receipt SET status = 'queued' WHERE status = 'reading'")


def photo_bytes(conn, photos_dir: Path, rid: int) -> list[bytes]:
    rows = conn.execute("SELECT path FROM receipt_photo WHERE receipt_id = ? ORDER BY seq",
                        (rid,)).fetchall()
    return [(Path(photos_dir) / r["path"]).read_bytes() for r in rows]


# --- reading results -------------------------------------------------------------------

def _attempt_row(rid, a) -> tuple:
    outcome = getattr(a, "outcome", "error")
    raw = getattr(a, "raw", None)
    return (rid, getattr(a, "provider", "?"), getattr(a, "model", "?"),
            getattr(a, "started_at", None) or iso(utcnow()), getattr(a, "seconds", None),
            getattr(a, "input_tokens", None), getattr(a, "output_tokens", None),
            getattr(a, "cost_usd", None),
            outcome if outcome in ATTEMPT_OUTCOMES else "error",
            json.dumps(raw, ensure_ascii=False) if raw is not None else None,
            getattr(a, "error", None))


def record_attempts(conn, rid: int, attempts) -> None:
    conn.executemany(
        "INSERT INTO reading (receipt_id, provider, model, started_at, seconds, input_tokens, "
        "output_tokens, cost_usd, outcome, raw_json, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [_attempt_row(rid, a) for a in attempts])


def self_check(total_cents, lines, discounts) -> dict:
    """Σ(line totals − line discounts) − receipt discounts vs the total (±1 cent)."""
    lines_cents = sum((_get(x, "total_cents") or 0) - (_get(x, "discount_cents") or 0)
                      for x in lines)
    discounts_cents = sum(_get(d, "cents") or 0 for d in discounts)
    diff = None if total_cents is None else lines_cents - discounts_cents - total_cents
    return {"lines_cents": lines_cents, "discounts_cents": discounts_cents,
            "total_cents": total_cents, "diff_cents": diff,
            "ok": bool(lines) and diff is not None and abs(diff) <= 1}


def _get(obj, name):
    return obj[name] if isinstance(obj, (dict, sqlite3.Row)) else getattr(obj, name, None)


def store_outcome(conn, rid: int, outcome, qr: dict | None = None) -> str:
    """Store a reader outcome: every attempt, the reading (if any) and the status."""
    status = getattr(outcome, "status", "failed")
    if status not in OUTCOME_STATUSES:
        status = "failed"
    reading = getattr(outcome, "reading", None)
    with conn:
        record_attempts(conn, rid, getattr(outcome, "attempts", None) or [])
        if reading is not None:
            _store_reading(conn, rid, reading)
        conn.execute(
            "UPDATE receipt SET status = ?, next_start_at = ?, qr_json = coalesce(?, qr_json),"
            " sheet_written_at = CASE WHEN ? = 'ok' THEN NULL ELSE sheet_written_at END "
            "WHERE id = ?",
            (status, getattr(outcome, "next_start_at", None),
             json.dumps(qr, ensure_ascii=False) if qr else None, status, rid))
    return status


def _store_reading(conn, rid: int, r) -> None:
    payment = r.payment if r.payment in ("cash", "card") else None
    lines = list(r.lines or [])
    discounts = list(r.receipt_discounts or [])
    conn.execute(
        "UPDATE receipt SET date = ?, time = ?, total_cents = ?, eik = ?, store_name = ?, "
        "store_address = ?, payment = ?, card_last4 = ?, category = ?, notes = ?, "
        "quality_json = ?, self_check_json = ? WHERE id = ?",
        (r.date, r.time, r.total_cents, r.eik, r.store_name, r.store_address, payment,
         r.card_last4, r.category, r.notes, json.dumps(list(r.quality or []), ensure_ascii=False),
         json.dumps(self_check(r.total_cents, lines, discounts)), rid))
    conn.execute("DELETE FROM receipt_line WHERE receipt_id = ?", (rid,))
    conn.execute("DELETE FROM receipt_discount WHERE receipt_id = ?", (rid,))
    conn.executemany(
        "INSERT INTO receipt_line (receipt_id, seq, name, qty, unit, unit_price_cents, "
        "total_cents, discount_cents, vat_group) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(rid, i, x.name, x.qty, x.unit if x.unit in ("pcs", "kg") else None,
          x.unit_price_cents, x.total_cents, x.discount_cents or 0, x.vat_group)
         for i, x in enumerate(lines, start=1)])
    conn.executemany(
        "INSERT INTO receipt_discount (receipt_id, seq, name, cents) VALUES (?, ?, ?, ?)",
        [(rid, i, d.name, d.cents) for i, d in enumerate(discounts, start=1)])


def apply_history(conn, sheet, rid: int) -> None:
    """Card digits → payment method; ЕИК → the category used before (when the model fell
    back to the default one). Ported from the PoC; a Sheet error only skips it."""
    r = get_row(conn, rid)
    method, category = None, r["category"]
    try:
        methods = sheet.payment_methods()
        if r["card_last4"]:
            method = next((m for m in methods if r["card_last4"] in m), None)
        elif r["payment"] == "cash":
            method = next((m for m in methods
                           if any(w in m.lower() for w in CASH_WORDS)), None)
    except Exception:
        log.warning("receipt %s: payment methods unavailable", rid, exc_info=True)
    if r["eik"] and (not category or category == DEFAULT_CATEGORY):
        try:
            category = sheet.category_for_eik(r["eik"]) or category
        except Exception:
            log.warning("receipt %s: ЕИК history unavailable", rid, exc_info=True)
    with conn:
        conn.execute("UPDATE receipt SET payment_method = ?, category = ? WHERE id = ?",
                     (method, category or DEFAULT_CATEGORY, rid))


# --- spend -----------------------------------------------------------------------------

def spent_this_month_usd(conn, now: datetime | None = None) -> float:
    local = (now or utcnow()).astimezone(SOFIA)
    start = datetime(local.year, local.month, 1, tzinfo=SOFIA)
    return conn.execute("SELECT coalesce(sum(cost_usd), 0) FROM reading WHERE started_at >= ?",
                        (iso(start),)).fetchone()[0]


# --- reading back ------------------------------------------------------------------------

def get_row(conn, rid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM receipt WHERE id = ?", (rid,)).fetchone()


def get(conn, rid: int) -> dict | None:
    r = get_row(conn, rid)
    if r is None:
        return None
    q = lambda sql: conn.execute(sql, (rid,)).fetchall()  # noqa: E731
    lines = q("SELECT * FROM receipt_line WHERE receipt_id = ? ORDER BY seq")
    discounts = q("SELECT * FROM receipt_discount WHERE receipt_id = ? ORDER BY seq")
    return {
        "receipt": r,
        "photos": q("SELECT * FROM receipt_photo WHERE receipt_id = ? ORDER BY seq"),
        "lines": lines,
        "discounts": discounts,
        "readings": q("SELECT * FROM reading WHERE receipt_id = ? ORDER BY id"),
        "check": self_check(r["total_cents"], lines, discounts) if lines else None,
        "quality": json.loads(r["quality_json"]) if r["quality_json"] else [],
        "qr": json.loads(r["qr_json"]) if r["qr_json"] else None,
    }


def recent(conn, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT r.*, (SELECT p.seq FROM receipt_photo p WHERE p.receipt_id = r.id "
        "ORDER BY p.seq LIMIT 1) AS first_photo FROM receipt r "
        "ORDER BY r.created_at DESC, r.id DESC LIMIT ?", (limit,)).fetchall()


def busy(conn) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM receipt "
                        "WHERE status IN ('queued', 'reading'))").fetchone()[0] == 1


def pending_sheet_ids(conn) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT id FROM receipt WHERE status = 'ok' AND sheet_written_at IS NULL ORDER BY id")]


# --- edits -------------------------------------------------------------------------------

def update_header(conn, sheet, rid: int, *, date: str | None, total_cents: int | None,
                  category: str | None, payment_method: str | None, notes: str | None,
                  now: datetime | None = None) -> bool:
    """Save the editable fields; a written receipt's row is updated. Returns whether the
    Sheet is in sync (False: the update is pending and will be retried)."""
    with conn:
        conn.execute(
            "UPDATE receipt SET date = ?, total_cents = ?, category = ?, payment_method = ?, "
            "notes = ?, sheet_written_at = CASE WHEN status = 'ok' THEN NULL "
            "ELSE sheet_written_at END WHERE id = ?",
            (date or None, total_cents, category or None, payment_method or None,
             notes or None, rid))
    return sync_sheet(conn, sheet, rid, now)


def confirm(conn, sheet, rid: int, now: datetime | None = None) -> bool:
    """A `check` receipt accepted as it is (or as edited): ok, and written."""
    with conn:
        changed = conn.execute(
            "UPDATE receipt SET status = 'ok', confirmed_at = ?, sheet_written_at = NULL "
            "WHERE id = ? AND status = 'check'", (iso(now or utcnow()), rid)).rowcount
    return bool(changed) and sync_sheet(conn, sheet, rid, now)


def create_manual(conn, sheet, *, date: str, total_cents: int, category: str,
                  payment_method: str | None, notes: str | None,
                  now: datetime | None = None) -> int:
    """An expense without a receipt: stored as a `manual` receipt and written at once."""
    stamp = iso(now or utcnow())
    with conn:
        rid = conn.execute(
            "INSERT INTO receipt (created_at, status, source, date, total_cents, category, "
            "payment_method, notes, confirmed_at) VALUES (?, 'ok', 'manual', ?, ?, ?, ?, ?, ?)",
            (stamp, date, total_cents, category, payment_method or None, notes or None,
             stamp)).lastrowid
    sync_sheet(conn, sheet, rid, now)
    return rid


# --- the Sheet ---------------------------------------------------------------------------

def sheet_data(r: sqlite3.Row) -> ReceiptData:
    day = date.fromisoformat(r["date"]) if r["date"] else \
        datetime.fromisoformat(r["created_at"]).astimezone(SOFIA).date()
    return ReceiptData(date=day.strftime("%d.%m.%Y"), total_eur=(r["total_cents"] or 0) / 100,
                       category=r["category"] or DEFAULT_CATEGORY,
                       payment_method=r["payment_method"], notes=r["notes"] or "",
                       bulstat=r["eik"])


def sync_sheet(conn, sheet, rid: int, now: datetime | None = None) -> bool:
    """Write (append or update) an `ok` receipt's row if it is pending. True when the
    row is in sync afterwards."""
    with SHEET_LOCK:
        r = get_row(conn, rid)
        if r is None or r["status"] != "ok":
            return False
        if r["sheet_written_at"] is not None:
            return True
        data = sheet_data(r)
        try:
            row = r["sheet_row"]
            if row:
                sheet.update(row, data)
            else:
                row = sheet.append(data)
        except Exception:
            log.warning("receipt %s: Sheet write failed, will retry", rid, exc_info=True)
            return False
        with conn:
            conn.execute("UPDATE receipt SET sheet_row = ?, sheet_written_at = ? WHERE id = ?",
                         (row, iso(now or utcnow()), rid))
        log.info("receipt %s: Sheet row %s written", rid, row)
        return True


def delete(conn, sheet, photos_dir: Path, rid: int) -> str | None:
    """Delete the receipt, its photos and its Sheet row. A row that no longer shows what
    the app wrote (edited by hand, or rows moved) is left alone: returns a warning.
    Raises SheetUnavailable (and deletes nothing) when the Sheet cannot be reached."""
    warning = None
    with SHEET_LOCK:
        r = get_row(conn, rid)
        if r is None:
            return None
        row = r["sheet_row"]
        if row:
            written = dict(zip(SHEET_COLUMNS, sheet_data(r).to_sheet_row()))
            expected = {k: written[k] for k in ("Дата", "Цена €")}
            try:
                found = sheet.row(row)
                if all(found.get(k) == v for k, v in expected.items()):
                    sheet.delete(row)
                else:
                    log.warning("receipt %s: Sheet row %s is %s, expected %s — kept",
                                rid, row, found, expected)
                    warning = (f"Редът в таблицата (ред {row}) не съвпада с бележката и "
                               "не е изтрит — провери го на ръка.")
                    row = None
            except Exception as exc:
                raise SheetUnavailable(str(exc)) from exc
        with conn:
            if row:
                conn.execute("UPDATE receipt SET sheet_row = sheet_row - 1 WHERE sheet_row > ?",
                             (row,))
            conn.execute("DELETE FROM receipt WHERE id = ?", (rid,))
    photo_store.remove(photos_dir, rid)
    return warning
