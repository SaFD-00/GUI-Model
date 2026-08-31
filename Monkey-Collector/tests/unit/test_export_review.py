"""Human verdicts, applied by the export. The half that makes the tool matter.

A review UI that collects opinions nobody reads is worse than none, so the
contract pinned here is that ``exclude`` actually removes the record, that it is
COUNTED where the other drops are counted, and that a verdict which no longer
matches the screens at its step is refused rather than applied.

The stale case is the subtle one. ``review/`` survives ``reset``, exactly like
``run.log``, so after a re-collection a verdict about step 42 outlives the
screens it was made about. Applying it would drop an arbitrary record; the check
lives in the pre-pass because ``_build`` -- the other place both dumps are read
-- never runs on an excluded triple, so a check there would report 0 forever.
"""

from __future__ import annotations

import json

from monkey_collector.export import Exporter
from monkey_collector.review.store import DecisionStore, identity_key
from tests.unit.test_export import triple, write_session

PACKAGE = "com.example.app"


def build(tmp_path, triples, **kwargs):
    write_session(tmp_path / "raw", PACKAGE, triples, **kwargs)
    return tmp_path


def key_for(tmp_path, step_row) -> str:
    """The identity a verdict for this triple must carry to still be valid."""
    observations = tmp_path / "raw" / PACKAGE / "observations"
    return identity_key(
        (observations / f"{step_row['before']:04d}" / "raw.xml").read_text(encoding="utf-8"),
        (observations / f"{step_row['after']:04d}" / "raw.xml").read_text(encoding="utf-8"),
        step_row["action"],
    )


def export(tmp_path, **kwargs):
    exporter = Exporter(
        tmp_path / "raw",
        tmp_path / "runtime",
        tmp_path / "out",
        review_dir=tmp_path / "review",
        ood_apps=0.0,
        id_ratio=0.0,
        **kwargs,
    )
    return exporter.run()


def records(tmp_path, split: str = "train") -> list[dict]:
    path = tmp_path / "out" / f"stage1_{split}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# the filter bites
# ---------------------------------------------------------------------------


def test_an_excluded_triple_is_not_written_and_is_counted(tmp_path):
    triples = [triple(0), triple(1)]
    build(tmp_path, triples)
    store = DecisionStore.load(tmp_path / "review")
    store.record(
        [{"package": PACKAGE, "step": 0, "verdict": "exclude", "reason": "no_effect",
          "key": key_for(tmp_path, triples[0])}],
        reviewer="bsw",
    )

    stats = export(tmp_path)

    assert stats.dropped_excluded == 1
    assert stats.total_written == 1
    assert len(records(tmp_path)) == 1


def test_a_keep_verdict_exports_the_record_and_counts_as_reviewed(tmp_path):
    triples = [triple(0)]
    build(tmp_path, triples)
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "keep", "key": key_for(tmp_path, triples[0])}],
        reviewer="bsw",
    )

    stats = export(tmp_path)

    assert stats.reviewed_kept == 1
    assert stats.dropped_excluded == 0
    assert stats.total_written == 1


def test_ignore_review_exports_everything(tmp_path):
    triples = [triple(0), triple(1)]
    build(tmp_path, triples)
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "exclude",
          "key": key_for(tmp_path, triples[0])}],
        reviewer="bsw",
    )

    stats = export(tmp_path, use_review=False)

    assert stats.dropped_excluded == 0
    assert stats.total_written == 2
    assert stats.review == {}


# ---------------------------------------------------------------------------
# staleness
# ---------------------------------------------------------------------------


def test_a_verdict_about_other_screens_is_reported_and_not_applied(tmp_path):
    build(tmp_path, [triple(0)])
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "exclude", "key": "deadbeefdeadbeef"}],
        reviewer="bsw",
    )

    stats = export(tmp_path)

    assert stats.review_stale == 1
    assert stats.dropped_excluded == 0
    assert stats.total_written == 1


def test_a_verdict_written_before_keys_existed_is_still_applied(tmp_path):
    # Refusing an unverifiable verdict would throw away real human work; the
    # hash is a check on verdicts that HAVE one, not a requirement.
    build(tmp_path, [triple(0)])
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "exclude"}], reviewer="bsw"
    )

    stats = export(tmp_path)

    assert stats.review_stale == 0
    assert stats.dropped_excluded == 1


def test_staleness_is_detected_for_exclusions_specifically(tmp_path):
    # A stale KEEP changes nothing (keeping is the default), so it must not
    # inflate the count that tells a human to go re-review an app.
    build(tmp_path, [triple(0)])
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "keep", "key": "deadbeefdeadbeef"}],
        reviewer="bsw",
    )

    stats = export(tmp_path)

    assert stats.review_stale == 0
    assert stats.total_written == 1


# ---------------------------------------------------------------------------
# the split
# ---------------------------------------------------------------------------


def test_an_excluded_triple_does_not_consume_an_id_slot(tmp_path):
    # The ID sample is drawn over the ELIGIBLE rows. If exclusions were applied
    # after the draw, an excluded row could hold a slot it never fills and the
    # ID split would silently come out below `id_ratio`.
    triples = [triple(step) for step in range(4)]
    build(tmp_path, triples)
    DecisionStore.load(tmp_path / "review").record(
        [
            {"package": PACKAGE, "step": step, "verdict": "exclude",
             "key": key_for(tmp_path, triples[step])}
            for step in (0, 1)
        ],
        reviewer="bsw",
    )

    exporter = Exporter(
        tmp_path / "raw", tmp_path / "runtime", tmp_path / "out",
        review_dir=tmp_path / "review", ood_apps=0.0, id_ratio=0.5,
    )
    stats = exporter.run()

    assert stats.dropped_excluded == 2
    assert stats.total_written == 2
    assert stats.written.get("test_id", 0) == 1
    assert stats.written.get("train", 0) == 1


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def test_export_meta_records_which_verdicts_produced_the_dataset(tmp_path):
    triples = [triple(0)]
    build(tmp_path, triples)
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "keep",
          "key": key_for(tmp_path, triples[0])}],
        reviewer="bsw",
    )

    export(tmp_path)
    meta = json.loads((tmp_path / "out" / "export_meta.json").read_text(encoding="utf-8"))

    assert meta["review"]["verdicts"] == 1
    assert "by-bsw.jsonl" in meta["review"]["files"]
    assert meta["use_review"] is True
    assert meta["dropped_excluded"] == 0


# ---------------------------------------------------------------------------
# --strict-review refuses BEFORE writing
# ---------------------------------------------------------------------------


def test_strict_review_writes_nothing_at_all(tmp_path):
    # Exiting non-zero AFTER writing leaves a complete, finished-looking export
    # that does not reflect the verdicts. Anything not checking the exit code
    # would pick that up as the corpus.
    build(tmp_path, [triple(0), triple(1)])
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "exclude", "key": "deadbeefdeadbeef"}],
        reviewer="bsw",
    )

    stats = export(tmp_path, strict_review=True)

    assert stats.review_stale == 1
    assert stats.total_written == 0
    assert not (tmp_path / "out" / "stage1_train.jsonl").exists()
    assert not (tmp_path / "out" / "images").exists()


def test_without_strict_review_the_export_is_written_and_the_verdict_skipped(tmp_path):
    build(tmp_path, [triple(0), triple(1)])
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "exclude", "key": "deadbeefdeadbeef"}],
        reviewer="bsw",
    )

    stats = export(tmp_path)

    assert stats.review_stale == 1
    assert stats.total_written == 2


def test_every_app_reports_how_many_records_it_contributed(tmp_path):
    triples = [triple(step) for step in range(3)]
    build(tmp_path, triples)
    DecisionStore.load(tmp_path / "review").record(
        [{"package": PACKAGE, "step": 0, "verdict": "exclude",
          "key": key_for(tmp_path, triples[0])}],
        reviewer="bsw",
    )

    stats = export(tmp_path)
    meta = json.loads((tmp_path / "out" / "export_meta.json").read_text(encoding="utf-8"))

    assert stats.written_by_app == {PACKAGE: 2}
    assert meta["written_by_app"] == {PACKAGE: 2}
