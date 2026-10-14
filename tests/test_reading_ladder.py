"""read_receipt: the model ladder, retake short-circuit, backup provider and spend cap."""

from datetime import datetime

import pytest

from backend import reading as reading_pkg
from backend.reading import ReadOutcome, read_receipt
from backend.reading.providers import ProviderError, ProviderResult

CATS = ["Храна", "Разни"]


def data(total=1000, lines=(1000,), quality=(), eik="BG123456789"):
    return {
        "date": "2026-10-12", "time": "10:00", "total_cents": total, "eik": eik,
        "store_name": "Магазин", "store_address": None, "payment": "cash", "card_last4": None,
        "category": "Храна", "notes": "хляб", "receipt_discounts": [], "quality": list(quality),
        "lines": [{"name": f"L{i}", "qty": 1, "unit": "pcs", "unit_price_cents": t,
                   "total_cents": t, "discount_cents": 0, "vat_group": "Б"}
                  for i, t in enumerate(lines)],
    }


GOOD = data()
BAD_SUM = data(lines=(900,))          # off by 1.00
WORSE = data(total=1000, lines=(500,))  # still one issue, larger gap
BLURRY = data(quality=["blurry"])


class Script:
    """Fake providers: (provider, model) → list of results/exceptions, consumed in order."""

    def __init__(self, plan):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.calls = []

    def factory(self, name, client=None):
        script = self

        class Fake:
            def read(self, images, system, schema, *, model, effort):
                script.calls.append(f"{name}:{model}")
                outcome = script.plan[(name, model)].pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return ProviderResult(data=outcome, input_tokens=1_000_000,
                                      output_tokens=100_000)

        return Fake()


@pytest.fixture
def ladder_env(monkeypatch):
    monkeypatch.setenv("READING_LADDER", "claude:claude-haiku-5-5,claude:claude-sonnet-5-5")
    monkeypatch.setenv("READING_BACKUP", "gemini:gemini-x")
    monkeypatch.setenv("READING_PRICES", "gemini-x=1/1")
    monkeypatch.delenv("MONTHLY_CAP_USD", raising=False)
    monkeypatch.setattr("backend.reading.ladder.qr_total_cents", lambda photos: None)


def run(monkeypatch, plan, **kw):
    script = Script(plan)
    monkeypatch.setattr("backend.reading.ladder.get_provider", script.factory)
    kw.setdefault("spent_this_month_usd", 0.0)
    outcome = read_receipt([b"photo"], categories=CATS, **kw)
    assert isinstance(outcome, ReadOutcome)
    return outcome, script.calls


H, S, G = ("claude", "claude-haiku-5-5"), ("claude", "claude-sonnet-5-5"), ("gemini", "gemini-x")


def test_first_rung_ok(monkeypatch, ladder_env):
    out, calls = run(monkeypatch, {H: [GOOD]})
    assert out.status == "ok"
    assert out.reading.total_cents == 1000 and out.reading.eik == "123456789"
    assert calls == ["claude:claude-haiku-5-5"]
    assert out.next_start_at == 1
    a = out.attempts[0]
    assert (a.provider, a.model, a.outcome) == ("claude", "claude-haiku-5-5", "ok")
    assert a.input_tokens == 1_000_000 and a.output_tokens == 100_000
    assert a.cost_usd == pytest.approx(0.10 + 0.05)
    assert a.raw == GOOD and a.error is None
    assert datetime.fromisoformat(a.started_at).utcoffset().total_seconds() == 0
    assert a.seconds >= 0


def test_failed_check_escalates(monkeypatch, ladder_env):
    out, calls = run(monkeypatch, {H: [BAD_SUM], S: [GOOD]})
    assert out.status == "ok"
    assert calls == ["claude:claude-haiku-5-5", "claude:claude-sonnet-5-5"]
    assert [a.outcome for a in out.attempts] == ["check_failed", "ok"]
    assert out.next_start_at is None


def test_quality_issue_returns_retake_without_escalating(monkeypatch, ladder_env):
    out, calls = run(monkeypatch, {H: [BLURRY]})
    assert out.status == "retake"
    assert out.reading.quality == ["blurry"]
    assert calls == ["claude:claude-haiku-5-5"]
    assert out.attempts[0].outcome == "quality"
    assert out.next_start_at == 1


def test_error_goes_to_backup(monkeypatch, ladder_env):
    out, calls = run(monkeypatch, {H: [ProviderError("overloaded")], G: [GOOD]})
    assert out.status == "ok"
    assert calls == ["claude:claude-haiku-5-5", "gemini:gemini-x"]
    assert [a.outcome for a in out.attempts] == ["error", "ok"]
    assert "overloaded" in out.attempts[0].error
    assert out.attempts[0].cost_usd == 0
    assert out.attempts[1].provider == "gemini"
    assert out.next_start_at == 1


def test_backup_error_continues_up_the_ladder(monkeypatch, ladder_env):
    out, calls = run(monkeypatch, {H: [RuntimeError("down")], G: [RuntimeError("down")],
                                   S: [GOOD]})
    assert out.status == "ok"
    assert calls == ["claude:claude-haiku-5-5", "gemini:gemini-x", "claude:claude-sonnet-5-5"]


def test_unusable_output_counts_as_an_error_and_keeps_raw(monkeypatch, ladder_env):
    broken = {"date": "2026-10-12"}
    out, _ = run(monkeypatch, {H: [broken], G: [GOOD]})
    assert out.attempts[0].outcome == "error"
    assert out.attempts[0].raw == broken
    assert out.attempts[0].cost_usd > 0  # it was paid for
    assert out.status == "ok"


def test_all_rungs_fail_check_gives_best_reading(monkeypatch, ladder_env):
    out, _ = run(monkeypatch, {H: [WORSE], S: [BAD_SUM]})
    assert out.status == "check"
    assert out.reading.lines[0].total_cents == 900  # the smaller gap
    assert out.next_start_at is None


def test_best_reading_prefers_fewest_issues(monkeypatch, ladder_env):
    # QR total 10.00: haiku has the sum and the QR wrong (2 issues), sonnet only the sum
    two_issues = data(total=1100, lines=(900,))
    out, _ = run(monkeypatch, {H: [two_issues], S: [BAD_SUM]}, qr_total_cents=1000)
    assert out.status == "check"
    assert out.reading.total_cents == 1000


def test_nothing_succeeded_is_failed(monkeypatch, ladder_env):
    out, _ = run(monkeypatch, {H: [RuntimeError("x")], S: [RuntimeError("y")],
                               G: [RuntimeError("g1"), RuntimeError("g2")]})
    assert out.status == "failed"
    assert out.reading is None
    assert len(out.attempts) == 4
    assert out.next_start_at is None


def test_no_backup_configured(monkeypatch, ladder_env):
    monkeypatch.setenv("READING_BACKUP", "")
    out, calls = run(monkeypatch, {H: [RuntimeError("x")], S: [GOOD]})
    assert calls == ["claude:claude-haiku-5-5", "claude:claude-sonnet-5-5"]
    assert out.status == "ok"


def test_start_at_skips_lower_rungs(monkeypatch, ladder_env):
    out, calls = run(monkeypatch, {S: [GOOD]}, start_at=1)
    assert calls == ["claude:claude-sonnet-5-5"]
    assert out.status == "ok" and out.next_start_at is None


def test_spend_cap_stops_escalation(monkeypatch, ladder_env):
    # haiku costs 0.15 here; 1.90 + 0.15 ≥ 2 → no sonnet call
    out, calls = run(monkeypatch, {H: [BAD_SUM], S: [GOOD]}, spent_this_month_usd=1.90)
    assert calls == ["claude:claude-haiku-5-5"]
    assert out.status == "check"
    assert out.reading is not None
    assert out.next_start_at == 1


def test_spend_cap_reached_before_any_call(monkeypatch, ladder_env):
    out, calls = run(monkeypatch, {}, spent_this_month_usd=2.0)
    assert calls == []
    assert out.status == "failed" and out.error == "spend cap"
    assert out.attempts == [] and out.next_start_at == 0


def test_spend_cap_from_env(monkeypatch, ladder_env):
    monkeypatch.setenv("MONTHLY_CAP_USD", "10")
    out, calls = run(monkeypatch, {H: [GOOD]}, spent_this_month_usd=5.0)
    assert out.status == "ok"


def test_spend_cap_also_guards_the_backup(monkeypatch, ladder_env):
    monkeypatch.setenv("READING_PRICES", "gemini-x=1/1,claude-haiku-5-5=1.9/1")
    out, calls = run(monkeypatch, {H: [ProviderError("x")], G: [GOOD]},
                     spent_this_month_usd=2.0 - 0.0001)
    # the failed call cost nothing, so the backup still runs
    assert calls == ["claude:claude-haiku-5-5", "gemini:gemini-x"]
    out, calls = run(monkeypatch, {H: [{"broken": True}], G: [GOOD]},
                     spent_this_month_usd=0.0)
    # the broken (paid: 1.9 + 0.1) output pushes the spend to the cap → no backup
    assert calls == ["claude:claude-haiku-5-5"]
    assert out.status == "failed" and out.error == "spend cap"


def test_qr_total_is_decoded_from_the_photos_when_not_given(monkeypatch, ladder_env):
    monkeypatch.setattr("backend.reading.ladder.qr_total_cents", lambda photos: 999)
    out, _ = run(monkeypatch, {H: [GOOD], S: [GOOD]})
    assert out.status == "check"  # QR 9.99 ≠ total 10.00 on both rungs


def test_given_qr_total_skips_decoding(monkeypatch, ladder_env):
    def boom(photos):
        raise AssertionError("must not decode")

    monkeypatch.setattr("backend.reading.ladder.qr_total_cents", boom)
    out, _ = run(monkeypatch, {H: [GOOD]}, qr_total_cents=1000)
    assert out.status == "ok"


def test_public_interface():
    for name in ["read_receipt", "decode_qr", "check", "ReadOutcome", "Reading", "Line",
                 "Discount", "Attempt", "SelfCheck"]:
        assert hasattr(reading_pkg, name), name
