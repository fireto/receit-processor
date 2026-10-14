"""The model comparison harness (python -m backend.eval) — no real model calls."""

import csv
import io
import json
from dataclasses import asdict

import pytest
from PIL import Image

from backend.eval import cli
from backend.eval.inputs import discover, prepare_photo
from backend.eval.scoring import (
    ModelSummary,
    ReceiptScore,
    cheapest_reliable,
    score_receipt,
    summarise,
)
from backend.reading import Discount, Line, Reading
from backend.reading.check import check
from backend.reading.providers import ModelSpec, ProviderResult


def jpeg(size=(40, 20)):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="JPEG")
    return buf.getvalue()


def make_reading(**over):
    r = Reading(
        date="2026-10-12", time="10:00", total_cents=1062, eik="131129282",
        store_name="Kaufland", store_address=None, payment="card", card_last4="0889",
        category="Храна", notes="хляб и банани",
        lines=[
            Line("ХЛЯБ ДОБРУДЖА 500Г", 1, "pcs", 189, 189, 0, "Б"),
            Line("БАНАНИ", 0.456, "kg", 599, 273, 0, "Б"),
            Line("МЛЯКО 3%", 2, "pcs", 350, 700, 50, "Б"),
        ],
        receipt_discounts=[Discount("купон", 50)],
    )
    for k, v in over.items():
        setattr(r, k, v)
    return r


def expected_dict(**over):
    d = asdict(make_reading())
    d["eik"] = "BG131129282"
    d.update(over)
    return d


# ---------------------------------------------------------------- inputs

def test_discover_groups_multi_photo_receipts(tmp_path):
    for name in ["a.jpg", "b.2.jpg", "b.1.jpg", "c.PNG", "notes.txt", "b.expected.json"]:
        (tmp_path / name).write_bytes(b"x" if not name.endswith(".json") else b'{"total_cents": 1}')
    samples = discover(tmp_path)
    assert [s.name for s in samples] == ["a", "b", "c"]
    b = samples[1]
    assert [p.name for p in b.photos] == ["b.1.jpg", "b.2.jpg"]
    assert b.expected == {"total_cents": 1}
    assert samples[0].expected is None


def test_prepare_photo_matches_the_app_store(tmp_path):
    path = tmp_path / "big.jpg"
    path.write_bytes(jpeg((3000, 1000)))
    data = prepare_photo(path)
    img = Image.open(io.BytesIO(data))
    assert img.format == "WEBP"
    assert max(img.size) == 2000


# ---------------------------------------------------------------- scoring

def score(actual, expected=None, **kw):
    expected = expected_dict() if expected is None else expected
    sc = check(actual, None) if actual and not actual.quality else None
    return score_receipt("r1", "claude:m", expected, actual, sc, seconds=kw.get("seconds", 5.0),
                         input_tokens=100, output_tokens=10, cost_usd=0.01,
                         error=kw.get("error"))


def test_perfect_reading_scores_full_marks():
    s = score(make_reading())
    assert s.readable is True
    assert s.header == {"date": True, "total": True, "eik": True, "payment": True}
    assert (s.lines_ok, s.lines_denominator) == (3, 3)
    assert s.self_check_ok is True


def test_names_match_fuzzily_but_prices_and_qty_exactly():
    actual = make_reading(lines=[
        Line("ХЛЯБ ДОБРУДЖА 500 Г", 1, "pcs", 189, 189, 0, None),   # name variant: ok
        Line("БАНАНИ", 0.465, "kg", 599, 273, 0, "Б"),              # qty wrong
        Line("МЛЯКО 3%", 2, "pcs", 350, 700, 0, "Б"),               # discount missed
        Line("ТОРБИЧКА", 1, "pcs", 10, 10, 0, "Б"),                 # extra line
    ])
    s = score(actual)
    assert (s.lines_ok, s.lines_denominator) == (1, 4)


def test_header_field_mismatches():
    s = score(make_reading(total_cents=1063, payment="cash"))
    assert s.header == {"date": True, "total": False, "eik": True, "payment": False}


def test_bad_photo_flagging():
    bad_expected = {"quality": ["blurry"]}
    flagged = score(make_reading(quality=["blurry"]), bad_expected)
    assert flagged.readable is False and flagged.quality_flagged is True
    assert flagged.header is None
    missed = score(make_reading(), bad_expected)
    assert missed.quality_flagged is False


def test_error_scores_as_a_miss():
    s = score(None, error="boom")
    assert s.header == {"date": False, "total": False, "eik": False, "payment": False}
    assert s.lines_ok == 0 and s.lines_denominator == 3
    assert s.self_check_ok is False


def test_without_expected_only_self_check_and_timing_count():
    s = score(make_reading(), expected={})
    assert s.readable is None and s.header is None
    assert s.self_check_ok is True


def receipt_scores(model, *, header_ok=True, lines=(3, 3), check_ok=True, seconds=5.0,
                   cost=0.01, n=10, bad_flagged=True):
    out = []
    for i in range(n):
        out.append(ReceiptScore(
            receipt=f"r{i}", model=model, readable=True,
            header={"date": True, "total": header_ok, "eik": True, "payment": True},
            lines_ok=lines[0], lines_denominator=lines[1], self_check_ok=check_ok,
            quality_flagged=False, seconds=seconds, input_tokens=100, output_tokens=10,
            cost_usd=cost, error=None))
    out.append(ReceiptScore(
        receipt="bad", model=model, readable=False, header=None, lines_ok=0,
        lines_denominator=0, self_check_ok=False, quality_flagged=bad_flagged, seconds=seconds,
        input_tokens=100, output_tokens=10, cost_usd=cost, error=None))
    return out


def test_summary_marks_reliable_models():
    s = summarise("claude:m", receipt_scores("claude:m"))
    assert isinstance(s, ModelSummary)
    assert s.reliable is True and s.reasons == []
    assert s.header_accuracy == 1.0 and s.line_accuracy == 1.0
    assert s.self_check_rate == 1.0 and s.bad_flagged == (1, 1)
    assert s.cost_total == pytest.approx(0.11)
    assert s.median_seconds == 5.0


@pytest.mark.parametrize("kw,reason", [
    ({"header_ok": False}, "header"),
    ({"lines": (97, 100)}, "lines"),
    ({"check_ok": False}, "self-check"),
    ({"bad_flagged": False}, "bad photos"),
    ({"seconds": 31.0}, "seconds"),
])
def test_summary_unreliable_reasons(kw, reason):
    s = summarise("m", receipt_scores("m", **kw))
    assert s.reliable is False
    assert any(reason in r for r in s.reasons)


def test_reliability_unknown_without_expected_values():
    scores = [ReceiptScore(receipt="r", model="m", readable=None, header=None, lines_ok=0,
                           lines_denominator=0, self_check_ok=True, quality_flagged=False,
                           seconds=1.0, input_tokens=1, output_tokens=1, cost_usd=0.0,
                           error=None)]
    assert summarise("m", scores).reliable is None


def test_cheapest_reliable():
    a = summarise("a", receipt_scores("a", cost=0.02))
    b = summarise("b", receipt_scores("b", cost=0.01))
    c = summarise("c", receipt_scores("c", cost=0.001, header_ok=False))
    assert cheapest_reliable([a, b, c]).model == "b"
    assert cheapest_reliable([c]) is None


# ---------------------------------------------------------------- CLI

@pytest.fixture
def samples(tmp_path):
    d = tmp_path / "samples"
    d.mkdir()
    (d / "one.jpg").write_bytes(jpeg())
    (d / "two.1.jpg").write_bytes(jpeg())
    (d / "two.2.jpg").write_bytes(jpeg())
    (d / "one.expected.json").write_text(json.dumps(expected_dict()), encoding="utf-8")
    return d


class FakeProviders:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def factory(self, name, client=None):
        fake = self

        class P:
            def read(self, images, system, schema, *, model, effort):
                fake.calls.append((name, model, len(images)))
                return ProviderResult(data=fake.data, input_tokens=2000, output_tokens=500)

        return P()


@pytest.fixture
def no_calls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no provider call allowed")

    monkeypatch.setattr("backend.reading.ladder.get_provider", boom)
    monkeypatch.setattr("backend.eval.cli.decode_qr", lambda img: None)


def test_dry_run_prints_plan_and_upper_bound(samples, no_calls, capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = cli.main(["--samples", str(samples), "--models",
                   "claude:claude-haiku-5-5,openai:gpt-x", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 receipts, 3 photos" in out
    assert "claude:claude-haiku-5-5" in out
    est = cli.estimate_cost(ModelSpec.parse("claude:claude-haiku-5-5"), n_receipts=2, n_photos=3)
    assert est is not None and est[1] >= est[0] > 0
    assert f"${est[1]:.4f}" in out
    assert "OPENAI_API_KEY" in out and "price unknown" in out


def test_upper_bound_formula():
    typical, upper = cli.estimate_cost(ModelSpec.parse("claude:claude-sonnet-5-5"),
                                       n_receipts=10, n_photos=12)
    inp = 10 * cli.PROMPT_TOKENS + 12 * cli.IMAGE_TOKENS_MAX
    assert upper == pytest.approx((inp * 2.0 + 10 * cli.MAX_OUTPUT_TOKENS * 10.0) / 1e6)


def test_real_run_requires_yes(samples, no_calls, capsys):
    rc = cli.main(["--samples", str(samples), "--models", "claude:claude-haiku-5-5"])
    assert rc == 2
    assert "--yes" in capsys.readouterr().err


def test_run_writes_report_and_csv(samples, tmp_path, monkeypatch, capsys):
    fake = FakeProviders(expected_dict())
    monkeypatch.setattr("backend.reading.ladder.get_provider", fake.factory)
    monkeypatch.setattr("backend.eval.cli.decode_qr", lambda img: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out_root = tmp_path / "eval-out"
    rc = cli.main(["--samples", str(samples), "--models",
                   "claude:claude-haiku-5-5,openai:gpt-x", "--yes", "--out", str(out_root)])
    assert rc == 0
    assert sorted(c[2] for c in fake.calls) == [1, 2]  # one receipt has two photos
    assert all(c[0] == "claude" for c in fake.calls)    # openai skipped: no key
    run_dir = next(out_root.iterdir())
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "claude:claude-haiku-5-5" in report
    assert "OPENAI_API_KEY" in report
    rows = list(csv.DictReader((run_dir / "results.csv").open(encoding="utf-8")))
    assert {r["receipt"] for r in rows} == {"one", "two"}
    one = next(r for r in rows if r["receipt"] == "one")
    assert one["header_ok"] == "True" and one["lines_ok"] == "3"
    assert (run_dir / "raw" / "claude_claude-haiku-5-5" / "one.json").exists()


def test_draft_expected_writes_only_missing_files(samples, monkeypatch):
    fake = FakeProviders(expected_dict(total_cents=4242))
    monkeypatch.setattr("backend.reading.ladder.get_provider", fake.factory)
    monkeypatch.setattr("backend.eval.cli.decode_qr", lambda img: None)
    before = (samples / "one.expected.json").read_text(encoding="utf-8")
    rc = cli.main(["--samples", str(samples), "--draft-expected", "claude:claude-opus-5-5",
                   "--yes"])
    assert rc == 0
    assert (samples / "one.expected.json").read_text(encoding="utf-8") == before
    draft = json.loads((samples / "two.expected.json").read_text(encoding="utf-8"))
    assert draft["total_cents"] == 4242 and draft["eik"] == "131129282"
    assert len(fake.calls) == 1


def test_draft_expected_needs_yes_too(samples, no_calls):
    assert cli.main(["--samples", str(samples), "--draft-expected", "claude:x"]) == 2


def test_dry_run_draft_counts_only_receipts_without_expected(samples, no_calls, capsys,
                                                              monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["--samples", str(samples), "--draft-expected", "claude:claude-opus-5-5",
                     "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "claude:claude-opus-5-5: 1 calls" in out
    est = cli.estimate_cost(ModelSpec.parse("claude:claude-opus-5-5"), n_receipts=1, n_photos=2)
    # keys missing: the total still shows what the run would cost with every key set
    assert f"${est[1]:.4f}" in out.splitlines()[-1]
