"""Web UI routes (backend/ui.py, backend/main.py): pages, HTMX fragments, form posts."""

import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import config, db, receipts
from backend.fakes import FakeSheet, Outcome, Reading
from backend.main import app
from backend.worker import Worker
from tests.helpers import FakeReader, image_bytes, photo

HX = {"HX-Request": "true"}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "receipts.db")
    monkeypatch.setattr(config, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setenv("RECEIPTS_READER", "0")
    c = db.connect(config.DB_PATH)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def sheet(conn):
    s = FakeSheet()
    app.state.sheet = s
    app.state.worker = None
    yield s
    del app.state.sheet


@pytest.fixture
def client(sheet):
    return TestClient(app, follow_redirects=False)


def worker(sheet, reader=None):
    return Worker(config.DB_PATH, config.PHOTOS_DIR, sheet, read=reader or FakeReader(),
                  decode_qr=lambda image: None)


def upload(client, n=1, source="camera", long=False, headers=None, content=None):
    files = [("photos", (f"p{i}.jpg", content or image_bytes(), "image/jpeg"))
             for i in range(n)]
    data = {"source": source}
    if long:
        data["long"] = "1"
    return client.post("/receipts", data=data, files=files, headers=headers or {})


def count(conn, sql="SELECT count(*) FROM receipt", *args):
    return conn.execute(sql, args).fetchone()[0]


def body(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.text


# --- home and capture ---------------------------------------------------------------------

def test_home_offers_camera_and_gallery_capture(client):
    resp = client.get("/")
    html = body(resp)

    assert '<html lang="bg"' in html and 'rel="manifest"' in html
    assert resp.headers["cache-control"] == "no-cache"
    assert "Снимай бележка" in html and "От галерията" in html
    assert 'capture="environment"' in html
    assert re.search(r'<input[^>]*name="photos"[^>]*multiple', html)
    assert "една дълга бележка" in html
    assert 'enctype="multipart/form-data"' in html  # works without JS
    assert "Още няма бележки" in html


def test_camera_upload_queues_one_receipt_and_redirects_home(client, conn):
    resp = upload(client)

    assert resp.status_code == 303 and resp.headers["location"] == "/"
    r = conn.execute("SELECT * FROM receipt").fetchone()
    assert (r["status"], r["source"]) == ("queued", "camera")
    assert count(conn, "SELECT count(*) FROM receipt_photo") == 1


def test_camera_upload_of_three_photos_is_one_long_receipt(client, conn):
    upload(client, n=3)
    assert count(conn) == 1
    assert count(conn, "SELECT count(*) FROM receipt_photo") == 3


def test_more_than_three_photos_for_one_receipt_is_refused(client, conn):
    resp = upload(client, n=4)
    assert "най-много 3 снимки" in body(resp, 400)
    assert count(conn) == 0


def test_gallery_photos_are_separate_receipts(client, conn):
    upload(client, n=3, source="gallery")
    assert count(conn) == 3
    assert count(conn, "SELECT count(*) FROM receipt WHERE source = 'gallery'") == 3


def test_gallery_long_receipt_toggle_makes_one_receipt(client, conn):
    upload(client, n=2, source="gallery", long=True)
    assert count(conn) == 1
    assert count(conn, "SELECT count(*) FROM receipt_photo") == 2


def test_a_file_that_is_not_an_image_is_refused(client, conn):
    resp = upload(client, content=b"%PDF-1.4 nope")
    assert "не е снимка" in body(resp, 400)
    assert count(conn) == 0


def test_upload_without_photos_is_refused(client, conn):
    resp = client.post("/receipts", data={"source": "camera"})
    assert resp.status_code in (400, 422)
    assert count(conn) == 0


def test_htmx_upload_returns_the_polling_recent_list(client, conn):
    resp = upload(client, headers=HX)

    html = body(resp)
    assert 'id="recent"' in html
    assert 'hx-trigger="every 2s"' in html
    assert "обработва се…" in html


def test_upload_wakes_the_reader(client, sheet):
    class Waker:
        woken = 0

        def wake(self):
            self.woken += 1

        def alive(self):
            return True

    app.state.worker = Waker()
    upload(client)
    assert app.state.worker.woken == 1


def test_recent_list_shows_status_chips_and_stops_polling_when_idle(client, conn, sheet):
    upload(client)
    upload(client)
    upload(client)
    w = worker(sheet, FakeReader(Outcome(), Outcome(status="check", reading=Reading(total_cents=999)),
                                 Outcome(status="retake", reading=Reading(quality=["blurry"]))))
    for _ in range(3):
        w.process_next()
    conn.execute("INSERT INTO receipt (created_at, status, source) "
                 "VALUES ('2026-01-01T00:00:00+00:00', 'failed', 'camera')")
    conn.commit()

    html = body(client.get("/receipts/recent", headers=HX))
    assert "hx-trigger" not in html
    for label in ("✓", "провери", "снимай отново", "грешка"):
        assert label in html
    assert "12,34 €" in html and "Kaufland" in html


# --- receipt page ----------------------------------------------------------------------------

@pytest.fixture
def read_receipt(client, conn, sheet):
    upload(client, n=2)
    worker(sheet).process_next()
    return conn.execute("SELECT id FROM receipt").fetchone()[0]


def test_receipt_page_shows_photos_fields_lines_and_sum_check(client, read_receipt):
    rid = read_receipt
    html = body(client.get(f"/receipts/{rid}"))

    assert f'src="/receipts/{rid}/photos/1"' in html and f'src="/receipts/{rid}/photos/2"' in html
    assert 'name="date" value="2026-10-14"' in html
    assert 'name="total" value="12,34"' in html
    assert '<option value="Храна" selected>' in html
    assert '<option value="FIB 0889" selected>' in html
    assert "Мляко Верея 3%" in html and "−0,50 €" in html
    assert "Сборът съвпада" in html
    assert "ред 2" in html  # the Sheet row
    assert 'data-confirm=' in html and f'action="/receipts/{rid}/delete"' in html
    assert f'action="/receipts/{rid}/reread"' in html
    assert f'action="/receipts/{rid}/retake"' in html


def test_unknown_receipt_is_404(client):
    assert client.get("/receipts/999").status_code == 404
    assert client.get("/receipts/999/photos/1").status_code == 404


def test_photo_is_served_as_webp(client, read_receipt):
    resp = client.get(f"/receipts/{read_receipt}/photos/2")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.content[:4] == b"RIFF"
    assert client.get(f"/receipts/{read_receipt}/photos/3").status_code == 404


def test_check_receipt_offers_confirm_and_shows_the_mismatch(client, conn, sheet):
    upload(client)
    worker(sheet, FakeReader(Outcome(status="check", reading=Reading(total_cents=1300)))) \
        .process_next()
    rid = conn.execute("SELECT id FROM receipt").fetchone()[0]

    html = body(client.get(f"/receipts/{rid}"))
    assert f'action="/receipts/{rid}/confirm"' in html
    assert "Разлика" in html and "0,66 €" in html

    resp = client.post(f"/receipts/{rid}/confirm")
    assert resp.status_code == 303 and resp.headers["location"] == f"/receipts/{rid}"
    assert conn.execute("SELECT status, sheet_row FROM receipt").fetchone()[:] == ("ok", 2)


def test_edit_saves_and_updates_the_sheet_row(client, conn, sheet, read_receipt):
    resp = client.post(f"/receipts/{read_receipt}", data={
        "date": "2026-10-13", "total": "9,99", "category": "Аптека",
        "payment_method": "Revolut", "notes": "лекарства"})

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/receipts/{read_receipt}?saved=1"
    r = conn.execute("SELECT total_cents, category FROM receipt").fetchone()
    assert tuple(r) == (999, "Аптека")
    assert sheet.rows[1][:6] == ["13.10.2026", "Аптека", "", "9,99", "", "Revolut"]
    assert "Запазено" in body(client.get(resp.headers["location"]))


def test_edit_with_a_bad_total_is_refused(client, conn, read_receipt):
    resp = client.post(f"/receipts/{read_receipt}", data={
        "date": "2026-10-13", "total": "дванадесет", "category": "Аптека",
        "payment_method": "", "notes": ""})
    assert "Сумата" in body(resp, 400)
    assert conn.execute("SELECT total_cents FROM receipt").fetchone()[0] == 1234


def test_edit_while_the_sheet_is_down_says_so(client, sheet, read_receipt):
    sheet.fail = True
    resp = client.post(f"/receipts/{read_receipt}", data={
        "date": "2026-10-13", "total": "9,99", "category": "Аптека",
        "payment_method": "", "notes": ""})
    assert resp.headers["location"] == f"/receipts/{read_receipt}?saved=pending"
    assert "ще бъде обновена" in body(client.get(resp.headers["location"]))


def test_reread_queues_at_the_next_step(client, conn, read_receipt):
    resp = client.post(f"/receipts/{read_receipt}/reread")
    assert resp.status_code == 303
    assert tuple(conn.execute("SELECT status, start_at FROM receipt").fetchone()) == (
        "queued", 1)


def test_queued_receipt_page_polls_its_state(client, conn):
    upload(client)
    rid = conn.execute("SELECT id FROM receipt").fetchone()[0]
    html = body(client.get(f"/receipts/{rid}"))
    assert f'hx-get="/receipts/{rid}/state"' in html and "обработва се…" in html
    assert "hx-trigger" in body(client.get(f"/receipts/{rid}/state", headers=HX))


def test_state_of_a_finished_receipt_refreshes_the_page(client, read_receipt):
    resp = client.get(f"/receipts/{read_receipt}/state", headers=HX)
    assert resp.headers.get("hx-refresh") == "true"


def test_retake_replaces_the_photos_and_keeps_the_id(client, conn, read_receipt):
    files = [("photos", ("n.jpg", image_bytes(80, 100), "image/jpeg"))]
    resp = client.post(f"/receipts/{read_receipt}/retake", files=files)

    assert resp.status_code == 303 and resp.headers["location"] == f"/receipts/{read_receipt}"
    r = conn.execute("SELECT id, status, sheet_row FROM receipt").fetchone()
    assert tuple(r) == (read_receipt, "queued", 2)
    assert count(conn, "SELECT count(*) FROM receipt_photo") == 1


def test_failed_receipt_can_be_retried(client, conn, sheet):
    upload(client)
    worker(sheet, FakeReader(error=RuntimeError("down"))).process_next()
    rid = conn.execute("SELECT id FROM receipt").fetchone()[0]
    assert "Опитай пак" in body(client.get(f"/receipts/{rid}"))

    client.post(f"/receipts/{rid}/retry")
    assert conn.execute("SELECT status FROM receipt").fetchone()[0] == "queued"


def test_delete_removes_the_receipt_and_its_row(client, conn, sheet, read_receipt):
    resp = client.post(f"/receipts/{read_receipt}/delete")
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    assert count(conn) == 0 and len(sheet.rows) == 1


def test_delete_while_the_sheet_is_down_keeps_the_receipt(client, conn, sheet, read_receipt):
    sheet.fail = True
    resp = client.post(f"/receipts/{read_receipt}/delete")
    assert "таблицата не отговаря" in body(resp, 503)
    assert count(conn) == 1


# --- manual entry ------------------------------------------------------------------------------

def test_manual_entry_form_and_post(client, conn, sheet):
    html = body(client.get("/manual"))
    assert '<option value="Гориво">' in html and 'name="total"' in html

    resp = client.post("/manual", data={"date": "2026-10-10", "total": "45", "category": "Гориво",
                                        "payment_method": "Cash", "notes": "бензин"})
    assert resp.status_code == 303
    assert sheet.rows[1][:4] == ["10.10.2026", "Гориво", "", "45,00"]
    assert conn.execute("SELECT source FROM receipt").fetchone()[0] == "manual"


def test_manual_entry_needs_a_total(client, conn):
    resp = client.post("/manual", data={"date": "2026-10-10", "total": "", "category": "Гориво"})
    assert resp.status_code == 400 and count(conn) == 0


# --- guard, health, PWA ------------------------------------------------------------------------

def test_cross_site_posts_are_refused(client, conn):
    files = [("photos", ("p.jpg", image_bytes(), "image/jpeg"))]
    resp = client.post("/receipts", data={"source": "camera"}, files=files,
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    resp = client.post("/receipts", data={"source": "camera"}, files=files,
                       headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403
    assert count(conn) == 0
    resp = client.post("/receipts", data={"source": "camera"}, files=files,
                       headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 303


def test_old_api_and_pin_are_gone(client):
    assert client.get("/api/config").status_code == 404


def test_livez(client):
    assert client.get("/livez").json() == {"status": "ok"}


def test_healthz_without_a_reader_is_503(client):
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["reader"] == "dead"


def test_healthz_with_a_running_reader(client, sheet):
    w = worker(sheet)
    w.idle_wait = 0.05
    w.start()
    app.state.worker = w
    try:
        resp = client.get("/healthz")
    finally:
        w.stop()
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "ok"


def test_manifest_and_service_worker(client):
    m = client.get("/manifest.webmanifest").json()
    assert m["lang"] == "bg" and m["theme_color"] == "#059669"
    sw = client.get("/sw.js")
    assert sw.headers["content-type"].startswith("text/javascript")
    assert "/static/" in sw.text and 'searchParams.has("v")' in sw.text


def test_static_files_are_versioned_and_immutable(client):
    html = body(client.get("/"))
    src = re.search(r'src="(/static/vendor/htmx\.min\.js\?v=[0-9a-f]{12})"', html)[1]
    resp = client.get(src)
    assert resp.status_code == 200
    assert "immutable" in resp.headers["cache-control"]


def test_duplicate_page_links_the_original_and_can_be_dismissed(client, conn, sheet):
    upload(client)
    upload(client)
    w = worker(sheet)
    w.process_next()
    w.process_next()
    first, second = [r[0] for r in conn.execute("SELECT id FROM receipt ORDER BY id")]

    html = body(client.get(f"/receipts/{second}"))
    assert "Вече имаш тази бележка" in html
    assert f'href="/receipts/{first}"' in html
    assert f'action="/receipts/{second}/not-duplicate"' in html
    assert "/reread" not in html and "/retake" not in html
    assert "дубликат" in body(client.get("/"))

    resp = client.post(f"/receipts/{second}/not-duplicate")
    assert resp.status_code == 303 and resp.headers["location"] == f"/receipts/{second}"
    assert conn.execute("SELECT status, sheet_row FROM receipt WHERE id = ?",
                        (second,)).fetchone()[:] == ("ok", 3)


def test_misread_eik_is_explained_and_can_be_corrected(client, conn, sheet):
    upload(client)
    worker(sheet, FakeReader(Outcome(reading=Reading(eik="131071687")))).process_next()
    rid = conn.execute("SELECT id FROM receipt").fetchone()[0]
    assert conn.execute("SELECT status FROM receipt").fetchone()[0] == "check"

    html = body(client.get(f"/receipts/{rid}"))
    assert "ЕИК 131071687 изглежда прочетен грешно" in html
    assert 'name="eik" value="131071687"' in html

    r = conn.execute("SELECT * FROM receipt").fetchone()
    resp = client.post(f"/receipts/{rid}", data={
        "date": r["date"], "total": "12,34", "category": "Храна", "payment_method": "",
        "notes": "", "eik": "BG 131071587"})
    assert resp.status_code == 303
    assert conn.execute("SELECT eik FROM receipt").fetchone()[0] == "131071587"
    assert "изглежда прочетен грешно" not in body(client.get(f"/receipts/{rid}"))


def test_receipt_page_shows_the_registered_company_under_the_venue(client, conn, sheet):
    conn.execute("INSERT INTO company VALUES ('131071587', 'ЛИДЛ БЪЛГАРИЯ ЕООД ЕНД КО - КД', "
                 "'гр.СОФИЯ', 'vies', 1, '2026-10-14T10:00:00+00:00')")
    conn.commit()
    upload(client)
    worker(sheet, FakeReader(Outcome(reading=Reading(eik="131071587",
                                                     store_name="Лидл Варна 0204")))).process_next()
    rid = conn.execute("SELECT id FROM receipt").fetchone()[0]
    html = body(client.get(f"/receipts/{rid}"))
    assert "Лидл Варна 0204" in html and "ЛИДЛ БЪЛГАРИЯ ЕООД ЕНД КО - КД" in html


def _add(conn, n, status="ok"):
    for i in range(n):
        conn.execute("INSERT INTO receipt (created_at, status, source, store_name, total_cents) "
                     "VALUES (?, ?, 'camera', ?, 100)",
                     (f"2026-09-{1 + i % 28:02d}T10:{i % 60:02d}:00+00:00", status, f"Магазин {i}"))
    conn.commit()


def test_home_links_all_receipts(client, conn):
    _add(conn, 25)
    html = body(client.get("/"))
    assert 'href="/receipts"' in html and "Всички бележки (25)" in html


def test_all_receipts_pages_through_everything(client, conn):
    _add(conn, 60)
    html = body(client.get("/receipts"))
    assert html.count('href="/receipts/') == 50
    assert 'href="/receipts?page=2"' in html
    html2 = body(client.get("/receipts?page=2"))
    assert html2.count("Магазин") == 10
    assert 'href="/receipts?page=3"' not in html2


def test_all_receipts_filters_by_status(client, conn):
    _add(conn, 3)
    _add(conn, 2, status="check")
    html = body(client.get("/receipts?status=check"))
    assert html.count("Магазин") == 2
    assert 'href="/receipts?status=check"' in html  # the chip, marked current
    assert "провери (2)" in html
    assert body(client.get("/receipts?status=bogus")).count("Магазин") == 5


def test_the_worker_sends_to_offers_only_when_offers_url_is_set(conn, monkeypatch):
    from backend import main
    monkeypatch.setenv("RECEIPTS_READER", "fake")
    monkeypatch.delenv("OFFERS_URL", raising=False)
    assert main.make_worker(FakeSheet()).offers is None
    monkeypatch.setenv("OFFERS_URL", "http://10.20.10.11:8090")
    monkeypatch.setenv("OFFERS_TOKEN", "tok")
    offers = main.make_worker(FakeSheet()).offers
    assert (offers.url, offers.token) == ("http://10.20.10.11:8090", "tok")
