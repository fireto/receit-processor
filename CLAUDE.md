# receit-processor — working rules

Scope, stack and decisions: [DESIGN.md](DESIGN.md). Deferred work: [docs/TODO.md](docs/TODO.md) —
read it at the start of every session.

## Rules

- **TDD always.** Write a failing test, run it and see it fail, write the minimal code to
  pass, then refactor. Bug fixes start with a test that reproduces the bug. Tests never call
  a real model or the real Sheet (fakes); the model comparison is the only thing that spends
  money, and it runs only when asked.
- **Verify end-to-end.** A change is done only when exercised for real: read a real receipt,
  see the Sheet row, restore the backup, curl the deployed service. "There is no backup until
  a restore has been tested."
- **Receipt photos are personal data**: `tests/sample_receipts/` and `eval-out/` stay
  git-ignored; never commit, upload or paste them anywhere else.
- **Track progress visibly.** On multi-step work, state a checklist up front and restate
  done / in progress / remaining as it evolves; surface scope growth explicitly. Anything
  deferred goes into `docs/TODO.md`; delete the entry when the work lands.
- **Commits:** conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `test:`,
  `refactor:`). No `Co-Authored-By` lines.

## Deployment

Runs on the lab (`../lab.sf.fireto.net`): host port from its SPEC.md port registry
(receit-processor = `8000`), keep its `projects/receit-processor/` route-only project in sync.
Same pattern as `../offers`.
