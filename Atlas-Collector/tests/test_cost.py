"""LLM cost accounting — keeping "the LLM is only used for input text" falsifiable."""

from __future__ import annotations

import csv

from atlas_collector.cost import CSV_COLUMNS, MODEL_PRICING, CostTracker, price_of

MODEL = "qwen/qwen3.8-flash"


def test_price_matches_the_recorded_rate_card():
    """1M in + 1M out at the default model's published rate."""
    rate = MODEL_PRICING[MODEL]
    assert price_of(MODEL, 1_000_000, 1_000_000) == rate["input"] + rate["output"]
    assert price_of(MODEL, 0, 0) == 0.0


def test_an_unknown_model_costs_zero_instead_of_raising():
    """A stale price entry must never abort a collection run — but it does mean
    cost_usd can read 0.00 while the provider is billing, so the tokens are the
    primary evidence and the dollars are a convenience."""
    assert price_of("vendor/model-that-does-not-exist", 10_000, 10_000) == 0.0


def test_tokens_are_still_counted_for_an_unpriced_model():
    tracker = CostTracker()
    tracker.record(model="vendor/unknown", input_tokens=100, output_tokens=20)
    assert tracker.tokens == (100, 20)
    assert tracker.total_usd == 0.0
    assert tracker.calls == 1


def test_the_running_total_accumulates_across_calls():
    tracker = CostTracker()
    first = tracker.record(model=MODEL, input_tokens=1000, output_tokens=100)
    second = tracker.record(model=MODEL, input_tokens=2000, output_tokens=200)
    assert tracker.total_usd == first + second
    assert tracker.calls == 2
    assert tracker.tokens == (3000, 300)


def test_calls_are_attributed_to_the_step_set_on_the_tracker(tmp_path):
    """The step is held on the tracker so a new LLM consumer cannot forget to
    pass it and quietly land its rows on step -1."""
    tracker = CostTracker()
    tracker.open(tmp_path / "cost.csv")
    tracker.set_step(7)
    tracker.record(model=MODEL, input_tokens=10, output_tokens=1)
    tracker.set_step(8)
    tracker.record(model=MODEL, input_tokens=10, output_tokens=1, agent="other")

    rows = list(csv.DictReader((tmp_path / "cost.csv").open()))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert [r["step"] for r in rows] == ["7", "8"]
    assert [r["agent"] for r in rows] == ["input_text", "other"]


def test_an_explicit_step_overrides_the_tracker_step(tmp_path):
    tracker = CostTracker()
    tracker.open(tmp_path / "cost.csv")
    tracker.set_step(3)
    tracker.record(model=MODEL, input_tokens=1, output_tokens=1, step=99)
    assert list(csv.DictReader((tmp_path / "cost.csv").open()))[0]["step"] == "99"


def test_every_row_carries_the_running_total(tmp_path):
    """A sum over rows is the one thing a reader cannot cheaply do mid-run."""
    tracker = CostTracker()
    tracker.open(tmp_path / "cost.csv")
    for _ in range(3):
        tracker.record(model=MODEL, input_tokens=1_000_000, output_tokens=0)
    totals = [float(r["total_usd"]) for r in csv.DictReader((tmp_path / "cost.csv").open())]
    assert totals == sorted(totals)
    assert totals[-1] == round(tracker.total_usd, 6)


def test_open_resets_state_so_a_tracker_can_be_reused_across_apps(tmp_path):
    tracker = CostTracker()
    tracker.open(tmp_path / "a.csv")
    tracker.record(model=MODEL, input_tokens=1_000_000, output_tokens=1_000_000)
    assert tracker.total_usd > 0

    tracker.open(tmp_path / "b.csv")
    assert tracker.total_usd == 0.0
    assert tracker.calls == 0
    assert tracker.tokens == (0, 0)


def test_recording_without_opening_a_csv_is_harmless():
    tracker = CostTracker()
    tracker.record(model=MODEL, input_tokens=5, output_tokens=5)
    assert tracker.calls == 1


def test_summary_reports_calls_and_both_token_directions():
    tracker = CostTracker()
    tracker.record(model=MODEL, input_tokens=2500, output_tokens=12)
    summary = tracker.summary()
    assert "1 calls" in summary
    assert "2,500 in" in summary and "12 out" in summary


def test_the_expected_session_bill_stays_small():
    """Pins the project's cost claim: input-text generation only.

    ~75 text fields per app x 48 apps x ~2.5k prompt tokens.
    """
    tracker = CostTracker()
    for _ in range(75 * 48):
        tracker.record(model=MODEL, input_tokens=2500, output_tokens=12)
    assert tracker.total_usd < 2.0, f"expected under $2, got ${tracker.total_usd:.2f}"
