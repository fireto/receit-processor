# TODO — receit-processor

Deferred and upcoming work. Delete an entry when it lands.

## State at 2026-10-14
- Rebuild from the February PoC under way (DESIGN.md, decided 2026-10-14): offers stack,
  SQLite + photo store, background reading with structured output, self-check and quality
  flags, lab-managed deployment.
- Deployed 2026-10-14 at https://rec.fireto.net from `/opt/lab/receit-processor`
  (lab-managed route: basic auth outside, LAN bypass; Gatus; restic); the old PoC deploy
  is gone. **Writes to the real Sheet HomeExpenditure** since 2026-10-14 (switched from a
  test copy via `GOOGLE_SHEETS_ID` in the host `.env`; J1 renamed to БУЛСТАТ). New rows get
  the household's Цена лв formula, because the reports sum that column only.
- First real-Sheet row checked by the user on 2026-10-14: row 1629, C formula 6,85 лв for
  €3,50. Duplicate detection caught a real re-scan (Haiku, by header).
- Live since 2026-10-14, schema v3:
  - duplicate detection
  - ЕИК check digit (a misread ЕИК → `check`) with the VIES company cache (`company`)
  - "Всички бележки" (/receipts) with status filters
  - Sheet column K "Обект" = venue name (backfilled on all 36 written rows)
  - Venue name kept as read; the registered company is shown under it
  - `CARD_METHODS` exists, but the host `.env` has none set: the user puts card digits in
    ПлатежноСредство names instead ("Ваучери за храна 6832").
  `data/backup/pre-migration-{2,3}.db` on the host can go once v3 has run for a while — ask
  the user first.
- 37 receipts scanned so far (all in the real Sheet except #1, which is detached).
- Sheet work on 2026-10-14, by hand through the API at the user's request:
  - New tab "Годишен Отчет €": a copy of "Годишен Отчет Лв" (the user renamed the original)
    summing Разходи!D instead of C.
  - 95 rows of Разходи (1162–1426) had only the € amount. They got the C formula
    `=D×1,95583`, so the лв report stopped undercounting 2026.
  - Both reports agree to the cent for 2026, checked cell by cell.
- PWA install on Android: promo.fireto.net's "Install" seemed to do nothing. Both sites
  have the same manifest, icons and service worker, so the likely causes are the icon
  landing in the app drawer only (Samsung "Add new apps to Home screen") or Chrome's
  engagement rules. Asked the user what ⋮ shows ("Install app" vs only "Add to home
  screen"). If it is not installable, debug with Chrome remote debugging.
- Push from the `rebuild` worktree (`../receit-processor-wt-int`):
  `git push git@github.com:fireto/receit-processor.git rebuild:main`. Local `main` holds
  the user's WIP and is behind; the user updates it with `git stash; git pull; git stash pop`.
- The user's uncommitted edit of `tests/test_receipt_parser.py` (pyzbar warning test) is
  theirs to finish or drop — leave it alone.

## Waiting for the user
- Misread ЕИКs already in the Sheet: #22 Lidl 131071687 → 131071587 (offered to fix it),
  and #37 МАКСИМУМ 66 ООД 131038077 (check the paper). Correct them on the receipt page,
  and the Sheet row follows.
- Correct the comparison drafts (`tests/sample_receipts/rNN.expected.json` in this
  worktree; 37 receipts, drafted from the app's own readings, so Haiku-biased) against the
  paper. Start with r10, r13, r20, r24, r37 (dates), r22, r37 (ЕИК), r35, r36, r39
  (self-check). Then rescore for free:
  `python -m backend.eval --samples tests/sample_receipts --rescore eval-out/20261014-153055`.
- An OpenAI API key (cap $2) for the comparison; $2 caps in the Anthropic, Google and xAI
  consoles. Anthropic credit was added 2026-10-14 and Haiku answers (4 s, $0.0007 a
  receipt).
- Optional: hide the "Цена лв" column (it now gets a formula; keep it).

## App rebuild (branch rebuild-app) — open points
- The camera island (several photos, "＋ още снимка") is only rendered in headless
  Firefox; try it on a phone (Android + iOS) before going live.
- Sheet rows are addressed by number: rows inserted/deleted by hand above a receipt's row
  make later edits land on the wrong row (only delete is guarded). Consider finding the
  row by a key (e.g. a receipt id in a hidden column) before updating.
- QR codes are decoded from the stored 2000 px WebP, not the original upload; if that
  loses QR reads, decode at upload time. Neither of the first two real receipts gave a
  QR read, so check this first: duplicates are caught for free only when the QR reads.
- "Последни" shows no photo thumbnails (the stored photos are ~300 KB each); add small
  thumbnails if it is hard to tell receipts apart.

- Receipt ids are reused: `receipt.id` is a plain INTEGER PRIMARY KEY, so deleting the
  newest receipts hands their id to the next upload (seen 2026-10-14: a new scan became #2
  again). Harmless now. Before the offers hand-off keys anything on the id, move to
  AUTOINCREMENT or a stable uid.
- A card not in ПлатежноСредство leaves Плащане empty (2026-10-14: card 0367). The user
  then typed "Bulbank 4416" into row 1629 by hand, and an edit in the app would overwrite
  it with "", because Плащане is an app column. Either keep a hand-set Плащане when the
  app's value is empty, or get the user to set it in the app.

- ЕИК → company (2026-10-14) uses VIES only. A company VIES does not know (not
  VAT-registered) keeps the name as read. Fallbacks with an API, all paid or scraped:
  apis.bg "Регистър API" (12-month subscription, search by ЕИК/name), TheCompanyBook API
  (API key), Apify "bulgarian-company-check" (~$2 / 1000 lookups). The Registry Agency
  portal has no public API. `companies.search()` (name → ЕИК among cached companies) has
  no UI yet; manual entry could use it.

## Model comparison — first run 2026-10-14 (stopped, partial)
- Ran on the server inside the app container (`/app/data/eval/`, samples + run
  20261014-153055). It pushed the container to 309/384 MB, so the next run goes in a
  separate container (`docker run --rm` with the image, `.env` and `data/eval` mounted). A
  local copy of the run is in `eval-out/` (git-ignored).
- Against the unchecked drafts:
  - Haiku: header 89 %, lines 91 %, self-check 83 %, 5.5 s, $0.0012 a receipt.
  - Sonnet: 83 % / 95 % / 89 %, 3.0 s, $0.015 a receipt.
  - Gemini 3.8 Flash: unusable (timeouts at 120 s, 503 "high demand").
  - Not run yet: gemini-3.8-flash@low and gemini-3.5-flash-lite (prices
    `READING_PRICES="gemini-3.8-flash=0.75/3.75,gemini-3.5-flash-lite=0.54/4.50"`).
- xAI has no credits, so Grok is out. OpenAI has no key yet.
- The default backup `gemini:gemini-3.8-flash` is therefore not reliable. Choose a backup
  after the next run.

## Next
- Model comparison → choose the ladder and the backup provider (cheapest reliable model
  first); record the result in DESIGN.md.
- Hand-off to offers: designed 2026-10-15 (DESIGN.md "Hand-off to offers"), being built
  (outbox, then backfill).
- Restore drill with real data (photos) once receipts exist — tracked in the lab's
  docs/TODO.md.

## Reading module (added 2026-10-14, branch `reading`)
- First real comparison run (`python -m backend.eval --dry-run`, then `--yes` with the
  user's approval): pick Gemini/OpenAI/Grok model ids, set their prices in
  `READING_PRICES`, then fix `READING_LADDER` / `READING_BACKUP`.
- Unverified against the live APIs (only against the SDKs' request serialisation): the
  shared schema (nullable type arrays, enums) on Gemini Interactions and xAI; `store=False`
  on xAI's Responses API; Haiku 5.5 effort default vs explicit `@low`.
- QR decoding is untested locally (no zbar shared library on the dev machine) — check it in
  the Docker image with a real receipt.
- Gemini backup (`gemini-3.8-flash`, default since 2026-10-14): took 70 s on one receipt
  and timed out at 120 s on the other, which is far over the 30 s target. Its price is not
  in `READING_PRICES`, so its calls count $0 toward the cap. Settle both in the comparison.
- Phones are all Android (S24 Ultra, Pixel 4a, Nokia; answered 2026-10-14), so no HEIC
  support is planned. The S24's camera can save HEIF ("High efficiency pictures", off by
  default). If such a photo is uploaded from the gallery, the app answers "Файлът не е
  снимка"; add `pillow-heif` only if that ever happens. HEIC samples for the comparison
  need converting.
