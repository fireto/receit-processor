"""FastAPI application: lifespan (migrations, the reader thread), the cross-site guard,
health endpoints and the UI router."""

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend import companies, config, db, health, receipts, sheet_sync, ui, worker

load_dotenv()

log = logging.getLogger(__name__)


def make_worker(sheet) -> worker.Worker | None:
    mode = worker.mode()
    if mode == "0":
        return None
    if mode == "fake":  # local development: no model is called
        from backend import fakes
        log.warning("RECEIPTS_READER=fake: canned readings, no model calls")
        return worker.Worker(config.DB_PATH, config.PHOTOS_DIR, sheet, read=fakes.dev_read,
                             decode_qr=fakes.dev_decode_qr)
    return worker.Worker(config.DB_PATH, config.PHOTOS_DIR, sheet,
                         company_lookup=companies.vies_fetch)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn only configures its own loggers; without this the reader's logs are lost
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    conn = db.connect(config.DB_PATH)
    db.migrate(conn)
    receipts.reset_interrupted(conn)
    conn.close()
    app.state.sheet = sheet_sync.make_sheet()
    app.state.worker = make_worker(app.state.sheet)
    # ЕИК → company over the network only with the real reader (not in tests / dev)
    app.state.company_lookup = companies.vies_fetch if worker.mode() == "1" else None
    if app.state.worker:
        app.state.worker.start()
    yield
    if app.state.worker:
        app.state.worker.stop()


app = FastAPI(title="Бележки", lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url=None)
app.include_router(ui.router)
app.mount("/static", StaticFiles(directory=ui.STATIC_DIR), name="static")


@app.middleware("http")
async def guard_and_cache(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and ui.cross_site(request):
        return PlainTextResponse("cross-site request refused", status_code=403)
    response = await call_next(request)
    ui.cache_control(request, response)
    return response


@app.get("/livez")
def livez():
    """The process serves requests. Used by the container healthcheck."""
    return {"status": "ok"}


@app.get("/healthz")
def healthz(request: Request):
    """The reader runs, nothing waits over 10 min, data/ is writable. Probed by Gatus."""
    w = getattr(request.app.state, "worker", None)
    ok, details = health.check(config.DB_PATH, receipts.utcnow(),
                               reader_alive=bool(w and w.alive()),
                               photos_dir=config.PHOTOS_DIR)
    return JSONResponse({"status": "ok" if ok else "unhealthy", **details},
                        status_code=200 if ok else 503)
