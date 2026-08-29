"""EXP08 Stage-1 export — the record shape, and the coordinate frame.

The contract these tests pin is ARCHITECTURE §8 / AGENTS §2(d), read off the
canonical corpus on ubuntu1.fclab. Two of its invariants fail SILENTLY when
broken, so they get the most attention here:

**The action frame.** ``coordinate`` / ``coordinate1`` / ``coordinate2`` are in
the same resized frame as ``data-bbox``. Atlas-Collector writes device pixels
instead; 67 of its 657 coordinates land outside the frame and the rest point at
the wrong place while staying inside it. Nothing downstream can detect the
second kind, so ``test_action_coordinate_lands_inside_the_tapped_elements_bbox``
is the real contract and
``test_rescale_is_anisotropic_and_matches_the_parsers_own_factors`` is what
catches a uniform or transposed scale that containment alone would tolerate.

**Step padding.** ``episode_{EP}_step_{STEP:04d}.jpg`` — an unpadded step still
matches the builder's regex and resolves to a file that does not exist.

Nothing here touches a device or an API: every fixture is a synthetic dump.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from monkey_collector import export as ex
from monkey_collector._exp08_prompt import SYSTEM_PROMPT, SYSTEM_PROMPT_MD5
from monkey_collector.session import Session
from monkey_collector.xml import parse_device_xml

PACKAGE = "com.example.app"
DEVICE = (1080, 2400)
FRAME = (840, 1876)

#: A small, off-centre, bottom-right element. Not a full-width bar: a box that
#: spans the screen contains almost any point, so it would pass even with a
#: wrong scale factor.
TARGET_BOUNDS = (704, 1811, 968, 1907)
TARGET_CENTER = (836, 1859)  # what the explorer's `element.center` would record


# ---------------------------------------------------------------------------
# fixtures on disk
# ---------------------------------------------------------------------------


def _node(cls: str, bounds: tuple[int, int, int, int], *, text: str = "",
          package: str = PACKAGE, clickable: str = "false", children: str = "") -> str:
    left, top, right, bottom = bounds
    return (
        f'<node index="0" text="{text}" resource-id="" class="{cls}" package="{package}" '
        f'content-desc="" checkable="false" checked="false" clickable="{clickable}" '
        f'enabled="true" focusable="true" focused="false" scrollable="false" '
        f'long-clickable="false" password="false" selected="false" '
        f'bounds="[{left},{top}][{right},{bottom}]" drawing-order="1" hint="">{children}</node>'
    )


def dump(
    *,
    package: str = PACKAGE,
    label: str = "Hello",
    device: tuple[int, int] = DEVICE,
    extra: str = "",
) -> str:
    """A raw uiautomator dump with one label and one small clickable target."""
    width, height = device
    body = (
        _node("android.widget.TextView", (100, 300, 600, 360), text=label, package=package)
        + _node(
            "android.widget.Button", TARGET_BOUNDS, text="Save",
            package=package, clickable="true",
        )
        + extra
    )
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
        '<hierarchy rotation="0">'
        + _node(
            "android.widget.FrameLayout", (0, 0, width, height),
            package=package, children=body,
        )
        + "</hierarchy>"
    )


def png_bytes() -> bytes:
    """A tiny PNG. ``write_jpeg`` resizes whatever it is given to the frame."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 16), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def tap(x: int = TARGET_CENTER[0], y: int = TARGET_CENTER[1]) -> dict:
    return {"action_type": "tap", "element_index": 1, "x": x, "y": y}


def write_session(
    raw_root: Path,
    package: str,
    triples: list[dict],
    *,
    dumps: dict[int, str] | None = None,
    screenshots: dict[int, bytes] | None = None,
    device: tuple[int, int] | None = DEVICE,
) -> Path:
    """One collected session on disk: observations, triples.jsonl, metadata.json.

    ``device=None`` writes metadata WITHOUT ``device_width``/``device_height``,
    i.e. a session predating the field.
    """
    app_dir = raw_root / package
    indices = {t["before"] for t in triples} | {t["after"] for t in triples}
    for index in sorted(indices):
        obs = app_dir / "observations" / f"{index:04d}"
        obs.mkdir(parents=True, exist_ok=True)
        raw = (dumps or {}).get(index, dump(package=package, label=f"screen {index}"))
        if raw is not None:
            (obs / "raw.xml").write_text(raw, encoding="utf-8")
        image = (screenshots or {}).get(index, png_bytes())
        if image is not None:
            (obs / "screenshot.png").write_bytes(image)
    with (app_dir / "triples.jsonl").open("w", encoding="utf-8") as handle:
        for triple in triples:
            handle.write(json.dumps(triple) + "\n")
    meta = {"package": package, "episode": package, "observations": len(indices),
            "steps": len(triples), "completed_at": 1.0}
    if device is not None:
        meta["device_width"], meta["device_height"] = device
    (app_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return app_dir


def triple(step: int, action: dict | None = None, *, changed: bool = True,
           before: int | None = None, after: int | None = None) -> dict:
    return {
        "step": step,
        "before": step if before is None else before,
        "after": step + 1 if after is None else after,
        "action": tap() if action is None else action,
        "changed": changed,
        "page_changed": changed,
        "reason": "explore",
        "from_page": 0,
        "to_page": 1,
    }


def run_export(tmp_path: Path, **kwargs) -> tuple[ex.ExportStats, Path]:
    """Export ``tmp_path/raw`` into ``tmp_path``; returns (stats, out_dir)."""
    params = {"ood_apps": 0.0, "id_ratio": 0.0, "seed": 8}
    params.update(kwargs)
    exporter = ex.Exporter(
        tmp_path / "raw", tmp_path / "runtime", tmp_path, **params
    )
    return exporter.run(), tmp_path


def records(out_dir: Path, split: str = "train") -> list[dict]:
    path = out_dir / f"stage1_{split}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def action_of(record: dict) -> dict:
    """The wire payload back out of the human turn."""
    human = record["messages"][1]["value"]
    return json.loads(human.split("<action>", 1)[1].split("</action>", 1)[0])


# ---------------------------------------------------------------------------
# 1. the system prompt is a literal
# ---------------------------------------------------------------------------


def test_system_prompt_is_byte_identical_to_the_corpus():
    """md5 measured over all 60,871 records of EXP08_stage1_state.jsonl.

    Reformat it, or interpolate the frame size into it, and every exported
    record becomes a different task wearing the same name.
    """
    assert hashlib.md5(SYSTEM_PROMPT.encode()).hexdigest() == "2b25a54f5ace1e15a94640ef64481809"
    assert SYSTEM_PROMPT_MD5 == "2b25a54f5ace1e15a94640ef64481809"


def test_system_prompt_states_the_frame_as_a_literal_not_a_placeholder():
    assert "840 x 1876" in SYSTEM_PROMPT
    assert "{" not in SYSTEM_PROMPT.replace('{"action"', ""), "it is a literal, not a template"


# ---------------------------------------------------------------------------
# 2. coordinates — the part Atlas gets wrong
# ---------------------------------------------------------------------------


def test_every_exported_action_coordinate_is_inside_the_frame(tmp_path):
    """Device pixels leaking through would put x past 840 and y past 1876."""
    write_session(
        tmp_path / "raw",
        PACKAGE,
        [
            triple(0),
            triple(1, {"action_type": "swipe", "element_index": 2, "x1": 540, "y1": 2100,
                       "x2": 1050, "y2": 300, "duration_ms": 300}),
            triple(2, {"action_type": "long_press", "element_index": 3,
                       "x": 1070, "y": 2390, "duration_ms": 1000}),
        ],
    )
    stats, out = run_export(tmp_path)
    assert stats.total_written == 3
    points = [
        pt
        for record in records(out)
        for key in ex.COORDINATE_KEYS
        if (pt := action_of(record).get(key)) is not None
    ]
    assert len(points) == 4
    for x, y in points:
        assert 0 <= x <= FRAME[0], f"x={x} is outside the {FRAME[0]}px frame"
        assert 0 <= y <= FRAME[1], f"y={y} is outside the {FRAME[1]}px frame"
    assert stats.coords_out_of_frame == 0


def test_action_coordinate_lands_inside_the_tapped_elements_bbox(tmp_path):
    """THE contract: the action and the boxes must be in one frame.

    Atlas's remaining 590 in-frame coordinates fail exactly this while passing
    the bounds check above, which is why this test exists separately.
    """
    write_session(tmp_path / "raw", PACKAGE, [triple(0)])
    _, out = run_export(tmp_path)
    record = records(out)[0]

    x, y = action_of(record)["coordinate"]
    xml = record["messages"][1]["value"].split("Current UI State:\n", 1)[1]
    line = next(ln for ln in xml.splitlines() if ">Save<" in ln)
    left, top, right, bottom = (int(v) for v in line.split('data-bbox="')[1].split('"')[0].split())

    assert left <= x <= right, f"x={x} outside the tapped element's box [{left}, {right}]"
    assert top <= y <= bottom, f"y={y} outside the tapped element's box [{top}, {bottom}]"


def test_rescale_is_anisotropic_and_matches_the_parsers_own_factors():
    """A uniform scale, a transposed one, or ``int()`` all change these numbers.

    x_scale = 840/1080 = 0.777778 and y_scale = 1876/2400 = 0.781667 — close
    enough that a containment check tolerates using one for both, so the exact
    values are pinned here instead.
    """
    payload = ex.translate_action({"action_type": "tap", "x": 1000, "y": 2300},
                                  device_size=DEVICE)
    assert payload == {"action": "click", "coordinate": [778, 1798]}
    assert payload["coordinate"] != [778, 1789], "y must not use the x scale"
    assert payload["coordinate"] != [782, 1798], "the two scales must not be transposed"
    # `int()` truncation instead of `round()`: 0.5-off from the boxes.
    assert ex.translate_action({"action_type": "tap", "x": 899, "y": 899},
                               device_size=DEVICE)["coordinate"] == [699, 703]


def test_resized_frame_derives_the_contract_frame_and_is_not_transposed():
    assert ex.resized_frame(1080, 2400) == FRAME
    assert ex.resized_frame(1440, 3120) == (840, 1848)
    assert ex.resized_frame(2400, 1080) != FRAME, "arguments are (width, height)"


def test_device_size_recorded_by_the_session_beats_the_config_default(tmp_path):
    """A session collected at 1440x3120 must not be rescaled as 1080x2400.

    Hardcoding the frame (or falling back to the config while metadata says
    otherwise) rescales every box AND every coordinate silently.
    """
    device = (1440, 3120)
    write_session(
        tmp_path / "raw",
        PACKAGE,
        [triple(0, {"action_type": "tap", "element_index": 1, "x": 1200, "y": 3000})],
        dumps={0: dump(device=device), 1: dump(device=device, label="next")},
        device=device,
    )
    stats, out = run_export(tmp_path, device_size=DEVICE)

    frame = ex.resized_frame(*device)
    assert frame == (840, 1848) != FRAME
    assert stats.frames[PACKAGE] == [1440, 3120, 840, 1848]
    assert stats.assumed_device_size == []
    expected = [round(1200 * 840 / 1440), round(3000 * 1848 / 3120)]
    assert action_of(records(out)[0])["coordinate"] == expected
    # The JPEG follows the derived frame too, so the pixels agree with the boxes.
    from PIL import Image

    with Image.open(out / "images" / "episode_com.example.app_step_0000.jpg") as image:
        assert image.size == frame


def test_session_without_a_recorded_device_size_falls_back_and_says_so(tmp_path):
    write_session(tmp_path / "raw", PACKAGE, [triple(0)], device=None)
    stats, _ = run_export(tmp_path, device_size=DEVICE)
    assert stats.assumed_device_size == [PACKAGE]
    assert stats.frames[PACKAGE] == [1080, 2400, 840, 1876]


# ---------------------------------------------------------------------------
# 3. action translation — seven in, seven out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "wire"),
    [
        ({"action_type": "tap", "x": 540, "y": 1200},
         {"action": "click", "coordinate": [420, 938]}),
        ({"action_type": "long_press", "x": 540, "y": 1200, "duration_ms": 1000},
         {"action": "long_press", "coordinate": [420, 938]}),
        ({"action_type": "input_text", "text": "hello", "x": 540, "y": 1200},
         {"action": "type", "text": "hello"}),
        ({"action_type": "swipe", "x1": 540, "y1": 1200, "x2": 540, "y2": 600,
          "duration_ms": 300},
         {"action": "swipe", "coordinate1": [420, 938], "coordinate2": [420, 469]}),
        ({"action_type": "press_back"}, {"action": "navigate_back"}),
        ({"action_type": "press_home"}, {"action": "navigate_home"}),
        ({"action_type": "open_app", "package": "com.x", "app_name": "Tasks"},
         {"action": "open", "app_name": "Tasks"}),
    ],
)
def test_all_seven_domain_actions_translate_to_their_exp08_payload(domain, wire):
    assert ex.translate_action(domain, device_size=DEVICE) == wire


def test_translated_payload_keys_are_exactly_the_corpus_keys():
    """No `duration_ms`, and no `coordinate` on a `type`."""
    long_press = ex.translate_action(
        {"action_type": "long_press", "x": 1, "y": 2, "duration_ms": 1000}, device_size=DEVICE
    )
    assert set(long_press) == {"action", "coordinate"}
    swipe = ex.translate_action(
        {"action_type": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration_ms": 300},
        device_size=DEVICE,
    )
    assert set(swipe) == {"action", "coordinate1", "coordinate2"}
    typed = ex.translate_action(
        {"action_type": "input_text", "text": "hi", "x": 540, "y": 1200}, device_size=DEVICE
    )
    assert set(typed) == {"action", "text"}, "the x/y it tapped to focus must not ship"
    # `action` first, as in the corpus: json.dumps preserves insertion order.
    assert list(swipe)[0] == "action"


@pytest.mark.parametrize("kind", ["wait", "terminate", "", "screenshot"])
def test_unknown_action_types_translate_to_nothing(kind):
    """`wait`/`terminate` are in the prompt's space but never in a human turn."""
    assert ex.translate_action({"action_type": kind}, device_size=DEVICE) is None


def test_unknown_action_type_is_counted_and_dropped_not_passed_through(tmp_path):
    write_session(
        tmp_path / "raw",
        PACKAGE,
        [triple(0), triple(1, {"action_type": "teleport", "x": 1, "y": 2})],
    )
    stats, out = run_export(tmp_path)
    assert stats.dropped_unknown_action == 1
    assert stats.total_written == 1
    assert all("teleport" not in json.dumps(r) for r in records(out))


def test_open_app_without_a_name_falls_back_to_the_package():
    payload = ex.translate_action(
        {"action_type": "open_app", "package": "com.x", "app_name": ""}, device_size=DEVICE
    )
    assert payload == {"action": "open", "app_name": "com.x"}


# ---------------------------------------------------------------------------
# 4. record structure
# ---------------------------------------------------------------------------


def test_record_has_exactly_two_top_level_keys_and_no_sample_id(tmp_path):
    write_session(tmp_path / "raw", PACKAGE, [triple(0)])
    _, out = run_export(tmp_path)
    record = records(out)[0]
    assert set(record) == {"messages", "images"}
    assert "sample_id" not in record


def test_three_turns_are_system_human_gpt_keyed_from_and_value(tmp_path):
    write_session(tmp_path / "raw", PACKAGE, [triple(0)])
    _, out = run_export(tmp_path)
    messages = records(out)[0]["messages"]
    assert [m["from"] for m in messages] == ["system", "human", "gpt"]
    assert all(set(m) == {"from", "value"} for m in messages)
    assert messages[0]["value"] == SYSTEM_PROMPT


def test_human_turn_is_xml_first_and_gpt_turn_is_bare_xml_ending_at_a_tag(tmp_path):
    """Stage 2 is image-first; swapped, the builder's split silently yields garbage."""
    write_session(tmp_path / "raw", PACKAGE, [triple(0)])
    _, out = run_export(tmp_path)
    human = records(out)[0]["messages"][1]["value"]
    gpt = records(out)[0]["messages"][2]["value"]

    assert human.startswith("Current UI State:\n")
    assert human.index("Current UI State:") < human.index("[Screenshot]") < human.index("Action:")
    assert "\n\n[Screenshot]\n<image>\n\nAction:\n<action>" in human
    assert human.endswith("</action>")

    assert gpt.startswith("<"), "no wrapper around the next state"
    assert gpt.endswith(">"), "the parser's pretty_xml leaves a trailing newline; rstrip it"
    assert "Current UI State:" not in gpt


def test_images_is_a_single_prefixed_path(tmp_path):
    write_session(tmp_path / "raw", PACKAGE, [triple(0)])
    _, out = run_export(tmp_path)
    images = records(out)[0]["images"]
    assert len(images) == 1
    assert images[0].startswith(f"{ex.IMAGE_PREFIX}/")


def test_image_path_pads_the_step_to_four_digits_and_leaves_the_episode_unpadded(tmp_path):
    """`step_2` matches the builder's regex and resolves to a missing file."""
    write_session(tmp_path / "raw", PACKAGE, [triple(2), triple(13)])
    _, out = run_export(tmp_path)
    names = sorted(r["images"][0] for r in records(out))
    assert names == [
        f"myset/images/episode_{PACKAGE}_step_0002.jpg",
        f"myset/images/episode_{PACKAGE}_step_0013.jpg",
    ]
    for name in names:
        assert (out / "images" / Path(name).name).is_file(), "the record must name a real file"


# ---------------------------------------------------------------------------
# 5. inverted bounds
# ---------------------------------------------------------------------------


def test_inverted_bounds_node_is_dropped_and_its_children_are_promoted():
    """uiautomator emits `[221,2390][650,2337]` for content past the viewport."""
    child = _node("android.widget.TextView", (221, 2337, 650, 2380), text="kept")
    raw = (
        '<hierarchy rotation="0">'
        + _node(
            "android.widget.FrameLayout", (0, 0, 1080, 2400),
            children=_node(
                "android.widget.LinearLayout", (221, 2390, 650, 2337), children=child
            ),
        )
        + "</hierarchy>"
    )
    pruned, dropped = ex.prune_inverted_nodes(raw)
    assert dropped == 1
    assert 'bounds="[221,2390][650,2337]"' not in pruned
    assert 'text="kept"' in pruned, "a child must be promoted, never deleted with its parent"


def test_export_contains_no_inverted_data_bbox(tmp_path):
    inverted = _node("android.widget.TextView", (221, 2390, 650, 2337), text="offscreen")
    write_session(
        tmp_path / "raw", PACKAGE, [triple(0)],
        dumps={0: dump(extra=inverted), 1: dump(label="next", extra=inverted)},
    )
    _, out = run_export(tmp_path)
    for record in records(out):
        for value in (record["messages"][1]["value"], record["messages"][2]["value"]):
            for raw_box in value.split('data-bbox="')[1:]:
                x1, y1, x2, y2 = (int(v) for v in raw_box.split('"')[0].split())
                assert x1 <= x2 and y1 <= y2, "the corpus has 0 inverted boxes in 266,407"


def test_encode_screen_matches_the_parser_for_a_clean_dump():
    raw = dump()
    assert ex.encode_screen(raw, *DEVICE) == parse_device_xml(
        raw, *DEVICE, strict_frame=False
    ).rstrip()


def test_encode_screen_tolerates_a_partial_window(tmp_path):
    """strict_frame=False: a dialog's dump is legitimately smaller than the screen."""
    dialog = (
        '<hierarchy rotation="0">'
        + _node("android.widget.FrameLayout", (28, 822, 1052, 1642), text="Allow?")
        + "</hierarchy>"
    )
    assert "data-bbox" in ex.encode_screen(dialog, *DEVICE)


# ---------------------------------------------------------------------------
# 6. drop reasons — five, each counted
# ---------------------------------------------------------------------------


def test_unchanged_triples_are_dropped_by_default_and_kept_on_request(tmp_path):
    write_session(tmp_path / "raw", PACKAGE, [triple(0), triple(1, changed=False)])
    stats, _ = run_export(tmp_path)
    assert (stats.dropped_unchanged, stats.total_written) == (1, 1)

    other = tmp_path / "keep"
    write_session(other / "raw", PACKAGE, [triple(0), triple(1, changed=False)])
    stats_kept, _ = run_export(other, keep_unchanged=True)
    assert (stats_kept.dropped_unchanged, stats_kept.total_written) == (0, 2)


def test_missing_files_are_dropped_and_counted(tmp_path):
    write_session(
        tmp_path / "raw",
        PACKAGE,
        [triple(0), triple(1, {"action_type": "tap", "element_index": 1, "x": 5000, "y": 9000})],
        screenshots={1: None},
    )
    stats, _ = run_export(tmp_path)
    assert (stats.dropped_missing_files, stats.total_written) == (1, 1)
    # The dropped triple's coordinate is far outside the frame; a record that was
    # never written must not count against the corpus's headline number.
    assert stats.coords_out_of_frame == 0


def test_foreign_screens_are_dropped_and_counted(tmp_path):
    """Another app's screen inside this app's split is an undetectable OOD leak."""
    write_session(
        tmp_path / "raw", PACKAGE, [triple(0), triple(1)],
        dumps={2: dump(package="net.gsantner.markor", label="markor")},
    )
    stats, _ = run_export(tmp_path)
    assert (stats.dropped_foreign, stats.total_written) == (1, 1)


def test_unparsable_dump_is_dropped_and_counted(tmp_path):
    write_session(
        tmp_path / "raw", PACKAGE, [triple(0), triple(1)],
        dumps={2: "<hierarchy><node bounds='nonsense'"},
    )
    stats, _ = run_export(tmp_path)
    assert (stats.dropped_unparsable, stats.total_written) == (1, 1)


def test_duplicate_step_is_dropped_and_counted(tmp_path):
    """A resume bug produced 22 duplicated steps; two records then claim one JPEG."""
    write_session(tmp_path / "raw", PACKAGE, [triple(0), triple(0, before=1, after=2)])
    stats, _ = run_export(tmp_path)
    assert (stats.dropped_duplicate_step, stats.total_written) == (1, 1)


def test_export_meta_records_every_drop_counter_and_the_frames(tmp_path):
    write_session(tmp_path / "raw", PACKAGE, [triple(0), triple(1, changed=False)])
    stats, out = run_export(tmp_path)
    meta = json.loads((out / "export_meta.json").read_text(encoding="utf-8"))
    for key in (
        "dropped_unchanged", "dropped_unparsable", "dropped_missing_files",
        "dropped_foreign", "dropped_duplicate_step", "dropped_unknown_action",
    ):
        assert key in meta, key
    assert meta["dropped_unchanged"] == 1
    assert meta["frames"][PACKAGE] == [1080, 2400, 840, 1876]
    assert meta["target_size"] == [840, 1876]
    assert meta["coords_out_of_frame"] == 0
    assert meta["total_written"] == stats.total_written


# ---------------------------------------------------------------------------
# 7. splits — two independent knobs
# ---------------------------------------------------------------------------


def test_split_apps_holds_out_whole_apps_and_is_seed_stable():
    packages = [f"com.app{i}" for i in range(10)]
    seen, held = ex.split_apps(packages, 0.3, seed=8)
    assert len(held) == 3
    assert not set(seen) & set(held)
    assert sorted(seen + held) == sorted(packages)
    assert ex.split_apps(list(reversed(packages)), 0.3, seed=8) == (seen, held)


def test_split_apps_never_silently_holds_out_zero_or_everything():
    assert ex.split_apps(["a", "b", "c"], 0.1, seed=1)[1] == ["b"]
    assert len(ex.split_apps(["a", "b", "c"], 1.0, seed=1)[0]) == 1
    with pytest.raises(ValueError):
        ex.split_apps(["a"], 1.5, seed=1)


def test_ood_apps_holds_out_an_entire_app_leaving_none_of_it_in_train(tmp_path):
    for name in ("com.a", "com.b"):
        write_session(tmp_path / "raw", name, [triple(0), triple(1)])
    stats, out = run_export(tmp_path, ood_apps=0.5, id_ratio=0.0)

    assert len(stats.ood_apps) == 1
    held = stats.ood_apps[0]
    ood_images = {r["images"][0] for r in records(out, "test_ood")}
    train_images = {r["images"][0] for r in records(out, "train")}
    assert len(ood_images) == 2 and len(train_images) == 2
    assert all(held in name for name in ood_images)
    assert all(held not in name for name in train_images), "not one triple may leak"


def test_id_ratio_samples_each_seen_app_independently(tmp_path):
    """Drawn per app, so ID eval cannot pile up on whichever app the shuffle picked."""
    for name in ("com.a", "com.b"):
        write_session(tmp_path / "raw", name, [triple(step) for step in range(4)])
    _, out = run_export(tmp_path, ood_apps=0.0, id_ratio=0.25)

    id_apps = [r["images"][0] for r in records(out, "test_id")]
    assert len(id_apps) == 2, "one of each app's four triples"
    assert sum("com.a" in name for name in id_apps) == 1
    assert sum("com.b" in name for name in id_apps) == 1
    assert len(records(out, "train")) == 6


def test_the_two_knobs_are_independent(tmp_path):
    """A held-out app contributes to OOD only — never to the ID sample."""
    for name in ("com.a", "com.b"):
        write_session(tmp_path / "raw", name, [triple(step) for step in range(4)])
    stats, out = run_export(tmp_path, ood_apps=0.5, id_ratio=0.25)

    held = stats.ood_apps[0]
    assert all(held not in r["images"][0] for r in records(out, "test_id"))
    assert len(records(out, "test_ood")) == 4
    assert len(records(out, "test_id")) == 1


# ---------------------------------------------------------------------------
# 8. discovery / plumbing
# ---------------------------------------------------------------------------


def test_sessions_lists_only_directories_holding_triples(tmp_path):
    write_session(tmp_path / "raw", "com.a", [triple(0)])
    (tmp_path / "raw" / "com.empty").mkdir(parents=True)
    exporter = ex.Exporter(tmp_path / "raw", tmp_path / "runtime", tmp_path)
    assert exporter.sessions() == ["com.a"]


def test_no_sessions_writes_nothing_and_reports_zero(tmp_path):
    stats, out = run_export(tmp_path)
    assert stats.total_written == 0
    assert not (out / "stage1_train.jsonl").exists()


def test_export_reads_the_triples_the_session_wrote(tmp_path):
    """The export's input is triples.jsonl (ARCHITECTURE §8), not graph.json."""
    write_session(tmp_path / "raw", PACKAGE, [triple(0)])
    session = Session(PACKAGE, tmp_path / "raw", tmp_path / "runtime", episode=PACKAGE)
    assert [t.step for t in session.read_triples()] == [0]
    assert session.device_size() == DEVICE
