"""The background reader: one in-process daemon thread, started from the app lifespan.

It reads queued receipts one at a time, oldest first, with the reader function injected
at construction (production: `backend.reading.read_receipt`, imported lazily so the app
starts without the providers' SDKs loaded). Every minute it also retries pending Sheet
writes, and between readings it sends the due operations of the offers outbox (`offers`:
the client, None when OFFERS_URL is unset). Nothing a reading does can stop the thread; /healthz reports a dead one.
"""

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from backend import db, offers_outbox, receipts

log = logging.getLogger(__name__)

SYNC_EVERY = 60  # seconds between retries of pending Sheet writes


def read_receipt(photos, **kwargs):
    """Production reader."""
    from backend.reading import read_receipt as read
    return read(photos, **kwargs)


def decode_qr(image: bytes) -> dict | None:
    from backend.reading import decode_qr as decode
    return decode(image)


def mode() -> str:
    """RECEIPTS_READER: 1 (default) the real reader, 0 no reader thread (tests),
    fake a canned reading after a short pause (local development only)."""
    return os.environ.get("RECEIPTS_READER", "1")


class Worker:
    def __init__(self, db_path: Path | str, photos_dir: Path, sheet,
                 read: Callable = read_receipt, decode_qr: Callable = decode_qr,
                 clock: Callable[[], datetime] = receipts.utcnow, idle_wait: float = 2.0,
                 company_lookup: Callable | None = None, offers=None):
        self.db_path, self.photos_dir, self.sheet = db_path, Path(photos_dir), sheet
        self.read, self.decode_qr, self.clock = read, decode_qr, clock
        self.idle_wait = idle_wait
        self.company_lookup = company_lookup
        self.offers = offers
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.thread: threading.Thread | None = None
        self._last_sync = 0.0

    # --- one step ------------------------------------------------------------------------

    def process_next(self) -> bool:
        """Read the oldest queued receipt; False when there is none."""
        conn = db.connect(self.db_path)
        try:
            r = receipts.claim_next(conn)
            if r is None:
                return False
            rid = r["id"]
            log.info("receipt %s: reading (start at %s)", rid, r["start_at"])
            try:
                self._read(conn, r)
            except Exception as exc:
                log.exception("receipt %s: reading failed", rid)
                conn.rollback()
                with conn:
                    receipts.record_attempts(conn, rid, [_AppError(self.clock(), exc)])
                    conn.execute("UPDATE receipt SET status = 'failed' WHERE id = ?", (rid,))
            return True
        finally:
            conn.close()

    def _read(self, conn, r) -> None:
        rid = r["id"]
        images = receipts.photo_bytes(conn, self.photos_dir, rid)
        qr = self._qr(images)
        original = receipts.find_duplicate(conn, rid, qr) if qr else None
        if original:  # the same fiscal receipt again: no need to pay for a reading
            receipts.mark_duplicate(conn, rid, original, None, qr)
            log.info("receipt %s: duplicate of %s (QR)", rid, original)
            return
        try:
            categories = self.sheet.categories()
        except Exception:
            log.warning("receipt %s: categories unavailable", rid, exc_info=True)
            categories = []
        amount = (qr or {}).get("amount")
        outcome = self.read(
            images, categories=categories,
            spent_this_month_usd=receipts.spent_this_month_usd(conn, self.clock()),
            start_at=r["start_at"],
            qr_total_cents=round(amount * 100) if isinstance(amount, (int, float)) else None)
        status = receipts.store_outcome(conn, rid, outcome, qr)
        if getattr(outcome, "reading", None) is not None:
            receipts.apply_history(conn, self.sheet, rid)
            status = receipts.apply_company(conn, rid, self.company_lookup, status, self.clock())
        if status in ("ok", "check") and not receipts.get_row(conn, rid)["sheet_row"]:
            original = receipts.find_duplicate(conn, rid)
            if original:
                receipts.mark_duplicate(conn, rid, original, status)
                log.info("receipt %s: duplicate of %s", rid, original)
                status = "duplicate"
        if status == "ok":
            receipts.sync_sheet(conn, self.sheet, rid, self.clock())
            with conn:
                offers_outbox.queue_put(conn, rid, self.clock())
        log.info("receipt %s: %s", rid, status)

    def _qr(self, images: list[bytes]) -> dict | None:
        for image in images:
            try:
                found = self.decode_qr(image)
            except Exception:
                log.warning("QR decoding failed", exc_info=True)
                continue
            if found:
                return found
        return None

    def sync_pending(self) -> None:
        conn = db.connect(self.db_path)
        try:
            for rid in receipts.pending_sheet_ids(conn):
                receipts.sync_sheet(conn, self.sheet, rid, self.clock())
        finally:
            conn.close()

    def send_to_offers(self) -> None:
        if self.offers is None:
            return
        conn = db.connect(self.db_path)
        try:
            offers_outbox.drain(conn, self.offers, self.clock())
        finally:
            conn.close()

    # --- the thread -------------------------------------------------------------------------

    def wake(self) -> None:
        """Something was queued: look now rather than after the idle wait."""
        self._wake.set()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="reader", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        self._wake.set()
        if self.thread:
            self.thread.join(timeout)

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            worked = False
            try:
                worked = self.process_next()
                self.send_to_offers()
                if time.monotonic() - self._last_sync >= SYNC_EVERY:
                    self._last_sync = time.monotonic()
                    self.sync_pending()
            except Exception:  # a database hiccup must not kill the reader
                log.exception("reader loop")
            if not worked:
                self._wake.wait(self.idle_wait)
                self._wake.clear()


class _AppError:
    """An attempt row for an exception raised outside the reader's own bookkeeping."""

    provider, model, outcome, raw = "app", "-", "error", None
    seconds = input_tokens = output_tokens = cost_usd = None

    def __init__(self, at: datetime, exc: Exception):
        self.started_at = receipts.iso(at)
        self.error = f"{type(exc).__name__}: {exc}"
