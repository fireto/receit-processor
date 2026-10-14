"""The same receipt scanned twice: caught by its fiscal QR code before reading (free), or by
ЕИК + date + minute + total after reading; held as `duplicate`, never written to the Sheet
unless dismissed ("Не е дубликат")."""

import pytest

from backend import db, receipts
from backend.fakes import FakeSheet, Outcome, Reading
from backend.worker import Worker
from tests.helpers import FakeReader, photo
from tests.test_worker import NOW, receipt

QR = {"fp_number": "DT123456", "receipt_number": "0041234", "date": "2026-10-14",
      "time": "18:42:10", "amount": 12.34}


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "receipts.db"
    conn = db.connect(path)
    db.migrate(conn)
    sheet = FakeSheet()
    photos_dir = tmp_path / "photos"

    def run(reader=None, qr=None):
        w = Worker(path, photos_dir, sheet, read=reader or FakeReader(),
                   decode_qr=lambda image: qr, clock=lambda: NOW)
        rid = receipts.create(conn, photos_dir, "camera", [photo()], now=NOW)
        w.process_next()
        return rid, w

    yield conn, sheet, photos_dir, run
    conn.close()


def test_same_header_twice_holds_the_second_as_duplicate(env):
    conn, sheet, _, run = env
    first, _ = run()
    second, _ = run()

    r = receipt(conn, second)
    assert r["status"] == "duplicate" and r["duplicate_of"] == first
    assert r["duplicate_held_status"] == "ok"
    assert r["sheet_row"] is None
    assert len(sheet.rows) == 2  # header + the first receipt only
    assert receipt(conn, first)["status"] == "ok"


def test_same_qr_is_caught_before_reading(env):
    conn, sheet, _, run = env
    reader = FakeReader()
    first, _ = run(reader, qr=QR)
    second, _ = run(reader, qr=QR)

    assert len(reader.calls) == 1  # the second was never sent to a model
    r = receipt(conn, second)
    assert r["status"] == "duplicate" and r["duplicate_of"] == first
    assert r["duplicate_held_status"] is None  # not read
    assert r["qr_json"] is not None
    assert len(sheet.rows) == 2


@pytest.mark.parametrize("change", [{"total_cents": 1235}, {"time": "18:43"},
                                    {"date": "2026-10-15"}, {"eik": "131071587"}])
def test_a_different_receipt_is_not_a_duplicate(env, change):
    conn, sheet, _, run = env
    run()
    other, _ = run(FakeReader(Outcome(reading=Reading(**change))))
    assert receipt(conn, other)["status"] == "ok"
    assert len(sheet.rows) == 3


def test_seconds_on_the_time_do_not_hide_a_duplicate(env):
    conn, _, _, run = env
    first, _ = run(FakeReader(Outcome(reading=Reading(time="18:42"))))
    second, _ = run(FakeReader(Outcome(reading=Reading(time="18:42:37"))))
    assert receipt(conn, second)["duplicate_of"] == first


def test_a_check_original_also_counts(env):
    conn, _, _, run = env
    first, _ = run(FakeReader(Outcome(status="check")))
    second, _ = run(FakeReader(Outcome(status="check")))
    r = receipt(conn, second)
    assert r["status"] == "duplicate" and r["duplicate_held_status"] == "check"


def test_not_a_duplicate_restores_a_read_receipt_and_writes_it(env):
    conn, sheet, _, run = env
    run()
    second, w = run()

    assert receipts.dismiss_duplicate(conn, sheet, second, now=NOW) == "ok"
    r = receipt(conn, second)
    assert r["status"] == "ok" and r["duplicate_of"] is None and r["duplicate_dismissed"]
    assert r["sheet_row"] == 3 and len(sheet.rows) == 3


def test_not_a_duplicate_of_an_unread_receipt_queues_it_and_it_stays_dismissed(env):
    conn, sheet, _, run = env
    reader = FakeReader()
    run(reader, qr=QR)
    second, w = run(reader, qr=QR)

    assert receipts.dismiss_duplicate(conn, sheet, second, now=NOW) == "queued"
    assert receipt(conn, second)["status"] == "queued"
    w.process_next()
    r = receipt(conn, second)
    assert r["status"] == "ok" and len(reader.calls) == 2
    assert r["sheet_row"] == 3


def test_a_dismissed_receipt_is_not_flagged_again_on_reread(env):
    conn, sheet, _, run = env
    run()
    second, w = run()
    receipts.dismiss_duplicate(conn, sheet, second, now=NOW)
    assert receipts.reread(conn, second, now=NOW)
    w.process_next()
    assert receipt(conn, second)["status"] == "ok"


def test_deleting_the_original_keeps_the_duplicate_dismissable(env):
    conn, sheet, photos_dir, run = env
    first, _ = run()
    second, _ = run()
    receipts.delete(conn, sheet, photos_dir, first)

    r = receipt(conn, second)
    assert r["status"] == "duplicate" and r["duplicate_of"] is None
    assert receipts.dismiss_duplicate(conn, sheet, second, now=NOW) == "ok"
    assert receipt(conn, second)["sheet_row"] == 2


def test_a_retaken_duplicate_is_checked_afresh(env):
    conn, sheet, photos_dir, run = env
    run()
    second, w = run()
    receipts.replace_photos(conn, photos_dir, second, [photo()], now=NOW)
    r = receipt(conn, second)
    assert r["status"] == "queued" and r["duplicate_of"] is None


def test_dismiss_only_applies_to_duplicates(env):
    conn, sheet, _, run = env
    first, _ = run()
    assert receipts.dismiss_duplicate(conn, sheet, first, now=NOW) is None
    assert receipt(conn, first)["status"] == "ok"
