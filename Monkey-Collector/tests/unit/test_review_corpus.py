"""The read-only corpus view, and the two things it must not get wrong.

**It must agree with the export.** The reviewer's "export 대상만" scope is a
claim about what ``stage1_*.jsonl`` will contain. It is made by calling the
exporter's own functions rather than re-deriving them, and these tests pin each
drop reason to the exporter's counter of the same name.

**It must survive a live collection.** A sweep appends to ``triples.jsonl``
while this reads it, and writes an observation's dump before its screenshot.
Both are normal, and neither may cost more than the one row it touches.
"""

from __future__ import annotations

import json

from monkey_collector.review.corpus import (
    DROP_FOREIGN,
    DROP_MISSING_FILES,
    DROP_UNCHANGED,
    DROP_UNKNOWN_ACTION,
    AppCorpus,
    action_mark,
    discover,
    read_triples_tolerant,
)
from tests.unit.test_export import DEVICE, dump, png_bytes, tap, triple, write_session

PACKAGE = "com.example.app"


def corpus(tmp_path, triples, **kwargs):
    write_session(tmp_path / "raw", PACKAGE, triples, **kwargs)
    return AppCorpus(tmp_path / "raw", PACKAGE, cache_dir=tmp_path / "review" / "cache")


# ---------------------------------------------------------------------------
# tolerance
# ---------------------------------------------------------------------------


def test_a_half_written_final_line_is_skipped_not_raised(tmp_path):
    path = tmp_path / "triples.jsonl"
    path.write_text(
        json.dumps({"step": 0, "before": 0, "after": 1, "action": {}}) + "\n"
        + '{"step": 1, "before": 1, "aft',
        encoding="utf-8",
    )

    rows, skipped = read_triples_tolerant(path)

    assert [row["step"] for row in rows] == [0]
    assert skipped == 1


def test_an_observation_with_a_dump_but_no_screenshot_yet_is_missing_files(tmp_path):
    app = corpus(tmp_path, [triple(0)], screenshots={0: None})

    assert app.rows()[0].drop == DROP_MISSING_FILES


def test_metadata_is_never_required(tmp_path):
    app = corpus(tmp_path, [triple(0)])
    (tmp_path / "raw" / PACKAGE / "metadata.json").unlink()

    assert app.metadata() == {}
    assert app.rows()[0].exportable


# ---------------------------------------------------------------------------
# the drop classification IS the exporter's
# ---------------------------------------------------------------------------


def test_unchanged_and_foreign_and_unknown_action_are_classified_apart(tmp_path):
    app = corpus(
        tmp_path,
        [
            triple(0),
            triple(1, changed=False),
            triple(2, {"action_type": "wait"}),
            triple(3),
        ],
        dumps={4: dump(package="com.other.app"), 5: dump(package="com.other.app")},
    )
    drops = {row.step: row.drop for row in app.rows()}

    assert drops[0] == ""
    assert drops[1] == DROP_UNCHANGED
    assert drops[2] == DROP_UNKNOWN_ACTION
    assert drops[3] == DROP_FOREIGN


def test_a_dropped_row_is_never_part_of_a_duplicate_group(tmp_path):
    # Two identical unchanged triples: they repeat, but they are not in the
    # corpus, so counting them as duplicates would inflate every app-card number.
    app = corpus(tmp_path, [triple(0, changed=False), triple(1, changed=False)])

    assert all(row.group == "" for row in app.rows())
    assert app.summary().redundant == 0


# ---------------------------------------------------------------------------
# grouping is by the EXPORTED record
# ---------------------------------------------------------------------------


def test_grouping_ignores_fields_the_export_drops(tmp_path):
    # `element_index` never reaches the exported action payload. Two triples
    # that differ only there are ONE record in the corpus, so grouping on the
    # raw action would under-count duplicates -- which is the whole rule.
    same_screen = {index: dump(label="one") for index in range(4)}
    app = corpus(
        tmp_path,
        [
            triple(0, {"action_type": "tap", "element_index": 1, "x": 100, "y": 200}),
            triple(1, {"action_type": "tap", "element_index": 42, "x": 100, "y": 200}),
        ],
        dumps=same_screen,
    )
    rows = app.rows()

    assert rows[0].group == rows[1].group
    assert [row.rank for row in rows] == [0, 1]
    assert app.summary().redundant == 1


def test_a_different_coordinate_is_a_different_record(tmp_path):
    same_screen = {index: dump(label="one") for index in range(4)}
    app = corpus(
        tmp_path,
        [triple(0, tap(100, 200)), triple(1, tap(900, 1800))],
        dumps=same_screen,
    )

    rows = app.rows()
    assert rows[0].group != rows[1].group
    assert app.summary().redundant == 0


def test_same_html_is_flagged_when_the_target_equals_the_prompt(tmp_path):
    same_screen = {index: dump(label="one") for index in range(3)}
    app = corpus(tmp_path, [triple(0)], dumps=same_screen)

    assert app.rows()[0].same_html


# ---------------------------------------------------------------------------
# action marks
# ---------------------------------------------------------------------------


def test_a_keypress_carries_no_point_to_draw():
    for kind in ("press_back", "press_home", "open_app"):
        mark = action_mark({"action_type": kind}, DEVICE)
        assert mark["kind"] == "none"
        assert mark["points"] == []
        assert mark["label"] == kind


def test_a_swipe_is_two_points_and_a_tap_is_one():
    swipe = action_mark(
        {"action_type": "swipe", "x1": 10, "y1": 20, "x2": 30, "y2": 40}, DEVICE
    )
    assert swipe["kind"] == "arrow"
    assert swipe["points"] == [[10.0, 20.0], [30.0, 40.0]]

    assert action_mark(tap(5, 6), DEVICE)["points"] == [[5.0, 6.0]]


def test_input_text_is_drawn_softly_because_the_export_drops_its_coordinates():
    mark = action_mark({"action_type": "input_text", "x": 5, "y": 6, "text": "hi"}, DEVICE)

    assert mark["emphasis"] == "soft"
    assert mark["label"].startswith("input_text: hi")


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


def test_each_observation_keeps_its_own_frame_when_the_screen_rotated(tmp_path):
    landscape = (2400, 1080)
    app = corpus(
        tmp_path,
        [triple(0)],
        dumps={1: dump(device=landscape)},
        screenshots={1: png_bytes(landscape)},
    )
    row = app.rows()[0]

    assert tuple(row.before_size) == DEVICE
    assert tuple(row.after_size) == landscape
    assert app.summary().landscape_obs == 1


def test_discover_lists_only_packages_with_a_session(tmp_path):
    write_session(tmp_path / "raw", PACKAGE, [triple(0)])
    (tmp_path / "raw" / "not-an-app").mkdir()

    assert discover(tmp_path / "raw") == [PACKAGE]


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_the_analysis_cache_is_not_written_for_a_live_app(tmp_path, monkeypatch):
    app = corpus(tmp_path, [triple(0)])
    monkeypatch.setattr(AppCorpus, "is_live", lambda self: True)

    app.rows(refresh=True)

    assert not (tmp_path / "review" / "cache" / "rows").exists()


def test_the_cache_is_invalidated_when_triples_grow(tmp_path):
    app = corpus(tmp_path, [triple(0)])
    app.rows()
    cache = tmp_path / "review" / "cache" / "rows" / f"{PACKAGE}.json"
    assert cache.is_file()

    with (tmp_path / "raw" / PACKAGE / "triples.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(triple(1)) + "\n")

    fresh = AppCorpus(tmp_path / "raw", PACKAGE, cache_dir=tmp_path / "review" / "cache")
    assert len(fresh.rows()) == 2
