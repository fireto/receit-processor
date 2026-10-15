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


def test_migration_2_keeps_v1_receipts_and_their_children(tmp_path):
    # The receipt table is rebuilt (new status in its CHECK); with foreign keys on, the
    # DROP would cascade-delete every photo and line.
    conn = db.connect(tmp_path / "receipts.db")
    conn.executescript(f"BEGIN; {db.MIGRATIONS[0]}; PRAGMA user_version = 1; COMMIT;")
    conn.execute("INSERT INTO receipt (id, created_at, status, source, total_cents, sheet_row) "
                 "VALUES (7, '2026-10-14T10:00:00+00:00', 'ok', 'camera', 425, 12)")
    conn.execute("INSERT INTO receipt_photo VALUES (7, 1, '7/1.webp', 10, 20)")
    conn.execute("INSERT INTO receipt_line (receipt_id, seq, name, total_cents) "
                 "VALUES (7, 1, 'Хляб', 425)")
    conn.execute("INSERT INTO reading (receipt_id, provider, model, started_at, outcome) "
                 "VALUES (7, 'claude', 'm', '2026-10-14T10:00:00+00:00', 'ok')")
    conn.commit()

    db.migrate(conn)

    assert conn.execute("SELECT total_cents, sheet_row FROM receipt WHERE id = 7").fetchone()[:] \
        == (425, 12)
    for table in ("receipt_photo", "receipt_line", "reading"):
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE receipt_id = 7").fetchone()[0] == 1
    assert {"duplicate_of", "duplicate_held_status", "duplicate_dismissed"} <= set(
        columns(conn, "receipt"))
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute("UPDATE receipt SET status = 'duplicate' WHERE id = 7")  # allowed now
    conn.execute("DELETE FROM receipt WHERE id = 7")  # cascades still work
    assert conn.execute("SELECT count(*) FROM receipt_photo").fetchone()[0] == 0
    assert conn.execute("SELECT receipt_id FROM reading").fetchone()[0] is None


def test_migration_4_adds_the_offers_outbox_and_stops_reusing_receipt_ids(tmp_path):
    # offers keys receipts on the id: a deleted receipt's id must never come back
    conn = db.connect(tmp_path / "receipts.db")
    for i, step in enumerate(db.MIGRATIONS[:3], start=1):
        conn.executescript(f"BEGIN; {step}; PRAGMA user_version = {i}; COMMIT;")
    conn.execute("INSERT INTO receipt (id, created_at, status, source, total_cents) "
                 "VALUES (7, '2026-10-14T10:00:00+00:00', 'ok', 'camera', 425)")
    conn.execute("INSERT INTO receipt_line (receipt_id, seq, name, total_cents) "
                 "VALUES (7, 1, 'Хляб', 425)")
    conn.commit()

    db.migrate(conn)

    assert "sent_to_offers_at" in columns(conn, "receipt")
    assert columns(conn, "offers_outbox") == [
        "op_id", "receipt_id", "op", "queued_at", "attempts", "next_try_at", "last_error"]
    assert conn.execute("SELECT count(*) FROM receipt_line WHERE receipt_id = 7"
                        ).fetchone()[0] == 1
    conn.execute("DELETE FROM receipt WHERE id = 7")
    new = conn.execute("INSERT INTO receipt (created_at, status, source) "
                       "VALUES ('2026-10-15T10:00:00+00:00', 'queued', 'camera')").lastrowid
    assert new == 8
    # a delete must outlive its receipt: no foreign key from the outbox
    conn.execute("INSERT INTO offers_outbox (receipt_id, op, queued_at, next_try_at) "
                 "VALUES (7, 'delete', '2026-10-15T10:00:00+00:00', "
                 "'2026-10-15T10:00:00+00:00')")
    with pytest.raises(sqlite3.IntegrityError):  # one pending operation per receipt
        conn.execute("INSERT INTO offers_outbox (receipt_id, op, queued_at) "
                     "VALUES (7, 'put', '2026-10-15T10:00:00+00:00')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO offers_outbox (receipt_id, op, queued_at) "
                     "VALUES (9, 'post', '2026-10-15T10:00:00+00:00')")
