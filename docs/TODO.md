# TODO — receit-processor

Deferred and upcoming work. Delete an entry when it lands.

## State at 2026-10-14
- Rebuild from the February PoC under way (DESIGN.md, decided 2026-10-14): offers stack,
  SQLite + photo store, background reading with structured output, self-check and quality
  flags, lab-managed deployment.
- The PoC still runs at https://rec.fireto.net from `/opt/appdata/receit-processor`
  (unmanaged route, PIN auth); it has never been used for real.
- The user's uncommitted edit of `tests/test_receipt_parser.py` (pyzbar warning test) is
  theirs to finish or drop — leave it alone.

## Waiting for the user
- 15–20 receipt photos in `tests/sample_receipts/` (see DESIGN.md "Model comparison"), and
  the paper originals of a few to check the expected values.
- An OpenAI API key (cap $2) for the comparison; $2 caps in the Anthropic, Google and xAI
  consoles.
- Hide the "Цена лв" column in the Sheet (keep its history).

## App rebuild (branch rebuild-app) — open points
- `tests/test_receipt_parser.py::TestSheetRow` still expects a BGN value in "Цена лв"; the
  design leaves it empty. Fix it with the reading work that owns that file.
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
- Move the deployment to `/opt/lab/receit-processor`; restore-test the receipts backup.
