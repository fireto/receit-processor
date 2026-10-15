"""/healthz checks (backend/health.py)."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend import db, health, receipts
from tests.helpers import photo

NOW = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "receipts.db"
    c = db.connect(p)
    db.migrate(c)
    c.close()
    return p


def test_healthy(path, tmp_path):
    ok, details = health.check(path, NOW, reader_alive=True, photos_dir=tmp_path)
    assert ok
    assert details == {"reader": "running", "oldest_queued_minutes": None,
                       "database": "writable", "photos": "writable", "sheet_pending": 0,
                       "offers_pending": 0, "offers_oldest_hours": None}


def test_dead_reader_is_unhealthy(path, tmp_path):
    ok, details = health.check(path, NOW, reader_alive=False, photos_dir=tmp_path)
    assert not ok and details["reader"] == "dead"


def test_receipt_queued_over_ten_minutes_is_unhealthy(path, tmp_path):
    conn = db.connect(path)
    rid = receipts.create(conn, tmp_path, "camera", [photo()], now=NOW - timedelta(minutes=9))
    assert health.check(path, NOW, reader_alive=True, photos_dir=tmp_path)[0]

    conn.execute("UPDATE receipt SET status = 'reading' WHERE id = ?", (rid,))
    conn.commit()
    ok, details = health.check(path, NOW + timedelta(minutes=2), reader_alive=True,
                               photos_dir=tmp_path)
    assert not ok and details["oldest_queued_minutes"] == 11


def test_an_offers_operation_pending_over_six_hours_is_unhealthy(path, tmp_path):
    conn = db.connect(path)
    with conn:
        conn.execute("INSERT INTO offers_outbox (receipt_id, op, queued_at, next_try_at, "
                     "last_error) VALUES (3, 'put', ?, NULL, 'HTTP 422')",
                     (receipts.iso(NOW - timedelta(hours=5, minutes=59)),))
    ok, details = health.check(path, NOW, reader_alive=True, photos_dir=tmp_path)
    assert ok and (details["offers_pending"], details["offers_oldest_hours"]) == (1, 5)

    ok, details = health.check(path, NOW + timedelta(minutes=2), reader_alive=True,
                               photos_dir=tmp_path)
    assert not ok and details["offers_oldest_hours"] == 6


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_read_only_database_is_unhealthy(tmp_path):
    path = tmp_path / "ro" / "receipts.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    for f in path.parent.iterdir():
        f.chmod(0o444)
    path.parent.chmod(0o555)
    try:
        ok, details = health.check(path, NOW, reader_alive=True, photos_dir=tmp_path)
        assert not ok and details["database"].startswith("not writable")
    finally:
        path.parent.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_read_only_photo_store_is_unhealthy(path, tmp_path):
    store = tmp_path / "photos"
    store.mkdir()
    store.chmod(0o555)
    try:
        ok, details = health.check(path, NOW, reader_alive=True, photos_dir=store)
        assert not ok and details["photos"] == "not writable"
    finally:
        store.chmod(0o755)
