"""Tests for monkey_collector.cost_tracker — Token cost tracking."""

import csv
import os
from contextlib import contextmanager

import pytest
from loguru import logger

from monkey_collector.domain.cost_tracker import MODEL_PRICING, CostTracker


@contextmanager
def captured_warnings():
    """Collect loguru WARNING messages emitted inside the block.

    tests/conftest.py disables the "monkey_collector" logger namespace for the
    whole session so other tests stay quiet; re-enable it for the duration of
    the block, since these tests are pinning the WARNING itself, not treating
    it as incidental noise.
    """
    messages: list[str] = []
    handler_id = logger.add(messages.append, level="WARNING", format="{message}")
    logger.enable("monkey_collector")
    try:
        yield messages
    finally:
        logger.disable("monkey_collector")
        logger.remove(handler_id)


class TestInitialize:
    def test_creates_csv(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        assert os.path.exists(tracker.csv_path)

    def test_writes_header(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        with open(tracker.csv_path) as f:
            header = next(csv.reader(f))
        assert header == CostTracker.CSV_COLUMNS

    def test_resets_state_on_reinitialize(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        tracker.record("gpt-5-nano", 100, 50, step=1)
        assert tracker.get_total_cost() > 0

        session2 = tmp_path / "session2"
        session2.mkdir()
        tracker.initialize(str(session2))
        assert tracker.get_total_cost() == 0.0


class TestRecord:
    def test_returns_entry_dict(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        entry = tracker.record("gpt-5-nano", 100, 50, step=1)
        assert isinstance(entry, dict)
        for col in CostTracker.CSV_COLUMNS:
            assert col in entry

    def test_appends_to_csv(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        tracker.record("gpt-5-nano", 100, 50, step=1)
        tracker.record("gpt-5-nano", 200, 100, step=2)
        with open(tracker.csv_path) as f:
            rows = list(csv.reader(f))
        assert len(rows) == 3  # header + 2 data rows

    def test_accumulates_total(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        tracker.record("gpt-5-nano", 1_000_000, 0, step=1)
        first_cost = tracker.get_total_cost()
        tracker.record("gpt-5-nano", 1_000_000, 0, step=2)
        assert tracker.get_total_cost() == pytest.approx(first_cost * 2)

    def test_default_agent(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        entry = tracker.record("gpt-5-nano", 100, 50, step=1)
        assert entry["agent"] == "text_generator"

    def test_custom_agent(self, tmp_path):
        tracker = CostTracker()
        tracker.initialize(str(tmp_path))
        entry = tracker.record("gpt-5-nano", 100, 50, step=1, agent="custom_agent")
        assert entry["agent"] == "custom_agent"


class TestCalcCost:
    def test_known_model(self):
        # gpt-5-nano: input=0.10/1M, output=0.40/1M
        cost = CostTracker._calc_cost("gpt-5-nano", 1_000_000, 1_000_000)
        expected = 0.10 + 0.40
        assert cost == pytest.approx(expected)

    def test_unknown_model(self):
        cost = CostTracker._calc_cost("unknown-model", 1000, 500)
        assert cost == 0.0

    def test_unknown_model_warns_that_zero_means_unknown_not_free(self):
        with captured_warnings() as messages:
            CostTracker._calc_cost("some-unpriced-model", 1000, 500)
        assert any(
            "some-unpriced-model" in m and "unknown" in m.lower() for m in messages
        ), messages

    def test_known_model_does_not_warn(self):
        with captured_warnings() as messages:
            CostTracker._calc_cost("gpt-5-nano", 1000, 500)
        assert messages == []

    def test_qwen_flash_pricing(self):
        # qwen/qwen3.8-flash: input=0.15/1M, output=0.47/1M (openrouter.ai/api/v1/models, 2026-08-29)
        cost = CostTracker._calc_cost("qwen/qwen3.8-flash", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.15 + 0.47)

    def test_qwen_plus_pricing(self):
        # qwen/qwen3.7-plus: input=0.32/1M, output=1.28/1M (openrouter.ai/api/v1/models, 2026-08-29)
        cost = CostTracker._calc_cost("qwen/qwen3.7-plus", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.32 + 1.28)

    def test_zero_tokens(self):
        cost = CostTracker._calc_cost("gpt-5-nano", 0, 0)
        assert cost == 0.0

    def test_input_only(self):
        cost = CostTracker._calc_cost("gpt-5-nano", 1_000_000, 0)
        assert cost == pytest.approx(0.10)

    def test_all_models_have_pricing(self):
        for model_name in MODEL_PRICING:
            pricing = MODEL_PRICING[model_name]
            assert "input" in pricing
            assert "output" in pricing


class TestEdgeCases:
    def test_record_without_initialize(self):
        tracker = CostTracker()
        entry = tracker.record("gpt-5-nano", 100, 50, step=1)
        assert entry == {}

    def test_get_total_cost_initial(self):
        tracker = CostTracker()
        assert tracker.get_total_cost() == 0.0
