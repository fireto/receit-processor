# receit-processor — design

Household expense capture: photograph a receipt, a vision model reads it, the expense lands
in the household Google Sheet. Served at `https://rec.fireto.net`. Decisions below were
settled in a design interview on 2026-10-14 (the February 2026 version was a PoC that never
went live: too slow, unreliable reading, switching models did not help). Change them here
first, then in code.

## Scope

- Household tool (several phones). UI in Bulgarian only, mobile-first, installable PWA,
  light/dark theme. No offline mode: without signal, take the photo with the phone's camera
  and upload it later from the gallery (the receipt's date comes from the receipt).
- Access: LAN (`10.0.0.0/8`) without login; from outside via the lab's Traefik basic auth
  (the old shared PIN is gone). POSTs with a foreign `Origin` or `Sec-Fetch-Site:
  cross-site` are refused (basic-auth credentials would ride along).
- Every receipt (groceries, pharmacy, fuel, …) is read **with its line items**. The sister
  app **offers** (`../offers`, promo.fireto.net) will receive them for paid-price-per-item
  tracking — that hand-off is a later phase (see docs/TODO.md); this app has no grocery logic.
- Currency: EUR, stored as integer cents.

## Flow

1. **Capture.** Home page: "Снимай бележка" (camera) and "От галерията" (one or more
   photos). A long receipt can have up to 3 photos ("＋ още снимка" before sending; from the
   gallery a toggle "една дълга бележка" sends the picked photos as one receipt, otherwise
   each photo is its own receipt). The upload returns at once — snap the next one.
2. **Reading** runs in the background (one at a time, in-process worker thread). The receipt
   shows in "Последни" as *обработва се…* and the list refreshes itself (HTMX polling).
3. **Outcome** (receipt `status`):
   - `ok` — read, the self-check passed, the quality is fine → written to the Sheet.
   - `check` (*провери*) — read, but the self-check failed on every model of the ladder (or
     the monthly spend cap stopped escalation). Not written until you confirm or fix it.
   - `retake` (*снимай отново*) — the model flagged the photo (blurry, top/bottom cut off,
     glare, not a receipt). Re-taking replaces the photos of the same receipt (same id,
     same Sheet row if one exists) and queues it again.
   - `failed` — every provider errored; "Опитай пак" re-queues.
4. **Receipt page.** Photos (tap = full size), the receipt-level fields editable (date,
   total, category, payment method, notes), the line items read-only with the sum check,
   and actions: *Потвърди* (for `check`), *Прочети отново* (re-read on the next model up the
   ladder), *Снимай отново* (re-take), *Изтрий*. Line items are not edited here — they are
   reviewed in offers, where matching happens.
5. **Manual entry** (an expense without a receipt) stays, written straight to the Sheet.

## Reading (`backend/reading/`)

- **Structured output** from every provider (a JSON schema the API enforces, never a JSON
  blob fished out with a regex). One schema for all receipts:
  - header: `date`, `time`, `total_cents`, `eik` (ЕИК/ЗДДС without "BG"), `store_name`,
    `store_address`, `payment` (cash/card), `card_last4`, `category` (one of the Sheet's
    categories), `notes` (3–5 Bulgarian words);
  - `lines`: `name` as printed, `qty` (pieces or kg), `unit_price_cents`, `total_cents`,
    `vat_group`; a discount printed under an item ("Kaufland Card -0,50", "ОТСТЪПКА")
    belongs to that item (`discount_cents`), so its paid price is real;
  - `receipt_discounts` (coupons, Lidl Plus, rounding — not spread over items);
  - `quality`: `ok` or a list of issues (`blurry`, `cut_top`, `cut_bottom`, `glare`,
    `not_a_receipt`).
- **Self-check**: Σ(line totals − discounts) − receipt discounts = total (±1 cent); the QR
  code's total (decoded locally with pyzbar) must agree when present. Pass → `ok`.
- **Ladder**: a configured list of models tried in order (cheap first). A failed self-check
  escalates to the next; a provider *error* goes to the backup provider. Which models and
  which backup is decided by the comparison (below); until then: Claude Haiku 5.5 → Claude
  Sonnet 5.5, backup Gemini.
- **Spend cap**: each reading stores its tokens and estimated cost; when this month's
  estimate reaches `MONTHLY_CAP_USD` (default 2) no further escalation happens (→ `check`).
  The provider consoles carry the same hard cap ($2 per provider).
- QR decoding, card-digits → payment method and ЕИК → category from history are kept from
  the PoC.

## Model comparison (`python -m backend.eval`)

15–20 real receipts in `tests/sample_receipts/` (git-ignored: personal data) — Kaufland,
Billa, Lidl, Metro, Фантастико, some non-grocery, some long, some crumpled, a few deliberately
blurry/cropped. Each has `<name>.expected.json`, drafted by the best model
(`--draft-expected`) and corrected by hand against the paper. Candidates: Claude Haiku 5.5,
Sonnet 5.5, Opus 5.5, Gemini, OpenAI, Grok (current model ids chosen when it runs).

A model is **reliable** when: header fields (date, total, ЕИК, payment) exact on 100 % of the
readable receipts; ≥ 98 % of lines with the right price and quantity, names recognisable;
the self-check passes on ≥ 90 % of receipts; the bad photos are flagged; < 30 s per receipt.
The cheapest reliable model leads the ladder; the best reliable model of another provider is
the backup. The report (per model: accuracy, seconds, tokens, cost) goes to `eval-out/`
(git-ignored).

## Data model (SQLite `data/receipts.db`, WAL)

- `receipt`: id, created_at, status, source (`camera`/`gallery`/`manual`), header fields,
  `category`, `payment_method`, `notes`, `qr_json`, `self_check_json`, `quality_json`,
  `sheet_row` (NULL until written), `sheet_written_at`, `confirmed_at`.
- `receipt_photo`: receipt_id, seq, path, width, height.
- `receipt_line`: receipt_id, seq, name, qty, unit, unit_price_cents, total_cents,
  discount_cents, vat_group.
- `receipt_discount`: receipt_id, seq, name, cents.
- `reading`: every model call — receipt_id, provider, model, started_at, seconds,
  input/output tokens, cost estimate, outcome, raw structured output (kept so a receipt can
  be re-derived without paying again).
- Migrations: append-only, `PRAGMA user_version`, run at startup (as in offers).
- **Photos**: `data/photos/<receipt id>/<seq>.webp`, EXIF orientation applied, long side
  ≤ 2000 px, WebP q80 (~300 KB). Kept for good (re-reading with a better prompt/model).
  Served only through the app.

## Google Sheet

Columns unchanged: Дата, Категория, Цена лв, Цена €, GGBG лв, Плащане, Допълн. такса,
Payback, Пояснения, БУЛСТАТ. **Цена лв is left empty for new rows** (the column keeps its
history; the user hides it in the Sheet by hand). Categories and payment methods come from
the named ranges Категории / ПлатежноСредство. A row is written when a receipt becomes `ok`
or is confirmed; later edits update the row; deleting the receipt deletes its row.

## UI

Server-rendered Jinja + HTMX 2 + Tailwind v4 (standalone CLI, built in the Docker image),
the same stack, look (one emerald accent) and rules as offers: every mutation is a POST form
that works without JavaScript (303 back), HTMX swaps fragments where it helps. JS islands
only where the browser must do the work (camera / multi-photo capture). Static files are
versioned by content hash; the service worker caches only versioned static files, never
pages.

## Stack and operations

Python 3.13, FastAPI, Jinja2, SQLite, Pillow, pyzbar, gspread, the providers' official SDKs
(anthropic, google-genai, openai). One uvicorn worker (the reader thread lives in-process).

- Deployed like offers: repo cloned to `/opt/lab/receit-processor` on docker.sf.fireto.net
  (moved from `/opt/appdata/…` so restic covers it), `rebuild.sh`, host port **8000** (lab
  SPEC.md registry), route-only project `projects/receit-processor/` in the lab repo
  (`Host(rec.fireto.net)`, letsencrypt, basic auth outside / LAN bypass).
- `/healthz` (Gatus): 503 when the reader thread is dead, a receipt has been queued for
  > 10 min, or the database is not writable. `/livez` always 200 (container healthcheck).
- Backups: the lab's restic takes `data/` (photos as files; the database through the backup
  role's SQLite online copy — the live file is excluded).
- Secrets in `.env` (API keys, Sheet id) and `service_account.json`, never in git.
