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
  tracking (see "Hand-off to offers"); this app has no grocery logic.
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
   - `duplicate` (*дубликат*) — the same receipt scanned again: the same fiscal QR code
     (fiscal device + receipt number; checked before reading, so it costs nothing) or the
     same ЕИК, date, minute and total as an older receipt. Not written to the Sheet. The
     page links the original and offers *Изтрий* or *Не е дубликат* (back to the status it
     would have had, or read if it was stopped by its QR; never flagged again).
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
  the PoC. Cards whose digits are not in their method's name (food vouchers) are mapped
  by `CARD_METHODS` in the host `.env`.
- **ЕИК**: a wrong check digit (9-digit ЕИК, 13-digit branch code) means a misread, so an
  `ok` receipt becomes `check` with the ЕИК highlighted (it is editable on the receipt
  page). A valid ЕИК is looked up once in **VIES** (EU VAT check, free, no key), and the
  answer is cached in the `company` table. The store name stays as read on the receipt,
  because it names the venue (one store of a chain); the registered company is its reference
  through the ЕИК and shows under it on the receipt page. VIES only knows VAT-registered companies: "not found" is cached and asked again
  after 30 days, and a failed request is not cached. Търговски регистър has no public API
  (paid ones: docs/TODO.md).
- **Settled in code** (2026-10-14): amounts are integer cents in the schema itself;
  `payment` is `cash`/`card`/`unknown` (→ None); `category` is an enum of the passed
  categories (fallback Разни); line discounts are stored positive, receipt discounts are
  signed (positive reduces the total). Prompt: one stable English block (cacheable), the
  category list in a second block. Config: `READING_LADDER` / `READING_BACKUP` as
  `provider:model[@effort]` (effort = Claude `output_config.effort`, Gemini
  `thinking_level`, OpenAI/xAI `reasoning.effort`; omitted = model default),
  `READING_PRICES` `model=in/out` USD per 1M (Claude 5.5 prices built in; unknown → $0 + a
  warning). APIs: Claude Messages `output_config.format`; Gemini Interactions API
  `response_format` (generateContent is legacy); OpenAI and Grok Responses API strict
  `json_schema` (Grok via the `openai` SDK at `api.x.ai/v1`, photos re-encoded to JPEG —
  xAI lists no WebP). A paid but unusable output (refusal, truncation, bad shape) counts as
  an error → backup. When every rung fails the self-check, the reading with the fewest
  issues (then the smallest gap, then the later rung) is kept.

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

Harness details (settled 2026-10-14): `name.jpg` is one receipt, `name.1.jpg`,
`name.2.jpg` one receipt in parts; `name.expected.json` holds a Reading (a bad photo just
`{"quality": [...]}`). Photos are normalised like the app stores them (≤ 2000 px WebP).
Lines match greedily in order by name similarity (difflib ≥ 0.6) and need qty, unit price,
total and discount exact; the line denominator is max(expected, read) so invented lines
count. "< 30 s" is judged on the median. `--dry-run` prints the plan and an upper bound
(output at `max_tokens`); real runs need `--yes`; a missing API key skips that model.

## Data model (SQLite `data/receipts.db`, WAL)

- `receipt`: id, created_at, status, source (`camera`/`gallery`/`manual`), header fields,
  `category`, `payment_method`, `notes`, `qr_json`, `self_check_json`, `quality_json`,
  `sheet_row` (NULL until written), `sheet_written_at`, `confirmed_at`, `duplicate_of`,
  `duplicate_held_status`, `duplicate_dismissed`.
- `receipt_photo`: receipt_id, seq, path, width, height.
- `receipt_line`: receipt_id, seq, name, qty, unit, unit_price_cents, total_cents,
  discount_cents, vat_group.
- `receipt_discount`: receipt_id, seq, name, cents.
- `company`: eik, name, address, source (`vies`), found, checked_at — the ЕИК cache.
- `reading`: every model call — receipt_id, provider, model, started_at, seconds,
  input/output tokens, cost estimate, outcome, raw structured output (kept so a receipt can
  be re-derived without paying again).
- Queue columns on `receipt`: `queued_at` (the >10 min health check), `start_at` (ladder
  index the next reading starts at), `next_start_at` (what *Прочети отново* uses; NULL = no
  better model). A manual entry is a `receipt` with source `manual`, no photos, status `ok`.
- `reading.receipt_id` is set NULL when a receipt is deleted: the rows are the month's spend.
- Migrations: append-only, `PRAGMA user_version`, run at startup (as in offers).
- **Photos**: `data/photos/<receipt id>/<seq>.webp`, EXIF orientation applied, long side
  ≤ 2000 px, WebP q80 (~300 KB). Kept for good (re-reading with a better prompt/model).
  Served only through the app.

## Google Sheet

Columns unchanged: Дата, Категория, Цена лв, Цена €, GGBG лв, Плащане, Допълн. такса,
Payback, Пояснения, БУЛСТАТ, Обект (K: the venue as printed, added 2026-10-14). **Цена лв gets the household's own conversion formula**
`=IF(NOT(ISFORMULA(D<row>));D<row>*1,95583;"")` (bg_BG syntax), written right after the
append: the reports (Годишен Отчет, Отчет2023) sum Цена лв only, so a row without it would
be missing from them. The user may hide the column. Categories and payment methods come from
the named ranges Категории / ПлатежноСредство. A row is written when a receipt becomes `ok`
or is confirmed; later edits update the row; deleting the receipt deletes its row.
The app writes only Дата, Категория, Цена €, Плащане, Пояснения, БУЛСТАТ, Обект; the hand-filled
columns are never touched. An `ok` receipt with `sheet_written_at` NULL has a pending
write, retried by the reader thread every minute. Deleting a row shifts the stored
`sheet_row`s below it; a row whose Дата/Цена € no longer match the receipt is not deleted
(the user is told to check it). All Sheet writes are serialised in-process.

## Hand-off to offers (decided 2026-10-15)

Every receipt that becomes `ok` or is confirmed is pushed to offers, which keeps the ones
from its five chains (by ЕИК) and does all grocery logic: matching lines to products,
purchases, paid prices, reports. The contract lives in offers' DESIGN.md ("Receipts").

- **Outbox** (schema v4, `offers_outbox`): one pending operation per receipt — `put` or
  `delete` — with `attempts`, `next_try_at` and `last_error`. Becoming `ok`, confirming,
  editing a receipt that was sent, or a re-read that ends `ok` queues a `put` (the latest
  state is sent, not a history); deleting one that was sent queues a `delete`. A new
  operation replaces a pending one for the same receipt. `sent_to_offers_at` on the receipt
  records the last successful `put`.
- **Sending**: the worker thread drains the outbox between readings. `PUT
  $OFFERS_URL/api/receipts/{id}` with the whole receipt (header, lines, discounts,
  `photo_url` = `https://rec.fireto.net/receipts/{id}`) and `Authorization: Bearer
  $OFFERS_TOKEN`; `DELETE` for deletions. 2xx → done (offers answers `stored: false` for
  other shops — also done). Network error or 5xx → retry with backoff (1, 5, 15, 60 min,
  then hourly); 4xx → kept with its error, not retried until the receipt changes.
  `OFFERS_URL` unset → the outbox fills but nothing is sent (local dev, tests use a fake).
  On the lab: `http://10.20.10.11:8090` (the host port, LAN only, not through Traefik).
- `/healthz` is 503 when an operation has been pending for over 6 h.
- `python -m backend.offers_outbox enqueue-all` queues a `put` for every `ok` or confirmed
  receipt (the one-off backfill).

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
- Local runs without models or the Sheet: `RECEIPTS_READER=fake` (canned reading; `0` = no
  reader thread, as in the tests), `RECEIPTS_SHEET=fake` (in-memory), `RECEIPTS_DATA`.
- `/healthz` (Gatus): 503 when the reader thread is dead, a receipt has been queued for
  > 10 min, or the database is not writable. `/livez` always 200 (container healthcheck).
- Backups: the lab's restic takes `data/` (photos as files; the database through the backup
  role's SQLite online copy — the live file is excluded).
- Secrets in `.env` (API keys, Sheet id) and `service_account.json`, never in git.
