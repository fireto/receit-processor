"""The hand-off to offers (backend/offers_outbox.py): queueing, the payload, sending with
retries, the HTTP client and the backfill CLI. Nothing here touches the network."""

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from backend import db, offers_outbox as outbox, receipts
from backend.fakes import Discount, FakeOffers, FakeSheet, Outcome, Reading
from tests.helpers import photo

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "receipts.db")
    db.migrate(c)
    yield c
    c.close()


def ok_receipt(conn, tmp_path, outcome=None) -> int:
    rid = receipts.create(conn, tmp_path / "photos", "camera", [photo()], now=NOW)
    receipts.store_outcome(conn, rid, outcome or Outcome())
    return rid


def op(conn, rid):
    return conn.execute("SELECT * FROM offers_outbox WHERE receipt_id = ?", (rid,)).fetchone()


def queue_put(conn, rid, now=NOW):
    with conn:
        outbox.queue_put(conn, rid, now)


def at(minutes):
    return outbox.iso(NOW + timedelta(minutes=minutes))


# --- queueing -------------------------------------------------------------------------

def test_an_ok_receipt_queues_a_put(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    o = op(conn, rid)
    assert (o["op"], o["queued_at"], o["attempts"], o["next_try_at"], o["last_error"]) == (
        "put", outbox.iso(NOW), 0, outbox.iso(NOW), None)


@pytest.mark.parametrize("status", ["queued", "reading", "check", "retake", "failed",
                                    "duplicate"])
def test_a_receipt_that_is_not_ok_is_never_queued(conn, tmp_path, status):
    rid = ok_receipt(conn, tmp_path)
    conn.execute("UPDATE receipt SET status = ? WHERE id = ?", (status, rid))
    queue_put(conn, rid)
    assert op(conn, rid) is None


def test_a_manual_entry_is_not_a_receipt_for_offers(conn):
    rid = receipts.create_manual(conn, FakeSheet(), date="2026-10-15", total_cents=500,
                                 category="Разни", payment_method=None, notes=None, now=NOW)
    queue_put(conn, rid)
    assert op(conn, rid) is None


def test_a_new_operation_replaces_the_pending_one(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    first = op(conn, rid)
    conn.execute("UPDATE offers_outbox SET attempts = 3, next_try_at = NULL, "
                 "last_error = 'HTTP 422' WHERE receipt_id = ?", (rid,))
    queue_put(conn, rid, NOW + timedelta(minutes=5))
    o = op(conn, rid)
    assert o["op_id"] != first["op_id"]
    assert (o["attempts"], o["next_try_at"], o["last_error"]) == (0, at(5), None)
    assert conn.execute("SELECT count(*) FROM offers_outbox").fetchone()[0] == 1


def test_deleting_a_sent_receipt_queues_a_delete(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    conn.execute("UPDATE receipt SET sent_to_offers_at = ? WHERE id = ?", (at(-60), rid))
    queue_put(conn, rid)  # an edit after it was sent
    with conn:
        outbox.queue_delete(conn, rid, NOW)
    assert op(conn, rid)["op"] == "delete"


def test_deleting_a_never_sent_receipt_drops_its_pending_put(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    with conn:
        outbox.queue_delete(conn, rid, NOW)
    assert op(conn, rid) is None


def test_deleting_a_receipt_offers_never_heard_of_queues_nothing(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    conn.execute("UPDATE receipt SET status = 'check' WHERE id = ?", (rid,))
    with conn:
        outbox.queue_delete(conn, rid, NOW)
    assert op(conn, rid) is None


# --- the payload ----------------------------------------------------------------------

def test_payload_follows_the_offers_contract(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path, Outcome(reading=Reading(
        total_cents=1184, receipt_discounts=[Discount("Lidl Plus", 50)])))
    assert outbox.payload(conn, rid, "https://rec.example") == {
        "id": rid, "date": "2026-10-14", "time": "18:42", "eik": "131129282",
        "store_name": "Kaufland България", "store_address": "София, ж.к. Младост",
        "total_cents": 1184, "photo_url": f"https://rec.example/receipts/{rid}",
        "lines": [
            {"seq": 1, "name": "Хляб Добруджа", "qty": 1.0, "unit": "pcs",
             "unit_price_cents": 139, "total_cents": 139, "discount_cents": 0},
            {"seq": 2, "name": "Мляко Верея 3%", "qty": 1.0, "unit": "pcs",
             "unit_price_cents": 395, "total_cents": 395, "discount_cents": 50},
            {"seq": 3, "name": "Сирене краве", "qty": 0.5, "unit": "kg",
             "unit_price_cents": 1500, "total_cents": 750, "discount_cents": 0},
        ],
        "discounts": [{"seq": 1, "name": "Lidl Plus", "cents": 50}],
    }


def test_photo_url_defaults_to_the_public_site(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("RECEIPTS_PUBLIC_URL", raising=False)
    rid = ok_receipt(conn, tmp_path)
    assert outbox.payload(conn, rid)["photo_url"] == f"https://rec.fireto.net/receipts/{rid}"
    monkeypatch.setenv("RECEIPTS_PUBLIC_URL", "http://localhost:8000/")
    assert outbox.payload(conn, rid)["photo_url"] == f"http://localhost:8000/receipts/{rid}"


# --- sending --------------------------------------------------------------------------

def test_a_sent_put_is_done_and_recorded(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    offers = FakeOffers()

    assert outbox.drain(conn, offers, NOW + timedelta(seconds=5)) == 1

    assert offers.calls == [("PUT", rid, outbox.payload(conn, rid))]
    assert op(conn, rid) is None
    assert receipts.get_row(conn, rid)["sent_to_offers_at"] == outbox.iso(
        NOW + timedelta(seconds=5))


def test_the_latest_state_is_sent(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    conn.execute("UPDATE receipt SET total_cents = 999 WHERE id = ?", (rid,))
    offers = FakeOffers()
    outbox.drain(conn, offers, NOW)
    assert offers.calls[0][2]["total_cents"] == 999


def test_a_sent_delete_is_done(conn, tmp_path):
    with conn:
        conn.execute("INSERT INTO offers_outbox (receipt_id, op, queued_at, next_try_at) "
                     "VALUES (41, 'delete', ?, ?)", (at(0), at(0)))
    offers = FakeOffers(204)
    assert outbox.drain(conn, offers, NOW) == 1
    assert offers.calls == [("DELETE", 41, None)]
    assert op(conn, 41) is None


def test_a_put_for_a_receipt_no_longer_ok_is_dropped_unsent(conn, tmp_path):
    # e.g. a re-read under way, or one that ended in check: offers keeps what it has
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    conn.execute("UPDATE receipt SET status = 'queued' WHERE id = ?", (rid,))
    offers = FakeOffers()
    assert outbox.drain(conn, offers, NOW) == 0
    assert offers.calls == [] and op(conn, rid) is None


@pytest.mark.parametrize("failure, error", [
    (urllib.error.URLError("connection refused"), "connection refused"),
    (TimeoutError("timed out"), "timed out"),
    (503, "HTTP 503"),
])
def test_a_network_error_or_5xx_is_retried_with_backoff(conn, tmp_path, failure, error):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    offers = FakeOffers(failure)

    waits, now = [], NOW
    for _ in range(6):
        assert outbox.drain(conn, offers, now) == 0
        o = op(conn, rid)
        assert error in o["last_error"]
        due = datetime.fromisoformat(o["next_try_at"])
        waits.append(int((due - now).total_seconds() // 60))
        assert outbox.drain(conn, offers, due - timedelta(seconds=1)) == 0  # not yet due
        now = due
    assert waits == [1, 5, 15, 60, 60, 60]
    assert op(conn, rid)["attempts"] == 6 and len(offers.calls) == 6
    assert receipts.get_row(conn, rid)["sent_to_offers_at"] is None


def test_a_4xx_is_kept_and_not_retried_until_the_receipt_changes(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    offers = FakeOffers((422, "bad unit"))

    outbox.drain(conn, offers, NOW)
    o = op(conn, rid)
    assert (o["attempts"], o["next_try_at"]) == (1, None)
    assert o["last_error"] == "HTTP 422: bad unit"
    assert outbox.drain(conn, offers, NOW + timedelta(days=30)) == 0
    assert len(offers.calls) == 1

    queue_put(conn, rid, NOW + timedelta(days=30))  # an edit
    offers.responses = [200]
    assert outbox.drain(conn, offers, NOW + timedelta(days=30)) == 1


def test_an_operation_replaced_while_sending_stays_queued(conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)
    offers = FakeOffers(on_send=lambda *_: queue_put(conn, rid, NOW + timedelta(seconds=1)))
    outbox.drain(conn, offers, NOW)
    assert op(conn, rid)["queued_at"] == outbox.iso(NOW + timedelta(seconds=1))
    assert receipts.get_row(conn, rid)["sent_to_offers_at"] is not None


def test_a_receipt_deleted_while_its_first_put_was_sending_is_deleted_in_offers(
        conn, tmp_path):
    rid = ok_receipt(conn, tmp_path)
    queue_put(conn, rid)

    def delete(*_):
        with conn:
            outbox.queue_delete(conn, rid, NOW)  # never sent yet: drops the put
            conn.execute("DELETE FROM receipt WHERE id = ?", (rid,))

    outbox.drain(conn, FakeOffers(on_send=delete), NOW)
    assert op(conn, rid)["op"] == "delete"


def test_one_failing_operation_does_not_stop_the_others(conn, tmp_path):
    a, b = ok_receipt(conn, tmp_path), ok_receipt(conn, tmp_path)
    queue_put(conn, a)
    queue_put(conn, b)
    offers = FakeOffers(OSError("reset"), 200)
    assert outbox.drain(conn, offers, NOW) == 1
    assert op(conn, a)["attempts"] == 1 and op(conn, b) is None


# --- the HTTP client ----------------------------------------------------------------------

class Opener:
    def __init__(self, status=200, body=b'{"stored": true}', error=None):
        self.status, self.body, self.error = status, body, error
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        resp = io.BytesIO(self.body)
        resp.status = self.status
        return resp


def test_client_puts_json_with_the_bearer_token():
    opener = Opener()
    client = outbox.OffersClient("http://10.20.10.11:8090/", "s3cret", opener=opener)
    assert client.send("PUT", 7, {"id": 7, "store_name": "Билла"}) == (200, '{"stored": true}')
    request, timeout = opener.requests[0]
    assert request.full_url == "http://10.20.10.11:8090/api/receipts/7"
    assert request.get_method() == "PUT" and timeout == 10
    assert request.get_header("Authorization") == "Bearer s3cret"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"id": 7, "store_name": "Билла"}


def test_client_deletes_without_a_body():
    opener = Opener(status=204, body=b"")
    client = outbox.OffersClient("http://offers", "t", opener=opener)
    assert client.send("DELETE", 7, None) == (204, "")
    assert opener.requests[0][0].get_method() == "DELETE"
    assert opener.requests[0][0].data is None


def test_client_returns_http_errors_as_a_status():
    error = urllib.error.HTTPError("http://offers/api/receipts/7", 401, "Unauthorized", {},
                                   io.BytesIO(b"bad token"))
    client = outbox.OffersClient("http://offers", "t", opener=Opener(error=error))
    assert client.send("PUT", 7, {}) == (401, "bad token")


def test_client_from_env(monkeypatch):
    monkeypatch.delenv("OFFERS_URL", raising=False)
    assert outbox.client_from_env() is None
    monkeypatch.setenv("OFFERS_URL", "http://10.20.10.11:8090")
    monkeypatch.setenv("OFFERS_TOKEN", "tok")
    client = outbox.client_from_env()
    assert (client.url, client.token) == ("http://10.20.10.11:8090", "tok")


# --- backfill -------------------------------------------------------------------------

def test_enqueue_all_queues_every_ok_receipt(conn, tmp_path):
    a, b, c = (ok_receipt(conn, tmp_path) for _ in range(3))
    conn.execute("UPDATE receipt SET status = 'check' WHERE id = ?", (b,))
    conn.execute("UPDATE receipt SET status = 'ok', confirmed_at = ? WHERE id = ?",
                 (at(0), c))
    receipts.create_manual(conn, FakeSheet(), date="2026-10-15", total_cents=500,
                           category="Разни", payment_method=None, notes=None, now=NOW)
    assert outbox.enqueue_all(conn, NOW) == 2
    assert [r[0] for r in conn.execute("SELECT receipt_id FROM offers_outbox "
                                       "ORDER BY receipt_id")] == [a, c]


def test_cli_enqueue_all_prints_the_count(conn, tmp_path, monkeypatch, capsys):
    from backend import config
    ok_receipt(conn, tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "receipts.db")
    assert outbox.main(["enqueue-all"]) == 0
    assert capsys.readouterr().out.strip() == "1 receipt(s) queued for offers"
