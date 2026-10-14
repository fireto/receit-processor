"""ЕИК → company: the check digit (a misread ЕИК sends the receipt to `check`), the VIES
lookup cached in the `company` table, and the registered name on the receipt."""

from datetime import timedelta

import pytest

from backend import companies, db, receipts
from backend.fakes import FakeSheet, Outcome, Reading
from backend.worker import Worker
from tests.helpers import FakeReader, photo
from tests.test_worker import NOW, receipt

LIDL = "131071587"
LIDL_MISREAD = "131071687"


@pytest.mark.parametrize("eik, valid", [
    (LIDL, True), (LIDL_MISREAD, False), ("131129282", True),
    ("000000000", True),           # the check digit alone cannot catch everything
    ("1310715870003", True),       # a branch: 9 + 4 digits, own check digit
    ("1310715870002", False),
    ("13107158", False), ("BG131071587", False), ("", False), (None, False),
])
def test_eik_check_digit(eik, valid):
    assert companies.eik_valid(eik) is valid


class FakeVies:
    def __init__(self, known=None, fail=False):
        self.known = known or {LIDL: ("ЛИДЛ БЪЛГАРИЯ ЕООД ЕНД КО - КД", "гр.СОФИЯ 1766")}
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, eik):
        self.calls.append(eik)
        if self.fail:
            raise TimeoutError("VIES down")
        return self.known.get(eik)  # (name, address) or None when not registered


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "receipts.db")
    db.migrate(c)
    yield c
    c.close()


def test_lookup_caches_found_and_not_found(conn):
    vies = FakeVies()
    assert companies.lookup(conn, LIDL, fetch=vies, now=NOW).name == \
        "ЛИДЛ БЪЛГАРИЯ ЕООД ЕНД КО - КД"
    assert companies.lookup(conn, LIDL, fetch=vies, now=NOW).address == "гр.СОФИЯ 1766"
    assert companies.lookup(conn, "131129282", fetch=vies, now=NOW) is None
    assert companies.lookup(conn, "131129282", fetch=vies, now=NOW) is None
    assert vies.calls == [LIDL, "131129282"]  # each asked once
    row = conn.execute("SELECT * FROM company WHERE eik = ?", (LIDL,)).fetchone()
    assert (row["source"], row["found"]) == ("vies", 1)


def test_not_found_is_asked_again_after_30_days(conn):
    vies = FakeVies()
    companies.lookup(conn, "131129282", fetch=vies, now=NOW)
    companies.lookup(conn, "131129282", fetch=vies, now=NOW + timedelta(days=29))
    companies.lookup(conn, "131129282", fetch=vies, now=NOW + timedelta(days=31))
    assert vies.calls == ["131129282", "131129282"]


def test_a_vies_error_is_not_cached(conn):
    assert companies.lookup(conn, LIDL, fetch=FakeVies(fail=True), now=NOW) is None
    vies = FakeVies()
    assert companies.lookup(conn, LIDL, fetch=vies, now=NOW).name.startswith("ЛИДЛ")
    assert vies.calls == [LIDL]


def test_an_invalid_eik_is_never_looked_up(conn):
    vies = FakeVies()
    assert companies.lookup(conn, LIDL_MISREAD, fetch=vies, now=NOW) is None
    assert vies.calls == []


def test_search_by_name_finds_cached_companies(conn):
    companies.lookup(conn, LIDL, fetch=FakeVies(), now=NOW)
    assert [c.eik for c in companies.search(conn, "лидл")] == [LIDL]
    assert companies.search(conn, "kaufland") == []


# --- in the reading flow --------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    path = tmp_path / "receipts.db"
    c = db.connect(path)
    db.migrate(c)
    sheet = FakeSheet()
    photos_dir = tmp_path / "photos"
    vies = FakeVies()

    def run(reading):
        w = Worker(path, photos_dir, sheet, read=FakeReader(Outcome(reading=reading)),
                   decode_qr=lambda image: None, clock=lambda: NOW, company_lookup=vies)
        rid = receipts.create(c, photos_dir, "camera", [photo()], now=NOW)
        w.process_next()
        return rid

    yield c, sheet, vies, run
    c.close()


def test_the_registered_name_replaces_the_name_as_read(env):
    conn, sheet, vies, run = env
    rid = run(Reading(eik=LIDL, store_name="Лидл България ЕООД енд КО. КД."))
    r = receipt(conn, rid)
    assert r["status"] == "ok" and r["store_name"] == "ЛИДЛ БЪЛГАРИЯ ЕООД ЕНД КО - КД"


def test_an_unknown_company_keeps_the_name_as_read(env):
    conn, sheet, vies, run = env
    rid = run(Reading(eik="131129282", store_name="Пицария Его"))
    assert receipt(conn, rid)["store_name"] == "Пицария Его"
    assert receipt(conn, rid)["status"] == "ok"


def test_a_misread_eik_sends_the_receipt_to_check(env):
    conn, sheet, vies, run = env
    rid = run(Reading(eik=LIDL_MISREAD))
    r = receipt(conn, rid)
    assert r["status"] == "check" and r["sheet_row"] is None
    assert len(sheet.rows) == 1
    assert vies.calls == []


def test_vies_down_does_not_stop_the_receipt(env):
    conn, sheet, vies, run = env
    vies.fail = True
    rid = run(Reading(eik=LIDL, store_name="Лидл"))
    r = receipt(conn, rid)
    assert r["status"] == "ok" and r["store_name"] == "Лидл"


def test_correcting_the_eik_looks_up_the_company(env):
    conn, sheet, vies, run = env
    rid = run(Reading(eik=LIDL_MISREAD, store_name="Лидл"))
    r = receipt(conn, rid)
    receipts.update_header(conn, sheet, rid, date=r["date"], total_cents=r["total_cents"],
                           category=r["category"], payment_method=r["payment_method"],
                           notes=r["notes"], eik=LIDL, company_lookup=vies, now=NOW)
    r = receipt(conn, rid)
    assert r["eik"] == LIDL and r["store_name"] == "ЛИДЛ БЪЛГАРИЯ ЕООД ЕНД КО - КД"
    assert r["status"] == "check"  # still waits for "Потвърди"
