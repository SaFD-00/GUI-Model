"""Verdict storage: folding, identity, staleness, and the multi-reviewer merge.

The invariant worth the most attention here is the one that fails SILENTLY:
a verdict is keyed by ``(package, step)`` but carries a content hash, and after
``reset --raw`` plus a re-collection the step number means a different screen.
Nothing downstream can tell the difference, so ``identity_key`` is the only
thing standing between a stale review and an arbitrary record being dropped.
"""

from __future__ import annotations

import json

import pytest

from monkey_collector.review.store import (
    REASONS,
    DecisionStore,
    identity_key,
    sanitize_reviewer,
)


@pytest.fixture()
def store(tmp_path):
    return DecisionStore.load(tmp_path / "review")


def row(step: int, verdict: str = "exclude", **extra):
    base = {"package": "org.tasks", "step": step, "verdict": verdict}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# folding
# ---------------------------------------------------------------------------


def test_a_verdict_round_trips_through_the_log(store, tmp_path):
    store.record([row(3, reason="no_effect", note="dup")], reviewer="bsw")

    reloaded = DecisionStore.load(tmp_path / "review")
    verdict = reloaded.get("org.tasks", 3)
    assert verdict is not None
    assert verdict.verdict == "exclude"
    assert verdict.reason == "no_effect"
    assert verdict.note == "dup"
    assert verdict.reviewer == "bsw"


def test_the_last_verdict_for_a_step_wins(store):
    store.record([row(7, "exclude", reason="broken")], reviewer="bsw")
    store.record([row(7, "keep")], reviewer="bsw")

    assert store.get("org.tasks", 7).verdict == "keep"


def test_clear_removes_the_decision_but_keeps_the_history(store, tmp_path):
    store.record([row(9)], reviewer="bsw")
    store.record([row(9, "clear")], reviewer="bsw")

    assert store.get("org.tasks", 9) is None
    # Re-read from disk, not just the in-memory pop: a `clear` line that the
    # FOLD does not honour makes undo look like it worked and then resurrect
    # the verdict on the next load -- and only the fold reaches the export.
    reloaded = DecisionStore.load(tmp_path / "review")
    assert reloaded.get("org.tasks", 9) is None
    assert reloaded.malformed == 0
    # The log still records that a decision was made and then withdrawn: the
    # append-only file is the audit trail, the fold is the current answer.
    lines = (tmp_path / "review" / "by-bsw.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[-1])["verdict"] == "clear"


def test_two_reviewers_merge_without_touching_each_others_files(tmp_path):
    directory = tmp_path / "review"
    first = DecisionStore.load(directory)
    first.record([row(1)], reviewer="bsw")
    second = DecisionStore.load(directory)
    second.record([{"package": "net.osmand", "step": 2, "verdict": "keep"}], reviewer="kim")

    merged = DecisionStore.load(directory)
    assert merged.get("org.tasks", 1).verdict == "exclude"
    assert merged.get("net.osmand", 2).verdict == "keep"
    assert {p.name for p in directory.glob("by-*.jsonl")} == {"by-bsw.jsonl", "by-kim.jsonl"}


def test_a_half_written_last_line_costs_one_verdict_not_the_file(tmp_path):
    directory = tmp_path / "review"
    store = DecisionStore.load(directory)
    store.record([row(1), row(2)], reviewer="bsw")
    with (directory / "by-bsw.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"package": "org.tasks", "step": 3, "verd')

    reloaded = DecisionStore.load(directory)
    assert reloaded.get("org.tasks", 1) is not None
    assert reloaded.get("org.tasks", 3) is None
    assert reloaded.malformed == 1


def test_an_unknown_reason_is_recorded_as_other_rather_than_stored_raw(store):
    store.record([row(4, reason="not-a-real-code")], reviewer="bsw")

    assert store.get("org.tasks", 4).reason == "other"
    assert set(REASONS) >= {"no_effect", "broken", "pii"}


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_identity_key_changes_when_any_of_the_three_inputs_changes():
    action = {"action_type": "tap", "x": 1, "y": 2}
    base = identity_key("<a/>", "<b/>", action)

    assert identity_key("<a2/>", "<b/>", action) != base
    assert identity_key("<a/>", "<b2/>", action) != base
    assert identity_key("<a/>", "<b/>", {**action, "x": 9}) != base


def test_identity_key_ignores_the_order_of_the_action_fields():
    first = {"action_type": "tap", "x": 1, "y": 2}
    second = {"y": 2, "x": 1, "action_type": "tap"}

    assert identity_key("<a/>", "<b/>", first) == identity_key("<a/>", "<b/>", second)


def test_a_reviewer_name_cannot_name_a_path():
    assert sanitize_reviewer("../../etc/passwd") == "etc-passwd"
    assert sanitize_reviewer("") == "anon"
    assert sanitize_reviewer("  ") == "anon"
    assert "/" not in sanitize_reviewer("a/b")


# ---------------------------------------------------------------------------
# rule provenance
# ---------------------------------------------------------------------------


def test_undo_rule_clears_only_what_that_rule_wrote(store):
    store.record([row(1, rule="duplicate"), row(2, rule="duplicate")], reviewer="bsw")
    store.record([row(3, rule="same_html")], reviewer="bsw")
    store.record([row(4)], reviewer="bsw")  # by hand, no rule

    cleared = store.undo_rule("org.tasks", "duplicate", reviewer="bsw")

    assert cleared == 2
    assert store.get("org.tasks", 1) is None
    assert store.get("org.tasks", 3) is not None
    assert store.get("org.tasks", 4) is not None


def test_generation_changes_when_a_verdict_is_added(store):
    before = store.generation()
    store.record([row(1)], reviewer="bsw")
    after = store.generation()

    assert before["verdicts"] == 0
    assert after["verdicts"] == 1
    assert after["files"] != before["files"]
