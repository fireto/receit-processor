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
- First real receipts: #1 and #2 were written to the test copy only, and their row links
  were cleared so they never touch the real Sheet. No row has been written to the real Sheet
  yet: check the first one (row 1629, C filled, report total up).
- Duplicate detection live (schema v2). `data/backup/pre-migration-2.db` on the host can
  be deleted once v2 has run for a while — ask the user first.
- Push from the `rebuild` worktree (`../receit-processor-wt-int`):
  `git push git@github.com:fireto/receit-processor.git rebuild:main`. Local `main` holds
  the user's WIP and is behind; the user updates it with `git stash; git pull; git stash pop`.
- The user's uncommitted edit of `tests/test_receipt_parser.py` (pyzbar warning test) is
  theirs to finish or drop — leave it alone.

## Waiting for the user
- 15–20 receipt photos in `tests/sample_receipts/` (see DESIGN.md "Model comparison"), and
  the paper originals of a few to check the expected values.
- An OpenAI API key (cap $2) for the comparison; $2 caps in the Anthropic, Google and xAI
  consoles. Anthropic had no credit on 2026-10-14; Sonnet then worked, so credit was
  apparently added. Confirm from the log that Haiku answers too.
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

## Next
- Model comparison → choose the ladder and the backup provider (cheapest reliable model
  first); record the result in DESIGN.md.
- Hand-off to offers: send each read receipt (header, lines, discounts, photo link) to an
  offers endpoint with a queue and retries; re-send after an edit or re-read. offers ignores
  ЕИКs that are not its chains. Designed together with the offers side (review screen,
  matching, purchases, price corrections) in its own grilling round.
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
