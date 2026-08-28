"""Activity coverage — the numerator, the denominator, and their provenance."""

from __future__ import annotations

import csv

import pytest

from atlas_collector.coverage import CSV_COLUMNS, ActivityCoverage, normalize_activity

PKG = "com.test.app"
DECLARED = {f"{PKG}/.Main", f"{PKG}/.Detail", f"{PKG}/.Settings", f"{PKG}/.About"}


def cov(**kwargs) -> ActivityCoverage:
    kwargs.setdefault("declared", set(DECLARED))
    return ActivityCoverage(package=PKG, **kwargs)


# ---------------------------------------------------------------------------
# normalize_activity — the silent coverage-halving bug it prevents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("com.test.app/.Main", "com.test.app/com.test.app.Main"),
        ("com.test.app/com.test.app.Main", "com.test.app/com.test.app.Main"),
        ("com.test.app/Main", "com.test.app/com.test.app.Main"),
        ("com.test.app/com.other.Main", "com.test.app/com.other.Main"),
        ("", ""),
        ("no-slash", "no-slash"),
    ],
)
def test_shorthand_and_full_names_normalize_to_one_string(raw, expected):
    assert normalize_activity(raw) == expected


def test_dumpsys_and_dump_spellings_are_the_same_activity():
    """`dumpsys` says `pkg/.Main`, a UI dump says `pkg/pkg.Main`. Treating those
    as two Activities halves coverage without any error appearing."""
    tracker = cov()
    assert tracker.record(f"{PKG}/.Main", step=0)
    assert not tracker.record(f"{PKG}/{PKG}.Main", step=1), "already visited"
    assert tracker.unique_visited == 1


# ---------------------------------------------------------------------------
# The denominator
# ---------------------------------------------------------------------------


def test_coverage_is_visited_over_declared():
    tracker = cov()
    tracker.record(f"{PKG}/.Main", step=0)
    tracker.record(f"{PKG}/.Detail", step=1)
    assert tracker.unique_visited == 2
    assert tracker.total == 4
    assert tracker.coverage == 0.5


def test_an_unknown_activity_is_logged_but_never_counted():
    """A share sheet or permission dialog must not inflate coverage, and must
    not extend the denominator — either one makes two runs incomparable."""
    tracker = cov()
    assert not tracker.record("com.android.systemui/.ShareSheet", step=0)
    assert tracker.unique_visited == 0
    assert tracker.total == 4, "the denominator is fixed ground truth"


def test_coverage_can_never_exceed_one_with_fixed_ground_truth():
    tracker = cov()
    for i in range(20):
        tracker.record(f"other.app/.Screen{i}", step=i)
    for name in DECLARED:
        tracker.record(name, step=99)
    assert tracker.coverage == 1.0


def test_dynamic_total_extends_the_denominator_when_asked():
    tracker = cov(allow_dynamic_total=True)
    assert tracker.record(f"{PKG}/.Undeclared", step=0)
    assert tracker.total == 5
    assert tracker.unique_visited == 1


def test_dynamic_total_still_ignores_other_packages():
    tracker = cov(allow_dynamic_total=True)
    assert not tracker.record("com.android.systemui/.ShareSheet", step=0)
    assert tracker.total == 4


def test_an_empty_denominator_reports_zero_rather_than_dividing_by_zero():
    """A missing manifest must not abort a run; 0.0 with total 0 reads as
    'not measured' to anyone looking."""
    tracker = ActivityCoverage(package=PKG, declared=set())
    tracker.record(f"{PKG}/.Main", step=0)
    assert tracker.coverage == 0.0
    assert tracker.total == 0


def test_record_returns_true_only_when_coverage_advanced():
    """The explorer's progress signal — not merely 'is this our package'."""
    tracker = cov()
    assert tracker.record(f"{PKG}/.Main", step=0) is True
    assert tracker.record(f"{PKG}/.Main", step=1) is False


def test_unvisited_reports_what_is_left():
    tracker = cov()
    tracker.record(f"{PKG}/.Main", step=0)
    assert tracker.unvisited == {normalize_activity(a) for a in DECLARED} - {
        normalize_activity(f"{PKG}/.Main")
    }


# ---------------------------------------------------------------------------
# The CSV timeseries
# ---------------------------------------------------------------------------


def test_the_csv_is_a_timeseries_not_a_final_tally(tmp_path):
    tracker = cov()
    tracker.open(tmp_path / "activity_coverage.csv")
    tracker.record(f"{PKG}/.Main", step=0)
    tracker.record("com.android.systemui/.Dialog", step=1)
    tracker.record(f"{PKG}/.Detail", step=2)

    rows = list(csv.DictReader((tmp_path / "activity_coverage.csv").open()))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert len(rows) == 3, "one row per observation, so the curve is recoverable"
    assert [r["counted"] for r in rows] == ["true", "false", "true"]
    assert [r["unique_visited"] for r in rows] == ["1", "1", "2"]
    assert rows[-1]["coverage"].startswith("0.5")
    assert rows[1]["activity"].startswith("com.android.systemui"), (
        "an uncounted activity is still recorded, for traceability"
    )


def test_recording_without_opening_a_csv_is_harmless():
    tracker = cov()
    tracker.record(f"{PKG}/.Main", step=0)
    assert tracker.unique_visited == 1


def test_summary_names_the_denominator_source():
    tracker = cov(source="dumpsys")
    tracker.record(f"{PKG}/.Main", step=0)
    assert "dumpsys" in tracker.summary()
    assert "1/4" in tracker.summary()
