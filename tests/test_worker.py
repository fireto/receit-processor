"""Background reading (backend/worker.py) and the receipt store (backend/receipts.py),
with a fake reader and a fake Sheet."""

import json
import time
from datetime import datetime, timezone

import pytest

from backend import db, receipts
from backend.fakes import Attempt, Discount, FakeSheet, Line, Outcome, Reading
from backend.worker import Worker
from tests.helpers import FakeReader, photo

NOW = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "receipts.db"
    conn = db.connect(path)
    db.migrate(conn)
    sheet = FakeSheet()
    photos_dir = tmp_path / "photos"

    def worker(reader, decode_qr=lambda image: None):
        return Worker(path, photos_dir, sheet, read=reader, decode_qr=decode_qr,
                      clock=lambda: NOW)

    yield conn, sheet, photos_dir, worker
    conn.close()


def new_receipt(conn, photos_dir, n=1, source="camera"):
    return receipts.create(conn, photos_dir, source, [photo() for _ in range(n)], now=NOW)


def receipt(conn, rid):
    return conn.execute("SELECT * FROM receipt WHERE id = ?", (rid,)).fetchone()


# --- queue and reading ------------------------------------------------------------------

def test_a_new_receipt_is_queued_with_its_photos_stored(env):
    conn, sheet, photos_dir, _ = env
    rid = new_receipt(conn, photos_dir, n=2)

    r = receipt(conn, rid)
    assert r["status"] == "queued" and r["source"] == "camera"
    assert r["queued_at"] == NOW.isoformat(timespec="seconds")
    rows = conn.execute("SELECT seq, path FROM receipt_photo WHERE receipt_id = ? "
                        "ORDER BY seq", (rid,)).fetchall()
    assert [tuple(x) for x in rows] == [(1, f"{rid}/1.webp"), (2, f"{rid}/2.webp")]
    assert (photos_dir / str(rid) / "2.webp").exists()


def test_ok_reading_stores_everything_and_writes_the_sheet_row(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir, n=2)
    outcome = Outcome(reading=Reading(receipt_discounts=[Discount("Lidl Plus", 0)]),
                      attempts=[Attempt(outcome="check_failed", raw={"x": 1}),
                                Attempt(model="fake-2", outcome="ok")], next_start_at=2)
    reader = FakeReader(outcome)

    assert worker(reader).process_next() is True

    call = reader.calls[0]
    assert len(call["photos"]) == 2 and call["photos"][0][:4] == b"RIFF"
    assert call["categories"] == FakeSheet().categories()
    assert call["start_at"] == 0 and call["spent"] == 0
    r = receipt(conn, rid)
    assert r["status"] == "ok"
    assert (r["date"], r["time"], r["total_cents"], r["eik"]) == (
        "2026-10-14", "18:42", 1234, "131129282")
    assert r["next_start_at"] == 2
    assert r["payment_method"] == "FIB 0889"  # card ****0889
    lines = conn.execute("SELECT name, qty, unit, unit_price_cents, total_cents, "
                         "discount_cents FROM receipt_line WHERE receipt_id = ? "
                         "ORDER BY seq", (rid,)).fetchall()
    assert [tuple(x) for x in lines][1] == ("Мляко Верея 3%", 1.0, "pcs", 395, 395, 50)
    assert len(lines) == 3
    readings = conn.execute("SELECT model, outcome, raw_json, cost_usd FROM reading "
                            "WHERE receipt_id = ? ORDER BY id", (rid,)).fetchall()
    assert [(x["model"], x["outcome"]) for x in readings] == [
        ("fake-1", "check_failed"), ("fake-2", "ok")]
    assert json.loads(readings[0]["raw_json"]) == {"x": 1}
    check = json.loads(r["self_check_json"])
    # Σ(line totals − line discounts) − receipt discounts vs the total
    assert check == {"lines_cents": 1234, "discounts_cents": 0, "total_cents": 1234,
                     "diff_cents": 0, "ok": True}
    # the Sheet: the next row, euro only, ЕИК in БУЛСТАТ
    assert r["sheet_row"] == 2 and r["sheet_written_at"]
    assert sheet.rows[1] == ["14.10.2026", "Храна", "", "12,34", "", "FIB 0889", "", "",
                             "хляб, мляко, сирене", "131129282", "Kaufland България"]


@pytest.mark.parametrize("status", ["check", "retake", "failed"])
def test_other_outcomes_set_the_status_and_write_nothing(env, status):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    reading = None if status == "failed" else Reading(
        quality=["blurry"] if status == "retake" else [])
    outcome = Outcome(status=status, reading=reading,
                      attempts=[Attempt(outcome="error", error="boom")])
    worker(FakeReader(outcome)).process_next()

    r = receipt(conn, rid)
    assert r["status"] == status
    assert r["sheet_row"] is None and len(sheet.rows) == 1
    if status == "retake":
        assert json.loads(r["quality_json"]) == ["blurry"]
    assert conn.execute("SELECT error FROM reading WHERE receipt_id = ?",
                        (rid,)).fetchone()[0] == "boom"


def test_a_reader_exception_fails_the_receipt_and_is_recorded(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader(error=RuntimeError("no network"))).process_next()

    assert receipt(conn, rid)["status"] == "failed"
    row = conn.execute("SELECT provider, outcome, error FROM reading").fetchone()
    assert (row["provider"], row["outcome"]) == ("app", "error")
    assert "no network" in row["error"]


def test_oldest_queued_first_and_nothing_to_do_returns_false(env):
    conn, sheet, photos_dir, worker = env
    first, second = new_receipt(conn, photos_dir), new_receipt(conn, photos_dir)
    reader = FakeReader(Outcome(status="check"))
    w = worker(reader)
    w.process_next()
    assert receipt(conn, first)["status"] == "check"
    assert receipt(conn, second)["status"] == "queued"
    w.process_next()
    assert w.process_next() is False


def test_qr_total_is_passed_and_stored(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir, n=2)
    seen = []

    def decode(image):
        seen.append(image)
        return {"amount": 12.34, "date": "2026-10-14"} if len(seen) == 2 else None

    reader = FakeReader()
    worker(reader, decode).process_next()

    assert reader.calls[0]["qr_total_cents"] == 1234
    assert json.loads(receipt(conn, rid)["qr_json"])["amount"] == 12.34


def test_spent_this_month_counts_this_sofia_month_only(env):
    conn, sheet, photos_dir, worker = env
    for started, cost in (("2026-09-30T20:59:00+00:00", 1.0),   # Sept 30 23:59 Sofia
                          ("2026-09-30T21:00:00+00:00", 0.25),  # Oct 1 00:00 Sofia
                          ("2026-10-14T10:00:00+00:00", 0.5)):
        conn.execute("INSERT INTO reading (provider, model, started_at, cost_usd, outcome) "
                     "VALUES ('p', 'm', ?, ?, 'ok')", (started, cost))
    conn.commit()
    assert receipts.spent_this_month_usd(conn, NOW) == pytest.approx(0.75)

    new_receipt(conn, photos_dir)
    reader = FakeReader()
    worker(reader).process_next()
    assert reader.calls[0]["spent"] == pytest.approx(0.75)


def test_interrupted_reading_is_queued_again(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    conn.execute("UPDATE receipt SET status = 'reading' WHERE id = ?", (rid,))
    conn.commit()
    receipts.reset_interrupted(conn)
    assert receipt(conn, rid)["status"] == "queued"


# --- re-read, retake, retry --------------------------------------------------------------

def test_reread_starts_at_the_next_ladder_step(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    reader = FakeReader(Outcome(status="check", next_start_at=1),
                        Outcome(status="check", next_start_at=None))
    w = worker(reader)
    w.process_next()

    assert receipts.reread(conn, rid, now=NOW) is True
    assert receipt(conn, rid)["status"] == "queued"
    w.process_next()
    assert reader.calls[1]["start_at"] == 1
    assert receipts.reread(conn, rid, now=NOW) is False  # no better model left


def test_retake_keeps_the_id_and_updates_the_same_sheet_row(env):
    conn, sheet, photos_dir, worker = env
    other = new_receipt(conn, photos_dir)
    rid = new_receipt(conn, photos_dir)
    reader = FakeReader(Outcome(), Outcome(reading=Reading(total_cents=1500)),
                        Outcome(reading=Reading(total_cents=2000, notes="нова снимка")))
    w = worker(reader)
    w.process_next(), w.process_next()
    assert receipt(conn, rid)["sheet_row"] == 3

    receipts.replace_photos(conn, photos_dir, rid, [photo(), photo(), photo()], now=NOW)
    r = receipt(conn, rid)
    assert (r["status"], r["start_at"]) == ("queued", 0)
    assert conn.execute("SELECT count(*) FROM receipt_photo WHERE receipt_id = ?",
                        (rid,)).fetchone()[0] == 3
    w.process_next()

    r = receipt(conn, rid)
    assert (r["id"], r["status"], r["sheet_row"]) == (rid, "ok", 3)
    assert len(sheet.rows) == 3  # updated in place, not appended
    assert sheet.rows[2][3] == "20,00" and sheet.rows[2][8] == "нова снимка"
    assert receipt(conn, other)["sheet_row"] == 2


def test_failed_receipt_retry_queues_it_at_the_same_step(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    conn.execute("UPDATE receipt SET status = 'failed', start_at = 1 WHERE id = ?", (rid,))
    receipts.retry(conn, rid, now=NOW)
    reader = FakeReader()
    worker(reader).process_next()
    assert reader.calls[0]["start_at"] == 1
    assert receipt(conn, rid)["status"] == "ok"


# --- history mapping ---------------------------------------------------------------------

def test_cash_maps_to_the_cash_payment_method(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader(Outcome(reading=Reading(payment="cash", card_last4=None)))).process_next()
    assert receipt(conn, rid)["payment_method"] == "Cash"


def test_unknown_card_leaves_the_payment_method_empty(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader(Outcome(reading=Reading(card_last4="9999")))).process_next()
    assert receipt(conn, rid)["payment_method"] is None


def test_card_methods_map_a_card_to_a_method_without_its_digits(env, monkeypatch):
    # CARD_METHODS (host .env): food vouchers *2889 are "Ваучери за храна" in the Sheet
    monkeypatch.setenv("CARD_METHODS", "2889=Ваучери за храна, 6832 = Revolut")
    conn, sheet, photos_dir, worker = env
    a, b = new_receipt(conn, photos_dir), new_receipt(conn, photos_dir)
    w = worker(FakeReader(Outcome(reading=Reading(card_last4="2889")),
                          Outcome(reading=Reading(card_last4="6832", total_cents=99))))
    w.process_next(), w.process_next()
    assert receipt(conn, a)["payment_method"] == "Ваучери за храна"
    assert receipt(conn, b)["payment_method"] == "Revolut"


def test_card_methods_naming_an_unknown_method_is_ignored(env, monkeypatch):
    monkeypatch.setenv("CARD_METHODS", "9999=Няма такъв")
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader(Outcome(reading=Reading(card_last4="9999")))).process_next()
    assert receipt(conn, rid)["payment_method"] is None


def test_eik_history_replaces_the_fallback_category(env):
    conn, sheet, photos_dir, worker = env
    sheet.rows += [["01.09.2026", "Аптека", "", "5,00", "", "", "", "", "", "222"],
                   ["02.09.2026", "Аптека", "", "6,00", "", "", "", "", "", "222"],
                   ["03.09.2026", "Козметика", "", "7,00", "", "", "", "", "", "222"]]
    a, b = new_receipt(conn, photos_dir), new_receipt(conn, photos_dir)
    w = worker(FakeReader(Outcome(reading=Reading(eik="222", category="Разни")),
                          Outcome(reading=Reading(eik="222", category="Храна"))))
    w.process_next(), w.process_next()
    assert receipt(conn, a)["category"] == "Аптека"
    assert receipt(conn, b)["category"] == "Храна"  # the model's own pick stays


# --- the Sheet ---------------------------------------------------------------------------

def test_check_waits_for_confirmation_then_writes(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader(Outcome(status="check"))).process_next()
    assert len(sheet.rows) == 1

    receipts.confirm(conn, sheet, rid, now=NOW)

    r = receipt(conn, rid)
    assert (r["status"], r["sheet_row"]) == ("ok", 2)
    assert r["confirmed_at"] == NOW.isoformat(timespec="seconds")


def test_edit_updates_the_row(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader()).process_next()

    receipts.update_header(conn, sheet, rid, date="2026-10-13", total_cents=999,
                           category="Аптека", payment_method="Revolut", notes="лекарства",
                           now=NOW)

    assert sheet.rows[1][:6] == ["13.10.2026", "Аптека", "", "9,99", "", "Revolut"]
    assert sheet.rows[1][8] == "лекарства"


def test_edit_of_an_unwritten_receipt_does_not_write(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader(Outcome(status="check"))).process_next()
    receipts.update_header(conn, sheet, rid, date="2026-10-13", total_cents=999,
                           category="Аптека", payment_method="", notes="", now=NOW)
    assert len(sheet.rows) == 1
    assert receipt(conn, rid)["payment_method"] is None


def test_delete_deletes_the_row_and_shifts_the_rows_below(env):
    conn, sheet, photos_dir, worker = env
    a, b, c = (new_receipt(conn, photos_dir) for _ in range(3))
    w = worker(FakeReader(*(Outcome(reading=Reading(total_cents=100 * n)) for n in (1, 2, 3))))
    for _ in range(3):
        w.process_next()
    assert [receipt(conn, x)["sheet_row"] for x in (a, b, c)] == [2, 3, 4]

    assert receipts.delete(conn, sheet, photos_dir, b) is None

    assert receipt(conn, b) is None
    assert not (photos_dir / str(b)).exists()
    assert len(sheet.rows) == 3
    assert [receipt(conn, x)["sheet_row"] for x in (a, c)] == [2, 3]


def test_delete_leaves_a_changed_row_alone(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader()).process_next()
    sheet.rows[1][3] = "99,99"  # someone edited the row by hand

    warning = receipts.delete(conn, sheet, photos_dir, rid)

    assert warning and "ред 2" in warning
    assert len(sheet.rows) == 2
    assert receipt(conn, rid) is None


def test_delete_is_refused_while_the_sheet_is_unreachable(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    worker(FakeReader()).process_next()
    sheet.fail = True
    with pytest.raises(receipts.SheetUnavailable):
        receipts.delete(conn, sheet, photos_dir, rid)
    assert receipt(conn, rid) is not None


def test_a_failed_sheet_write_stays_pending_and_is_retried(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    sheet.fail = True
    w = worker(FakeReader())
    w.process_next()
    r = receipt(conn, rid)
    assert (r["status"], r["sheet_row"], r["sheet_written_at"]) == ("ok", None, None)
    assert receipts.pending_sheet_ids(conn) == [rid]

    sheet.fail = False
    w.sync_pending()
    assert receipt(conn, rid)["sheet_row"] == 2
    assert receipts.pending_sheet_ids(conn) == []


def test_manual_entry_is_written_straight_away(env):
    conn, sheet, photos_dir, worker = env
    rid = receipts.create_manual(conn, sheet, date="2026-10-10", total_cents=4500,
                                 category="Гориво", payment_method="Cash", notes="бензин",
                                 now=NOW)
    r = receipt(conn, rid)
    assert (r["source"], r["status"], r["sheet_row"]) == ("manual", "ok", 2)
    assert sheet.rows[1][:4] == ["10.10.2026", "Гориво", "", "45,00"]


# --- the thread --------------------------------------------------------------------------

def test_the_thread_processes_the_queue_and_stops(env):
    conn, sheet, photos_dir, worker = env
    rid = new_receipt(conn, photos_dir)
    w = worker(FakeReader())
    w.idle_wait = 0.05
    w.start()
    try:
        for _ in range(100):
            if receipt(conn, rid)["status"] == "ok":
                break
            time.sleep(0.02)
        assert receipt(conn, rid)["status"] == "ok"
        assert w.alive()
    finally:
        w.stop()
    assert not w.alive()
