"""SQLite connection and append-only migrations (backend/db.py)."""

import sqlite3

import pytest

from backend import db


def columns(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_migrate_creates_the_schema_and_records_the_version(tmp_path):
    conn = db.connect(tmp_path / "sub" / "receipts.db")
    db.migrate(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"receipt", "receipt_photo", "receipt_line", "receipt_discount",
            "reading"} <= tables
    assert {"id", "created_at", "status", "source", "date", "time", "total_cents", "eik",
            "store_name", "store_address", "payment", "card_last4", "category",
            "payment_method", "notes", "qr_json", "self_check_json", "quality_json",
            "sheet_row", "sheet_written_at", "confirmed_at", "queued_at", "start_at",
            "next_start_at"} <= set(columns(conn, "receipt"))
    assert columns(conn, "receipt_photo") == ["receipt_id", "seq", "path", "width", "height"]
    assert columns(conn, "receipt_line") == [
        "receipt_id", "seq", "name", "qty", "unit", "unit_price_cents", "total_cents",
        "discount_cents", "vat_group"]
    assert columns(conn, "receipt_discount") == ["receipt_id", "seq", "name", "cents"]
    assert {"receipt_id", "provider", "model", "started_at", "seconds", "input_tokens",
            "output_tokens", "cost_usd", "outcome", "raw_json", "error"} <= set(
        columns(conn, "reading"))


def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "receipts.db")
    db.migrate(conn)
    db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_status_and_source_are_constrained(tmp_path):
    conn = db.connect(tmp_path / "receipts.db")
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO receipt (created_at, status, source) "
                     "VALUES ('2026-10-14T10:00:00+00:00', 'weird', 'camera')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO receipt (created_at, status, source) "
                     "VALUES ('2026-10-14T10:00:00+00:00', 'queued', 'fax')")


def test_deleting_a_receipt_removes_its_rows_but_keeps_the_readings(tmp_path):
    conn = db.connect(tmp_path / "receipts.db")
    db.migrate(conn)
    rid = conn.execute("INSERT INTO receipt (created_at, status, source) "
                       "VALUES ('2026-10-14T10:00:00+00:00', 'ok', 'camera')").lastrowid
    conn.execute("INSERT INTO receipt_photo VALUES (?, 1, 'x/1.webp', 10, 20)", (rid,))
    conn.execute("INSERT INTO receipt_line (receipt_id, seq, name, qty, unit, total_cents) "
                 "VALUES (?, 1, 'Хляб', 1, 'pcs', 129)", (rid,))
    conn.execute("INSERT INTO receipt_discount VALUES (?, 1, 'Lidl Plus', 50)", (rid,))
    conn.execute("INSERT INTO reading (receipt_id, provider, model, started_at, outcome) "
                 "VALUES (?, 'anthropic', 'haiku', '2026-10-14T10:00:00+00:00', 'ok')", (rid,))
    conn.execute("DELETE FROM receipt WHERE id = ?", (rid,))
    for t in ("receipt_photo", "receipt_line", "receipt_discount"):
        assert conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0
    # the spend history stays: deleting a receipt must not reset this month's cost
    assert [tuple(r) for r in conn.execute("SELECT receipt_id FROM reading")] == [(None,)]
