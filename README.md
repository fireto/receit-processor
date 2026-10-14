# receit-processor

Household expense capture at https://rec.fireto.net: photograph a receipt, a vision model
reads it (header and line items) in the background, the expense lands in the household
Google Sheet. Scope, decisions and the data model: [DESIGN.md](DESIGN.md); deferred work:
[docs/TODO.md](docs/TODO.md).

## Layout

- `backend/main.py` — FastAPI app: lifespan (migrations, the reader thread), cross-site
  POST guard, `/livez`, `/healthz`.
- `backend/ui.py` + `backend/templates/` — server-rendered pages (Jinja + HTMX 2), in
  Bulgarian; `backend/static/app.js` is the camera-capture island; `backend/assets/app.css`
  is the Tailwind v4 input (output built, not committed).
- `backend/worker.py` — the in-process reader thread; `backend/reading/` reads a receipt.
- `backend/receipts.py` — the receipt store, the Sheet row (write, update, delete).
- `backend/db.py` — SQLite schema, append-only migrations (`PRAGMA user_version`).
- `backend/photos.py` — the photo store (`data/photos/<id>/<seq>.webp`).
- `backend/sheets.py`, `backend/sheet_sync.py` — gspread calls and the injectable Sheet.

## Run locally

```bash
pip install -r requirements-dev.txt
scripts/build-css.sh                       # downloads the Tailwind CLI once into .cache/
RECEIPTS_READER=fake RECEIPTS_SHEET=fake RECEIPTS_DATA=/tmp/receipts \
    uvicorn backend.main:app --port 8000   # canned readings, in-memory Sheet
python -m pytest -q
```

`RECEIPTS_READER=0` runs without the reader thread. With neither switch the app uses the
real models (`.env`, see `.env.example`) and the real Sheet (`service_account.json`).

## Deploy

On docker.sf.fireto.net in `/opt/lab/receit-processor`: `./rebuild.sh` (git pull, build,
`docker compose up -d`). Host port 8000; `data/` (database + photos) is a bind mount owned
by uid 1000 and backed up by the lab's restic.

## Google Sheet columns

Дата, Категория, Цена лв (left empty for new rows — history only), Цена €, GGBG лв,
Плащане, Допълн. такса, Payback, Пояснения, БУЛСТАТ (the store's ЕИК). The app writes and
updates Дата, Категория, Цена €, Плащане, Пояснения and БУЛСТАТ; the other columns are
filled by hand and never touched.
