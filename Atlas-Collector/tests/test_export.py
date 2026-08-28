"""EXP08 Stage-1 export.

Every invariant here was read off the real corpus on ubuntu1.fclab, not inferred
from the builder's code. A test that fails here means the export would be
rejected — or worse, silently dropped — by `build_exp08_data.py`.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from atlas_collector._exp08_prompt import SYSTEM_PROMPT, SYSTEM_PROMPT_MD5
from atlas_collector.export import (
    IMAGE_PREFIX,
    Exporter,
    build_record,
    encode_screen,
    prune_inverted_nodes,
    split_apps,
    write_jpeg,
)
from atlas_collector.session import Session

FIXTURES = Path(__file__).parent / "fixtures" / "pages"
FRAME = (840, 1876)
DEVICE = (1080, 2400)

#: The builder's own regex (build_exp08_data._IMG_RE).
IMG_RE = re.compile(r"^myset/images/episode_(.+)_step_(\d+)\.jpg$")


def png_bytes(size=DEVICE) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def dump(name: str = "settings_root") -> str:
    return (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")


def make_session(tmp_path, package: str, steps: int, *, changed: bool = True) -> Session:
    session = Session(package, tmp_path / "data", tmp_path / "runtime", episode=package)
    session.open(resume=False)
    names = ["settings_root", "subsettings_a", "subsettings_a_scrolled", "files_list"]
    previous = None
    for index in range(steps + 1):
        source = names[index % len(names)] if changed else names[0]
        observation = session.write_observation(
            png=png_bytes(),
            raw_xml=dump(source),
            page_key=str(index if changed else 0),
            activity=f"{package}/.Main",
            state_str=f"s{index}" if changed else "same",
            is_new_page=changed,
            match_kind="new",
        )
        if previous is not None:
            session.write_triple(
                before=previous,
                after=observation,
                action={"action": "click", "coordinate": [100, 200]},
            )
        previous = observation
    session.write_metadata(
        completed=True, extra={"device_width": DEVICE[0], "device_height": DEVICE[1]}
    )
    return session


# ---------------------------------------------------------------------------
# The system prompt is a literal, not a template
# ---------------------------------------------------------------------------


def test_the_system_prompt_matches_the_corpus_byte_for_byte():
    """A record whose system turn differs is a different task wearing the same
    name — the model would train on two prompts and be evaluated on one."""
    assert hashlib.md5(SYSTEM_PROMPT.encode()).hexdigest() == SYSTEM_PROMPT_MD5
    assert SYSTEM_PROMPT.startswith("# Mode: NEXT_STATE_PREDICTION")
    assert "840 x 1876" in SYSTEM_PROMPT, "the frame is baked into the literal"


# ---------------------------------------------------------------------------
# The record shape
# ---------------------------------------------------------------------------


def record() -> dict:
    return build_record(
        before_xml='<div data-bbox="0 0 840 1876"/>',
        after_xml='<button data-bbox="0 0 10 10"/>',
        action={"action": "click", "coordinate": [420, 305]},
        image_path=f"{IMAGE_PREFIX}/episode_0_step_0002.jpg",
    )


def test_exactly_two_top_level_keys_and_no_sample_id():
    """`build_wm_formats.py` injects sample_id as a positional index downstream;
    emitting our own would collide with it."""
    assert sorted(record()) == ["images", "messages"]


def test_messages_are_sharegpt_triples_in_order():
    messages = record()["messages"]
    assert [m["from"] for m in messages] == ["system", "human", "gpt"]
    assert all(sorted(m) == ["from", "value"] for m in messages), "from/value, not role/content"


def test_the_human_turn_is_xml_first():
    """The builder's `_copy_ratio_of_rec` splits on 'Current UI State:' then
    '[Screenshot]'. Stage-2's img-first ordering makes that split yield garbage,
    and the copy-ratio filter then mis-scores every record rather than erroring.
    """
    human = record()["messages"][1]["value"]
    assert human.startswith("Current UI State:\n")
    assert human.index("Current UI State:") < human.index("[Screenshot]") < human.index("Action:")
    assert human.count("<image>") == 1, "must match len(images)"
    assert human.endswith("</action>")


def test_the_gpt_turn_is_bare_xml_with_no_trailing_newline():
    """All 3,000 sampled corpus gpt values end at '>'. The parser's pretty_xml
    lstrips but never rstrips, so the newline must be removed here."""
    gpt = record()["messages"][2]["value"]
    assert gpt.endswith(">")
    assert not gpt.endswith("\n")
    assert "<thought>" not in gpt, "no wrapper — that is stage 2"


def test_encode_screen_strips_the_parsers_trailing_newline():
    from atlas_collector.xml import parse_device_xml

    raw = dump()
    assert parse_device_xml(raw, *DEVICE, strict_frame=False).endswith("\n")
    assert not encode_screen(raw, *DEVICE).endswith("\n")


# ---------------------------------------------------------------------------
# Geometry the corpus never contains
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["settings_root", "subsettings_a", "subsettings_a_scrolled"])
def test_no_exported_box_is_inverted_or_negative(name):
    """Measured over 3,000 real EXP08 records: 266,407 boxes, ZERO inverted and
    ZERO negative. uiautomator emits inverted bounds for content scrolled past
    the viewport, and the parser passes them through faithfully, so export must
    remove them or ship geometry no renderer can draw.
    """
    encoded = encode_screen(dump(name), *DEVICE)
    for bbox in re.findall(r'data-bbox="([^"]+)"', encoded):
        x1, y1, x2, y2 = (int(v) for v in bbox.split())
        assert 0 <= x1 <= x2, bbox
        assert 0 <= y1 <= y2, bbox


def test_the_real_dump_really_does_contain_an_inverted_node():
    """Guards the guard: if the fixture stops exercising this, the test above
    passes for the wrong reason."""
    _, dropped = prune_inverted_nodes(dump("settings_root"))
    assert dropped == 1


def test_pruning_promotes_children_rather_than_deleting_a_subtree():
    """Every instance seen so far is a childless leaf, but dropping a subtree
    because its container scrolled off would remove real content — invisibly."""
    xml = (
        '<hierarchy><node class="A" bounds="[0,0][10,10]">'
        '<node class="OffScreen" bounds="[5,900][5,100]">'
        '<node class="Keep" bounds="[0,0][10,10]"/>'
        "</node></node></hierarchy>"
    )
    pruned, dropped = prune_inverted_nodes(xml)
    assert dropped == 1
    assert 'class="Keep"' in pruned
    assert 'class="OffScreen"' not in pruned


def test_a_clean_dump_is_returned_untouched():
    xml = '<hierarchy><node class="A" bounds="[0,0][10,10]"/></hierarchy>'
    pruned, dropped = prune_inverted_nodes(xml)
    assert dropped == 0
    assert pruned is xml, "no reserialisation when there is nothing to fix"


# ---------------------------------------------------------------------------
# The image contract
# ---------------------------------------------------------------------------


def test_the_image_path_matches_the_builders_regex():
    match = IMG_RE.match(record()["images"][0])
    assert match is not None
    assert len(match.group(2)) == 4, "step must be zero-padded to 4"


def test_a_mispadded_step_would_still_match_and_that_is_the_danger():
    """`step_2` passes the regex, resolves to a file that does not exist, and the
    length filter drops the record indistinguishably from a too-long one."""
    assert IMG_RE.match("myset/images/episode_0_step_2.jpg") is not None


def test_write_jpeg_resizes_to_the_export_frame(tmp_path):
    destination = tmp_path / "out" / "x.jpg"
    write_jpeg(png_bytes(), destination, FRAME)
    with Image.open(destination) as image:
        assert image.size == FRAME
        assert image.format == "JPEG"


# ---------------------------------------------------------------------------
# ID / OOD — two independent knobs
# ---------------------------------------------------------------------------


def test_apps_are_held_out_whole():
    """A single leaked triple makes 'unseen app' false for the whole app, and
    nothing downstream can detect it."""
    seen, held = split_apps([f"app{i}" for i in range(10)], 0.3, seed=1)
    assert len(held) == 3
    assert not set(seen) & set(held)
    assert len(seen) + len(held) == 10


def test_the_split_is_stable_for_a_seed_regardless_of_input_order():
    forward = split_apps(["a", "b", "c", "d"], 0.5, seed=7)
    backward = split_apps(["d", "c", "b", "a"], 0.5, seed=7)
    assert forward == backward


def test_a_requested_ood_fraction_never_silently_rounds_to_zero_apps():
    _, held = split_apps(["a", "b", "c"], 0.05, seed=1)
    assert len(held) == 1, "asking for OOD and getting none is a meaningless split"


def test_ood_never_consumes_every_app():
    seen, held = split_apps(["a", "b"], 1.0, seed=1)
    assert seen, "training on nothing is not a split"


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_an_out_of_range_ood_fraction_is_rejected(bad):
    with pytest.raises(ValueError, match="ood_apps"):
        split_apps(["a"], bad, seed=1)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_a_full_export_satisfies_every_contract_invariant(tmp_path):
    for package in ("com.a", "com.b", "com.c"):
        make_session(tmp_path, package, steps=4)

    exporter = Exporter(
        tmp_path / "data",
        tmp_path / "runtime",
        tmp_path / "out",
        frame=FRAME,
        device_size=DEVICE,
        ood_apps=0.34,
        id_ratio=0.25,
        seed=3,
    )
    stats = exporter.run()
    assert stats.total_written > 0
    assert len(stats.ood_apps) == 1

    seen_system = set()
    for path in sorted((tmp_path / "out").glob("stage1_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            assert sorted(rec) == ["images", "messages"]
            seen_system.add(rec["messages"][0]["value"])
            human = rec["messages"][1]["value"]
            assert human.count("<image>") == len(rec["images"]) == 1
            assert rec["messages"][2]["value"].endswith(">")
            name = Path(rec["images"][0]).name
            assert (tmp_path / "out" / "images" / name).is_file()
            for bbox in re.findall(r'data-bbox="([^"]+)"', human):
                x1, y1, x2, y2 = (int(v) for v in bbox.split())
                assert 0 <= x1 <= x2 <= FRAME[0]
                assert 0 <= y1 <= y2 <= FRAME[1]
    assert len(seen_system) == 1, "the system prompt must never vary"


def test_ood_app_triples_never_appear_in_train(tmp_path):
    for package in ("com.a", "com.b"):
        make_session(tmp_path, package, steps=3)
    exporter = Exporter(
        tmp_path / "data", tmp_path / "runtime", tmp_path / "out",
        frame=FRAME, device_size=DEVICE, ood_apps=0.5, id_ratio=0.0, seed=1,
    )
    stats = exporter.run()
    held = set(stats.ood_apps)
    train = (tmp_path / "out" / "stage1_train.jsonl").read_text()
    for package in held:
        assert f"episode_{package}_" not in train


def test_unchanged_triples_are_dropped_by_default_and_kept_on_request(tmp_path):
    make_session(tmp_path, "com.same", steps=4, changed=False)
    common = dict(frame=FRAME, device_size=DEVICE, ood_apps=0.0, id_ratio=0.0, seed=1)

    dropped = Exporter(
        tmp_path / "data", tmp_path / "runtime", tmp_path / "out1", **common
    ).run()
    assert dropped.total_written == 0
    assert dropped.dropped_unchanged == 4

    kept = Exporter(
        tmp_path / "data", tmp_path / "runtime", tmp_path / "out2",
        keep_unchanged=True, **common
    ).run()
    assert kept.total_written == 4


def test_the_session_recorded_resolution_is_preferred_over_the_config(tmp_path):
    """A permission dialog's window is legitimately smaller than the display.

    Deriving the frame from the dump confuses that with a wrong resolution — it
    discarded 6 of 26 real triples as 'frame mismatch' when nothing was
    mismatched. The session records what it measured, so there is no guess.
    """
    session = make_session(tmp_path, "com.a", steps=2)
    assert session.device_size() == DEVICE

    exporter = Exporter(
        tmp_path / "data", tmp_path / "runtime", tmp_path / "out",
        frame=FRAME, device_size=(1440, 3120),  # deliberately wrong
        ood_apps=0.0, id_ratio=0.0, seed=1,
    )
    stats = exporter.run()
    assert stats.dropped_unparsable == 0
    assert stats.total_written == 2


def test_export_meta_records_the_split_parameters(tmp_path):
    make_session(tmp_path, "com.a", steps=2)
    Exporter(
        tmp_path / "data", tmp_path / "runtime", tmp_path / "out",
        frame=FRAME, device_size=DEVICE, ood_apps=0.0, id_ratio=0.5, seed=11,
    ).run()
    meta = json.loads((tmp_path / "out" / "export_meta.json").read_text())
    assert meta["seed"] == 11
    assert meta["id_ratio"] == 0.5
    assert meta["frame"] == list(FRAME)


def test_an_empty_corpus_reports_rather_than_crashes(tmp_path):
    stats = Exporter(tmp_path / "nothing", tmp_path / "runtime", tmp_path / "out").run()
    assert stats.total_written == 0
