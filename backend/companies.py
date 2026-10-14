"""ЕИК → company: the check digit, and the registered name looked up in VIES (the EU VAT
check service, free and keyless) and cached in the `company` table.

VIES knows only VAT-registered companies; a small shop below the VAT threshold is "not
found" (cached, asked again after NOT_FOUND_RETRY). A failed request is not cached.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/BG/vat/{eik}"
NOT_FOUND_RETRY = timedelta(days=30)
TIMEOUT_SECONDS = 15


@dataclass
class Company:
    eik: str
    name: str
    address: str | None


def _check_digit(digits: list[int], weights, fallback) -> int:
    s = sum(d * w for d, w in zip(digits, weights)) % 11
    if s == 10:
        s = sum(d * w for d, w in zip(digits, fallback)) % 11
    return 0 if s == 10 else s


def eik_valid(eik: str | None) -> bool:
    """A 9-digit ЕИК or a 13-digit branch code with the right check digit(s)."""
    if not eik or not eik.isdigit() or len(eik) not in (9, 13):
        return False
    d = [int(x) for x in eik]
    if _check_digit(d[:8], range(1, 9), range(3, 11)) != d[8]:
        return False
    if len(eik) == 13:
        return _check_digit(d[8:12], (2, 7, 3, 5), (4, 9, 5, 7)) == d[12]
    return True


def vies_fetch(eik: str) -> tuple[str, str | None] | None:
    """(name, address) of a VAT-registered company, None when VIES does not know it.
    Raises on a network or service error."""
    import httpx

    r = httpx.get(VIES_URL.format(eik=eik), timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    data = r.json()
    if data.get("userError") not in ("VALID", "INVALID"):
        raise RuntimeError(f"VIES: {data.get('userError')}")
    name = (data.get("name") or "").strip()
    if not data.get("isValid") or name in ("", "---"):
        return None
    address = " ".join((data.get("address") or "").split()) or None
    return name, address


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def lookup(conn, eik: str | None, *, fetch=vies_fetch, now: datetime | None = None
           ) -> Company | None:
    """The registered company of a valid ЕИК: from the cache, else asked once (never when
    `fetch` is None: tests and local development)."""
    if not eik_valid(eik):
        return None
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM company WHERE eik = ?", (eik,)).fetchone()
    if row is not None and (row["found"] or
                            now - datetime.fromisoformat(row["checked_at"]) < NOT_FOUND_RETRY):
        return Company(eik, row["name"], row["address"]) if row["found"] else None
    if fetch is None:
        return None
    try:
        found = fetch(eik)
    except Exception:
        log.warning("ЕИК %s: company lookup failed", eik, exc_info=True)
        return None
    with conn:
        conn.execute(
            "INSERT INTO company (eik, name, address, source, found, checked_at) "
            "VALUES (?, ?, ?, 'vies', ?, ?) ON CONFLICT (eik) DO UPDATE SET name = excluded.name, "
            "address = excluded.address, source = excluded.source, found = excluded.found, "
            "checked_at = excluded.checked_at",
            (eik, found[0] if found else None, found[1] if found else None,
             1 if found else 0, _iso(now)))
    return Company(eik, *found) if found else None


def search(conn, text: str) -> list[Company]:
    """Name → ЕИК among the companies already looked up (case-insensitive)."""
    needle = text.casefold().strip()
    if not needle:
        return []
    rows = conn.execute("SELECT eik, name, address FROM company WHERE found = 1 ORDER BY name")
    return [Company(r["eik"], r["name"], r["address"]) for r in rows
            if needle in r["name"].casefold()]
