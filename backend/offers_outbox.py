"""The hand-off to offers: every `ok` receipt is pushed to offers' receipts API.

A status change queues an operation in `offers_outbox` (one per receipt, a new one
replaces the pending one): `put` when a receipt is `ok` (read, confirmed, edited, re-read
or released as not a duplicate), `delete` when a receipt offers has is deleted. The
reader thread drains the outbox between readings with an injected client (production:
`OffersClient`, tests: `backend.fakes.FakeOffers`); OFFERS_URL unset → no client, the
outbox just fills.

A put is built from the database when it is sent, so offers gets the latest state, and
only while the receipt is still `ok`: one that left `ok` (a re-read under way or ending in
`check`) sends nothing new — offers keeps the last version it got until the receipt is
`ok` again. Manual entries have no receipt behind them and are never sent.

    python -m backend.offers_outbox enqueue-all   # queue a put for every ok receipt
"""

import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from backend import config, db

log = logging.getLogger(__name__)

TIMEOUT = 10  # seconds per request
BACKOFF_MINUTES = (1, 5, 15, 60)  # after the 1st, 2nd, 3rd failure; then hourly
PUBLIC_URL = "https://rec.fireto.net"  # where photo_url points (RECEIPTS_PUBLIC_URL)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


# --- queueing (inside the caller's transaction) -----------------------------------------

def _sendable(r: sqlite3.Row | None) -> bool:
    return r is not None and r["status"] == "ok" and r["source"] != "manual"


def _replace(conn, rid: int, op: str, now: datetime) -> None:
    conn.execute("DELETE FROM offers_outbox WHERE receipt_id = ?", (rid,))
    conn.execute("INSERT INTO offers_outbox (receipt_id, op, queued_at, next_try_at) "
                 "VALUES (?, ?, ?, ?)", (rid, op, iso(now), iso(now)))


def queue_put(conn, rid: int, now: datetime) -> None:
    """Send the receipt's latest state, if it is one offers should have."""
    r = conn.execute("SELECT status, source FROM receipt WHERE id = ?", (rid,)).fetchone()
    if _sendable(r):
        _replace(conn, rid, "put", now)


def queue_delete(conn, rid: int, now: datetime) -> None:
    """Before the receipt row is deleted: offers forgets it, if it ever got it; a put
    still pending for a receipt never sent is just dropped."""
    r = conn.execute("SELECT sent_to_offers_at FROM receipt WHERE id = ?", (rid,)).fetchone()
    if r is not None and r["sent_to_offers_at"]:
        _replace(conn, rid, "delete", now)
    else:
        conn.execute("DELETE FROM offers_outbox WHERE receipt_id = ?", (rid,))


def enqueue_all(conn, now: datetime) -> int:
    """The backfill: a put for every ok (read or confirmed) receipt. Returns the count."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM receipt WHERE status = 'ok' AND source != 'manual' ORDER BY id")]
    with conn:
        for rid in ids:
            _replace(conn, rid, "put", now)
    return len(ids)


# --- the payload ------------------------------------------------------------------------

def public_url() -> str:
    return os.environ.get("RECEIPTS_PUBLIC_URL", PUBLIC_URL).rstrip("/")


def payload(conn, rid: int, base_url: str | None = None) -> dict | None:
    """The receipt as offers' contract has it; None when it is not to be sent."""
    r = conn.execute("SELECT * FROM receipt WHERE id = ?", (rid,)).fetchone()
    if not _sendable(r):
        return None
    lines = conn.execute(
        "SELECT seq, name, qty, unit, unit_price_cents, total_cents, discount_cents "
        "FROM receipt_line WHERE receipt_id = ? ORDER BY seq", (rid,)).fetchall()
    discounts = conn.execute("SELECT seq, name, cents FROM receipt_discount "
                             "WHERE receipt_id = ? ORDER BY seq", (rid,)).fetchall()
    return {
        "id": rid, "date": r["date"], "time": r["time"], "eik": r["eik"],
        "store_name": r["store_name"], "store_address": r["store_address"],
        "total_cents": r["total_cents"],
        "photo_url": f"{(base_url or public_url()).rstrip('/')}/receipts/{rid}",
        "lines": [dict(x) for x in lines],
        "discounts": [dict(d) for d in discounts],
    }


# --- sending ----------------------------------------------------------------------------

class OffersClient:
    """offers' receipts API over HTTP (urllib: no extra dependency)."""

    def __init__(self, url: str, token: str, opener=urllib.request.urlopen,
                 timeout: float = TIMEOUT):
        self.url, self.token = url.rstrip("/"), token
        self.opener, self.timeout = opener, timeout

    def send(self, method: str, rid: int, body: dict | None) -> tuple[int, str]:
        """(status, response body); an HTTP error is a status, a network error raises."""
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        request = urllib.request.Request(
            f"{self.url}/api/receipts/{rid}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     **({"Content-Type": "application/json"} if data else {})})
        try:
            with self.opener(request, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode(errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(errors="replace")


def client_from_env() -> OffersClient | None:
    url = os.environ.get("OFFERS_URL", "").strip()
    return OffersClient(url, os.environ.get("OFFERS_TOKEN", "")) if url else None


def drain(conn, client, now: datetime) -> int:
    """Send every due operation; returns how many offers accepted."""
    due = conn.execute("SELECT * FROM offers_outbox WHERE next_try_at <= ? "
                       "ORDER BY next_try_at, op_id", (iso(now),)).fetchall()
    sent = 0
    for op in due:
        rid = op["receipt_id"]
        body = payload(conn, rid) if op["op"] == "put" else None
        if op["op"] == "put" and body is None:  # no longer ok: offers keeps what it has
            with conn:
                conn.execute("DELETE FROM offers_outbox WHERE op_id = ?", (op["op_id"],))
            continue
        method = "PUT" if op["op"] == "put" else "DELETE"
        try:
            status, text = client.send(method, rid, body)
        except Exception as exc:
            _failed(conn, op, f"{type(exc).__name__}: {exc}", now, retry=True)
            continue
        if 200 <= status < 300:
            _done(conn, op, now)
            sent += 1
        else:
            error = f"HTTP {status}: {text[:200]}" if text else f"HTTP {status}"
            _failed(conn, op, error, now, retry=status >= 500 or status < 400)
    return sent


def _done(conn, op: sqlite3.Row, now: datetime) -> None:
    rid = op["receipt_id"]
    with conn:
        # an operation queued while this one was in flight stays for the next round
        conn.execute("DELETE FROM offers_outbox WHERE op_id = ?", (op["op_id"],))
        if op["op"] == "put":
            found = conn.execute("UPDATE receipt SET sent_to_offers_at = ? WHERE id = ?",
                                 (iso(now), rid)).rowcount
            pending = conn.execute("SELECT 1 FROM offers_outbox WHERE receipt_id = ?",
                                   (rid,)).fetchone()
            if not found and not pending:  # deleted while its first put was in flight
                _replace(conn, rid, "delete", now)
    log.info("receipt %s: %s sent to offers", rid, op["op"])


def _failed(conn, op: sqlite3.Row, error: str, now: datetime, retry: bool) -> None:
    attempts = op["attempts"] + 1
    wait = timedelta(minutes=BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES)) - 1])
    with conn:
        conn.execute("UPDATE offers_outbox SET attempts = ?, next_try_at = ?, last_error = ? "
                     "WHERE op_id = ?",
                     (attempts, iso(now + wait) if retry else None, error, op["op_id"]))
    log.warning("receipt %s: %s to offers failed (%s)%s", op["receipt_id"], op["op"], error,
                "" if retry else ", not retried until the receipt changes")


# --- CLI --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["enqueue-all"]:
        print("usage: python -m backend.offers_outbox enqueue-all", file=sys.stderr)
        return 2
    conn = db.connect(config.DB_PATH)
    try:
        db.migrate(conn)
        n = enqueue_all(conn, datetime.now(timezone.utc))
    finally:
        conn.close()
    print(f"{n} receipt(s) queued for offers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
