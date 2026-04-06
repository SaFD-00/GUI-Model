"""Tests for server.activity_coverage — Activity coverage tracking."""

import csv
import os

import pytest

from server.activity_coverage import ActivityCoverageTracker


class TestInitialize:
    def test_creates_csv(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        assert os.path.exists(tracker.csv_path)

    def test_writes_header(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        with open(tracker.csv_path) as f:
            header = next(csv.reader(f))
        assert header == ActivityCoverageTracker.CSV_COLUMNS

    def test_resets_state_on_reinitialize(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        tracker.record("A", step=1)
        assert tracker.get_visited_count() == 1

        # Re-initialize for new session
        session2 = tmp_path / "session2"
        session2.mkdir()
        tracker.initialize(str(session2), ["X", "Y", "Z"])
        assert tracker.get_visited_count() == 0
        assert len(tracker.total_activities) == 3


class TestRecord:
    def test_adds_activity(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        tracker.record("A", step=1)
        assert "A" in tracker.visited_activities

    def test_returns_entry_dict(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        entry = tracker.record("A", step=1)
        assert isinstance(entry, dict)
        for col in ActivityCoverageTracker.CSV_COLUMNS:
            assert col in entry

    def test_appends_to_csv(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B", "C"])
        tracker.record("A", step=1)
        tracker.record("B", step=2)
        with open(tracker.csv_path) as f:
            rows = list(csv.reader(f))
        assert len(rows) == 3  # header + 2 data rows


class TestCoverage:
    def test_coverage_computation(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B", "C", "D"])
        tracker.record("A", step=1)
        tracker.record("B", step=2)
        assert tracker.get_coverage() == pytest.approx(0.5)

    def test_initial_coverage_zero(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["X", "Y"])
        assert tracker.get_coverage() == 0.0

    def test_full_coverage(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        tracker.record("A", step=1)
        tracker.record("B", step=2)
        assert tracker.get_coverage() == pytest.approx(1.0)

    def test_get_visited_count(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        assert tracker.get_visited_count() == 0
        tracker.record("A", step=1)
        assert tracker.get_visited_count() == 1


class TestEdgeCases:
    def test_duplicate_not_double_counted(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A", "B"])
        tracker.record("A", step=1)
        tracker.record("A", step=2)
        assert tracker.get_visited_count() == 1
        assert tracker.get_coverage() == pytest.approx(0.5)

    def test_record_without_initialize(self):
        tracker = ActivityCoverageTracker()
        entry = tracker.record("A", step=1)
        assert "A" in tracker.visited_activities
        assert entry["activity"] == "A"
        assert not os.path.exists(tracker.csv_path)

    def test_empty_activity_name(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), ["A"])
        tracker.record("", step=1)
        assert tracker.get_visited_count() == 0

    def test_empty_total_activities(self, tmp_path):
        tracker = ActivityCoverageTracker()
        tracker.initialize(str(tmp_path), [])
        entry = tracker.record("A", step=1)
        # coverage = 1/1 (max(0,1)) to avoid division by zero
        assert entry["total_activities"] == 0
        assert entry["coverage"] == pytest.approx(1.0)
