# TODO — receit-processor

Deferred and upcoming work. Delete an entry when it lands.

## State at 2026-10-14
- Rebuild from the February PoC under way (DESIGN.md, decided 2026-10-14): offers stack,
  SQLite + photo store, background reading with structured output, self-check and quality
  flags, lab-managed deployment.
- Deployed 2026-10-14 at https://rec.fireto.net from `/opt/lab/receit-processor`
  (lab-managed route: basic auth outside, LAN bypass; Gatus; restic). Not yet used for
  real; the old PoC deploy is gone.
- The user's uncommitted edit of `tests/test_receipt_parser.py` (pyzbar warning test) is
  theirs to finish or drop — leave it alone.

## Waiting for the user
- 15–20 receipt photos in `tests/sample_receipts/` (see DESIGN.md "Model comparison"), and
  the paper originals of a few to check the expected values.
- An OpenAI API key (cap $2) for the comparison; $2 caps in the Anthropic, Google and xAI
  consoles.
- Hide the "Цена лв" column in the Sheet (keep its history).

## App rebuild (branch rebuild-app) — open points
- The camera island (several photos, "＋ още снимка") is only rendered in headless
  Firefox; try it on a phone (Android + iOS) before going live.
- Sheet rows are addressed by number: rows inserted/deleted by hand above a receipt's row
  make later edits land on the wrong row (only delete is guarded). Consider finding the
  row by a key (e.g. a receipt id in a hidden column) before updating.
- QR codes are decoded from the stored 2000 px WebP, not the original upload; if that
  loses QR reads, decode at upload time.
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
- HEIC samples need `pillow-heif` (not a dependency); convert them or add it if needed.
