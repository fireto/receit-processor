"""Web UI: server-rendered Jinja pages with HTMX fragments. All text is Bulgarian.

Every mutation is a POST form that also works without JavaScript (303 back); with HTMX
(`HX-Request`) an upload returns just the "Последни" list, which polls itself every 2 s
while a receipt is being read. The camera capture (several photos per receipt) is a small
JS island in static/app.js; without JS the plain file forms still work.
"""

import hashlib
import logging
import sqlite3
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from backend import config, db, fmt, photos, receipts, sheet_sync

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
STATIC_DIR = BASE / "static"

STATUS_LABELS = {"queued": "обработва се…", "reading": "обработва се…", "ok": "✓",
                 "check": "провери", "retake": "снимай отново", "failed": "грешка"}
QUALITY_LABELS = {"blurry": "размазана", "cut_top": "отрязан горен край",
                  "cut_bottom": "отрязан долен край", "glare": "отблясък",
                  "not_a_receipt": "не е бележка"}


def _asset_version() -> str:
    """Content hash of the static files: part of every static URL, and the SW cache name."""
    h = hashlib.sha256()
    for p in sorted(STATIC_DIR.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(STATIC_DIR).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


ASSET_VERSION = _asset_version()


def static_url(path: str) -> str:
    return f"/static/{path}?v={ASSET_VERSION}"


templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals.update(static=static_url, STATUS_LABELS=STATUS_LABELS,
                             QUALITY_LABELS=QUALITY_LABELS, MAX_PHOTOS=receipts.MAX_PHOTOS)
templates.env.filters.update(eur=fmt.eur, amount=fmt.amount, bgdate=fmt.bgdate,
                             local=fmt.local, qty=fmt.qty)

router = APIRouter()


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


Conn = Depends(get_conn)


def get_sheet(request: Request):
    if getattr(request.app.state, "sheet", None) is None:
        request.app.state.sheet = sheet_sync.make_sheet()
    return request.app.state.sheet


def wake_reader(request: Request) -> None:
    w = getattr(request.app.state, "worker", None)
    if w:
        w.wake()


def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _options(load) -> list[str]:
    try:
        return load()
    except Exception:
        log.warning("Sheet named range unavailable", exc_info=True)
        return []


def _error(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    return render(request, "error.html", status_code=status_code, message=message)


# --- home and capture ----------------------------------------------------------------------

def _recent_ctx(conn) -> dict:
    return {"recent": receipts.recent(conn), "busy": receipts.busy(conn)}


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn: sqlite3.Connection = Conn):
    return render(request, "home.html", **_recent_ctx(conn))


@router.get("/receipts/recent", response_class=HTMLResponse)
def recent(request: Request, conn: sqlite3.Connection = Conn):
    return render(request, "_recent.html", **_recent_ctx(conn))


async def _read_photos(files: list[UploadFile]) -> list[photos.Photo]:
    out = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        out.append(photos.process(data))
    return out


@router.post("/receipts")
async def upload(request: Request, photos_: list[UploadFile] = File(..., alias="photos"),
                 source: str = Form("camera"), long: str = Form(""),
                 conn: sqlite3.Connection = Conn):
    """Camera: the 1–3 photos are one receipt. Gallery: each photo is its own receipt,
    unless "една дълга бележка" is ticked."""
    if source not in ("camera", "gallery"):
        source = "gallery"
    try:
        processed = await _read_photos(photos_)
    except photos.NotAnImage:
        return _error(request, "Файлът не е снимка.")
    if not processed:
        return _error(request, "Няма избрана снимка.")
    groups = [processed] if source == "camera" or long else [[p] for p in processed]
    if any(len(g) > receipts.MAX_PHOTOS for g in groups):
        return _error(request, f"Една бележка е най-много {receipts.MAX_PHOTOS} снимки.")
    for g in groups:
        receipts.create(conn, config.PHOTOS_DIR, source, g)
    wake_reader(request)
    if is_htmx(request):
        return render(request, "_recent.html", **_recent_ctx(conn))
    return see_other("/")


# --- one receipt ---------------------------------------------------------------------------

def _receipt(conn, rid: int) -> dict:
    found = receipts.get(conn, rid)
    if found is None:
        raise HTTPException(404, "Няма такава бележка")
    return found


@router.get("/receipts/{rid}", response_class=HTMLResponse)
def receipt_page(request: Request, rid: int, saved: str = "", conn: sqlite3.Connection = Conn):
    found = _receipt(conn, rid)
    sheet = get_sheet(request)
    return render(request, "receipt.html", **found, saved=saved,
                  categories=_options(sheet.categories),
                  payment_methods=_options(sheet.payment_methods))


@router.get("/receipts/{rid}/state", response_class=HTMLResponse)
def receipt_state(request: Request, rid: int, conn: sqlite3.Connection = Conn):
    """Polled by the receipt page while it is being read; reloads it when done."""
    r = receipts.get_row(conn, rid)
    if r is None or r["status"] not in ("queued", "reading"):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return render(request, "_state.html", receipt=r)


@router.get("/receipts/{rid}/photos/{seq}")
def receipt_photo(rid: int, seq: int, conn: sqlite3.Connection = Conn):
    row = conn.execute("SELECT path FROM receipt_photo WHERE receipt_id = ? AND seq = ?",
                       (rid, seq)).fetchone()
    path = config.PHOTOS_DIR / row["path"] if row else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Няма такава снимка")
    return FileResponse(path, media_type="image/webp")


def _parse_header(date_: str, total: str) -> tuple[str | None, int | None, str | None]:
    """(date, cents, error)"""
    cents = fmt.parse_cents(total)
    if cents is None:
        return None, None, "Сумата трябва да е число, напр. 12,34."
    try:
        day = date.fromisoformat(date_.strip()).isoformat()
    except ValueError:
        return None, None, "Датата не е валидна."
    return day, cents, None


@router.post("/receipts/{rid}")
def edit_receipt(request: Request, rid: int, date_: str = Form("", alias="date"),
                 total: str = Form(""), category: str = Form(""),
                 payment_method: str = Form(""), notes: str = Form(""),
                 conn: sqlite3.Connection = Conn):
    found = _receipt(conn, rid)
    day, cents, error = _parse_header(date_, total)
    if error:
        sheet = get_sheet(request)
        return render(request, "receipt.html", status_code=400, **found, error=error,
                      categories=_options(sheet.categories),
                      payment_methods=_options(sheet.payment_methods))
    in_sync = receipts.update_header(conn, get_sheet(request), rid, date=day, total_cents=cents,
                                     category=category.strip(),
                                     payment_method=payment_method.strip(),
                                     notes=notes.strip())
    pending = found["receipt"]["status"] == "ok" and not in_sync
    return see_other(f"/receipts/{rid}?saved={'pending' if pending else '1'}")


@router.post("/receipts/{rid}/confirm")
def confirm(request: Request, rid: int, conn: sqlite3.Connection = Conn):
    _receipt(conn, rid)
    receipts.confirm(conn, get_sheet(request), rid)
    return see_other(f"/receipts/{rid}")


@router.post("/receipts/{rid}/reread")
def reread(request: Request, rid: int, conn: sqlite3.Connection = Conn):
    _receipt(conn, rid)
    if receipts.reread(conn, rid):
        wake_reader(request)
    return see_other(f"/receipts/{rid}")


@router.post("/receipts/{rid}/retry")
def retry(request: Request, rid: int, conn: sqlite3.Connection = Conn):
    _receipt(conn, rid)
    if receipts.retry(conn, rid):
        wake_reader(request)
    return see_other(f"/receipts/{rid}")


@router.post("/receipts/{rid}/retake")
async def retake(request: Request, rid: int,
                 photos_: list[UploadFile] = File(..., alias="photos"),
                 conn: sqlite3.Connection = Conn):
    _receipt(conn, rid)
    try:
        processed = await _read_photos(photos_)
    except photos.NotAnImage:
        return _error(request, "Файлът не е снимка.")
    if not processed:
        return _error(request, "Няма избрана снимка.")
    if len(processed) > receipts.MAX_PHOTOS:
        return _error(request, f"Една бележка е най-много {receipts.MAX_PHOTOS} снимки.")
    receipts.replace_photos(conn, config.PHOTOS_DIR, rid, processed)
    wake_reader(request)
    return see_other(f"/receipts/{rid}")


@router.post("/receipts/{rid}/delete")
def delete(request: Request, rid: int, conn: sqlite3.Connection = Conn):
    _receipt(conn, rid)
    try:
        warning = receipts.delete(conn, get_sheet(request), config.PHOTOS_DIR, rid)
    except receipts.SheetUnavailable:
        return _error(request, "Google таблицата не отговаря — бележката не е изтрита. "
                               "Опитай пак след малко.", status_code=503)
    if warning:
        return render(request, "error.html", message=warning, title="Бележката е изтрита")
    return see_other("/")


# --- manual entry ----------------------------------------------------------------------------

@router.get("/manual", response_class=HTMLResponse)
def manual_form(request: Request):
    sheet = get_sheet(request)
    return render(request, "manual.html", form={"date": datetime.now(receipts.SOFIA).date()
                                                .isoformat()},
                  categories=_options(sheet.categories),
                  payment_methods=_options(sheet.payment_methods))


@router.post("/manual")
def manual_entry(request: Request, date_: str = Form("", alias="date"), total: str = Form(""),
                 category: str = Form(""), payment_method: str = Form(""),
                 notes: str = Form(""), conn: sqlite3.Connection = Conn):
    sheet = get_sheet(request)
    day, cents, error = _parse_header(date_, total)
    if error or not category.strip():
        return render(request, "manual.html", status_code=400,
                      error=error or "Избери категория.",
                      form={"date": date_, "total": total, "category": category,
                            "payment_method": payment_method, "notes": notes},
                      categories=_options(sheet.categories),
                      payment_methods=_options(sheet.payment_methods))
    rid = receipts.create_manual(conn, sheet, date=day, total_cents=cents,
                                 category=category.strip(),
                                 payment_method=payment_method.strip(), notes=notes.strip())
    return see_other(f"/receipts/{rid}")


# --- PWA ------------------------------------------------------------------------------------

@router.get("/manifest.webmanifest")
def manifest():
    icon = static_url
    return JSONResponse({
        "name": "Бележки", "short_name": "Бележки", "lang": "bg", "dir": "ltr",
        "description": "Снимай касова бележка — разходът отива в таблицата",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#fafafa", "theme_color": "#059669",
        "icons": [
            {"src": icon("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": icon("icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": icon("icons/maskable-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
            {"src": icon("icons/icon.svg"), "sizes": "any", "type": "image/svg+xml"},
        ],
    }, media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker(request: Request):
    resp = render(request, "sw.js", version=ASSET_VERSION)
    resp.headers["content-type"] = "text/javascript; charset=utf-8"
    return resp


# --- middleware helpers (installed by backend.main) -------------------------------------------

def cross_site(request: Request) -> bool:
    """A state-changing request from another site (basic-auth credentials ride along)."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = request.headers.get("origin")
    return bool(origin) and urlparse(origin).netloc != request.headers.get("host")


def cache_control(request: Request, response: Response) -> None:
    if "cache-control" in response.headers:
        return
    if request.url.path.startswith("/static/") and request.query_params.get("v"):
        response.headers["cache-control"] = "public, max-age=31536000, immutable"
    elif request.url.path.startswith("/receipts/") and "/photos/" in request.url.path:
        # a re-take replaces the photo under the same URL: revalidate (ETag) every time
        response.headers["cache-control"] = "private, no-cache"
    else:
        # HTML, fragments, the SW script: always revalidate — never serve a stale page
        response.headers["cache-control"] = "no-cache"
