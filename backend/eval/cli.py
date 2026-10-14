"""python -m backend.eval — compare vision models on the sample receipts.

Real runs spend money and need --yes; --dry-run prints the plan and a cost estimate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from backend.eval.inputs import Sample, discover, prepare_photo
from backend.eval.report import write_csv, write_report
from backend.eval.scoring import score_receipt, summarise
from backend.reading.ladder import run_attempt
from backend.reading.pricing import price_for
from backend.reading.providers import ModelSpec, key_env
from backend.reading.providers.base import MAX_OUTPUT_TOKENS
from backend.reading.qr import decode_qr

DEFAULT_SAMPLES = Path("tests/sample_receipts")
DEFAULT_OUT = Path("eval-out")
DEFAULT_CATEGORIES = ["Храна", "Аптека", "Козметика", "Бебе", "Гориво", "Разни"]
PROMPT_TOKENS = 2500  # prompt + schema + categories, generous
IMAGE_TOKENS_MAX = 3000  # one ≤ 2000 px photo, generous for every provider
TYPICAL_OUTPUT_TOKENS = 3000


def estimate_cost(spec: ModelSpec, *, n_receipts: int, n_photos: int
                  ) -> tuple[float, float] | None:
    """(typical, upper bound) USD for one pass over the samples; None if no price."""
    price = price_for(spec.model)
    if price is None:
        return None
    inp = n_receipts * PROMPT_TOKENS + n_photos * IMAGE_TOKENS_MAX
    typical = (inp * price[0] + n_receipts * TYPICAL_OUTPUT_TOKENS * price[1]) / 1e6
    upper = (inp * price[0] + n_receipts * MAX_OUTPUT_TOKENS * price[1]) / 1e6
    return typical, upper


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backend.eval", description=__doc__)
    p.add_argument("--models", help="comma-separated provider:model[@effort] to compare")
    p.add_argument("--draft-expected", metavar="MODEL",
                   help="write <name>.expected.json drafts with this model (never overwrites)")
    p.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--categories", help="comma-separated; default: <samples>/categories.txt")
    p.add_argument("--jobs", type=int, default=4, help="parallel calls (default 4)")
    p.add_argument("--dry-run", action="store_true", help="no API calls: plan + cost estimate")
    p.add_argument("--yes", action="store_true", help="really call the models (costs money)")
    return p


def _categories(args) -> list[str]:
    if args.categories:
        return [c.strip() for c in args.categories.split(",") if c.strip()]
    path = args.samples / "categories.txt"
    if path.exists():
        return [c.strip() for c in path.read_text(encoding="utf-8").splitlines() if c.strip()]
    return DEFAULT_CATEGORIES


def _missing_key(spec: ModelSpec) -> str | None:
    env = key_env(spec.provider)
    return None if os.environ.get(env) else env


def _dry_run(plan: list[tuple[ModelSpec, list[Sample]]], samples: list[Sample]) -> int:
    print(f"{len(samples)} receipts, {sum(len(s.photos) for s in samples)} photos "
          f"({sum(1 for s in samples if s.expected)} with expected values)")
    total = total_with_keys = 0.0
    for spec, todo in plan:
        missing = _missing_key(spec)
        est = estimate_cost(spec, n_receipts=len(todo),
                            n_photos=sum(len(s.photos) for s in todo))
        cost = (f"typical ${est[0]:.4f}, upper bound ${est[1]:.4f}" if est
                else "price unknown (set READING_PRICES) — counted as $0")
        skip = f"  [would be skipped: {missing} not set]" if missing else ""
        print(f"  {spec}: {len(todo)} calls, {cost}{skip}")
        if est:
            total += est[1]
            total_with_keys += 0 if missing else est[1]
    print(f"No API call made. Upper bound: ${total_with_keys:.4f} with the keys set now, "
          f"${total:.4f} with every key set")
    return 0


def _load(samples: list[Sample], notes: list[str]) -> list[tuple[Sample, list[bytes], int | None]]:
    loaded = []
    for sample in samples:
        try:
            photos = [prepare_photo(p) for p in sample.photos]
        except Exception as e:  # noqa: BLE001 — e.g. HEIC without pillow-heif
            notes.append(f"{sample.name}: skipped, photo unreadable ({type(e).__name__}: {e})")
            continue
        qr_total = None
        for photo in photos:
            qr = decode_qr(photo)
            if qr and qr.get("amount_cents") is not None:
                qr_total = qr["amount_cents"]
                break
        loaded.append((sample, photos, qr_total))
    return loaded


def _safe(spec: ModelSpec) -> str:
    return str(spec).replace(":", "_").replace("@", "_").replace("/", "_")


def _compare(specs: list[ModelSpec], samples: list[Sample], categories: list[str], args) -> int:
    notes: list[str] = []
    runnable = []
    for spec in specs:
        missing = _missing_key(spec)
        if missing:
            notes.append(f"{spec}: skipped, {missing} not set")
        else:
            runnable.append(spec)
    loaded = _load(samples, notes)
    run_dir = args.out / datetime.now().strftime("%Y%m%d-%H%M%S")
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)

    def one(task):
        spec, (sample, photos, qr_total) = task
        res = run_attempt(spec, photos, categories, qr_total)
        raw_dir = run_dir / "raw" / _safe(spec)
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{sample.name}.json").write_text(json.dumps({
            "attempt": asdict(res.attempt),
            "reading": asdict(res.reading) if res.reading else None,
            "self_check": asdict(res.self_check) if res.self_check else None,
            "qr_total_cents": qr_total,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        a = res.attempt
        return score_receipt(sample.name, str(spec), sample.expected, res.reading,
                             res.self_check, seconds=a.seconds, input_tokens=a.input_tokens,
                             output_tokens=a.output_tokens, cost_usd=a.cost_usd, error=a.error)

    tasks = [(spec, item) for spec in runnable for item in loaded]
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        scores = list(pool.map(one, tasks))
    summaries = [summarise(str(spec), [s for s in scores if s.model == str(spec)])
                 for spec in runnable]
    write_csv(run_dir / "results.csv", scores)
    write_report(run_dir / "report.md", title=run_dir.name, n_receipts=len(loaded),
                 n_photos=sum(len(p) for _, p, _ in loaded), summaries=summaries,
                 scores=scores, notes=notes)
    print(f"Report: {run_dir / 'report.md'}")
    for n in notes:
        print(f"note: {n}")
    return 0


def _draft(spec: ModelSpec, samples: list[Sample], categories: list[str]) -> int:
    missing = _missing_key(spec)
    if missing:
        print(f"{missing} not set", file=sys.stderr)
        return 1
    notes: list[str] = []
    todo = [s for s in samples if not (s.photos[0].parent / f"{s.name}.expected.json").exists()]
    for sample, photos, qr_total in _load(todo, notes):
        res = run_attempt(spec, photos, categories, qr_total)
        if res.reading is None:
            print(f"{sample.name}: no reading ({res.attempt.error})")
            continue
        draft = asdict(res.reading)
        draft["_drafted_by"] = str(spec)
        path = sample.photos[0].parent / f"{sample.name}.expected.json"
        path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        check = "self-check ok" if res.self_check and res.self_check.ok else "CHECK BY HAND"
        print(f"{sample.name}: draft written ({check}, ${res.attempt.cost_usd:.4f})")
    for n in notes:
        print(f"note: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.models and not args.draft_expected:
        print("give --models and/or --draft-expected", file=sys.stderr)
        return 2
    try:
        specs = [ModelSpec.parse(m) for m in (args.models or "").split(",") if m.strip()]
        draft_spec = ModelSpec.parse(args.draft_expected) if args.draft_expected else None
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if not args.samples.is_dir():
        print(f"no samples directory {args.samples}", file=sys.stderr)
        return 1
    samples = discover(args.samples)
    if not samples:
        print(f"no receipt photos in {args.samples}", file=sys.stderr)
        return 1
    categories = _categories(args)

    if args.dry_run:
        plan = []
        if draft_spec:
            plan.append((draft_spec, [s for s in samples if s.expected is None]))
            print(f"draft-expected with {draft_spec} for the receipts without expected values")
        plan += [(spec, samples) for spec in specs]
        return _dry_run(plan, samples)
    if not args.yes:
        print("A real run calls paid APIs: add --yes (or use --dry-run first).",
              file=sys.stderr)
        return 2
    if draft_spec:
        rc = _draft(draft_spec, samples, categories)
        if rc or not specs:
            return rc
        samples = discover(args.samples)
    return _compare(specs, samples, categories, args)
