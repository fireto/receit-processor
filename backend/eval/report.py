"""report.md and results.csv for one comparison run."""

from __future__ import annotations

import csv
from pathlib import Path

from backend.eval.scoring import ModelSummary, ReceiptScore, cheapest_reliable

CSV_FIELDS = ["receipt", "model", "readable", "header_ok", "date_ok", "total_ok", "eik_ok",
              "payment_ok", "lines_ok", "lines_denominator", "self_check_ok",
              "quality_flagged", "seconds", "input_tokens", "output_tokens", "cost_usd",
              "error"]


def write_csv(path: Path, scores: list[ReceiptScore]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for s in scores:
            h = s.header or {}
            w.writerow({
                "receipt": s.receipt, "model": s.model,
                "readable": "" if s.readable is None else s.readable,
                "header_ok": all(h.values()) if h else "",
                "date_ok": h.get("date", ""), "total_ok": h.get("total", ""),
                "eik_ok": h.get("eik", ""), "payment_ok": h.get("payment", ""),
                "lines_ok": s.lines_ok, "lines_denominator": s.lines_denominator,
                "self_check_ok": s.self_check_ok, "quality_flagged": s.quality_flagged,
                "seconds": f"{s.seconds:.2f}", "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens, "cost_usd": f"{s.cost_usd:.6f}",
                "error": s.error or "",
            })


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f} %"


def _reliable(s: ModelSummary) -> str:
    if s.reliable is None:
        return "unknown (no expected values)"
    return "**yes**" if s.reliable else "no: " + "; ".join(s.reasons)


def write_report(path: Path, *, title: str, n_receipts: int, n_photos: int,
                 summaries: list[ModelSummary], scores: list[ReceiptScore],
                 notes: list[str]) -> None:
    lines = [f"# Model comparison {title}", "",
             f"{n_receipts} receipts, {n_photos} photos.", ""]
    best = cheapest_reliable(summaries)
    lines += [f"**Cheapest reliable model: {best.model}** "
              f"(${best.cost_per_receipt:.4f} per receipt)" if best
              else "**No model met the reliability thresholds.**", ""]
    lines += [
        "| model | header exact | date / total / ЕИК / payment | lines | self-check | "
        "bad photos flagged | false flags | errors | median s | max s | tokens in / out | "
        "$ per receipt | $ total | reliable |",
        "|" + "---|" * 14,
    ]
    for s in summaries:
        fields = " / ".join(_pct(s.field_accuracy.get(f)) for f in
                            ("date", "total", "eik", "payment")) if s.field_accuracy else "n/a"
        lines.append(
            f"| {s.model} | {_pct(s.header_accuracy)} | {fields} | {_pct(s.line_accuracy)} | "
            f"{_pct(s.self_check_rate)} | {s.bad_flagged[0]}/{s.bad_flagged[1]} | "
            f"{s.false_flags} | {s.errors} | {s.median_seconds:.1f} | {s.max_seconds:.1f} | "
            f"{s.input_tokens} / {s.output_tokens} | {s.cost_per_receipt:.4f} | "
            f"{s.cost_total:.4f} | {_reliable(s)} |")
    lines += ["", "Thresholds (DESIGN.md): header 100 %, lines ≥ 98 %, self-check ≥ 90 %, "
              "every bad photo flagged, median < 30 s.", ""]
    misses = [s for s in scores if s.error or (s.readable is not False and not s.self_check_ok)
              or (s.header and not all(s.header.values()))]
    if misses:
        lines += ["## Receipts with problems", "", "| receipt | model | problem |", "|---|---|---|"]
        for s in misses:
            problem = s.error or ", ".join(
                [f"{k} wrong" for k, v in (s.header or {}).items() if not v]
                + ([] if s.self_check_ok else ["self-check failed"])
                + ([f"lines {s.lines_ok}/{s.lines_denominator}"] if s.header else []))
            lines.append(f"| {s.receipt} | {s.model} | {problem} |")
        lines.append("")
    if notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    path.write_text("\n".join(lines), encoding="utf-8")
