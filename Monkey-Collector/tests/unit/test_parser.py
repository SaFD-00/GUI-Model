"""Prove the vendored parser emits the EXP08 dialect from REAL device dumps.

Ported from ``Atlas-Collector/tests/test_parser.py`` verbatim, save for the
``atlas_collector`` -> ``monkey_collector`` import and the fixture/cwd path
fixes needed because this file lives one directory deeper (``tests/unit/``
instead of ``tests/``).

Every fixture here is a live ``uiautomator dump`` off the target Pixel 6
(serial 19101FDF6004EH, oriole, Android 16 / SDK 36, ``wm size`` 1080x2400).
No CONTRACT assertion here runs on a synthesised dump — if a fixture goes missing
these tests must fail loudly rather than skip, because a green suite over a made-up
dump would prove nothing about the export contract. The only hand-written XML in
this file is the tiny degenerate-input probes at the bottom (empty, nodeless,
malformed, missing/garbage bounds), which exist precisely because no real device
produces them.
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from loguru import logger

from monkey_collector.xml import (
    EmptyUiDumpError,
    Parser,
    StructuredXmlParser,
    parse_device_xml,
    parse_device_xml_absolute,
    smart_resize_dims,
    source_frame,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Physical device geometry, measured live (`wm size`), and the export frame it
# resolves to. These four numbers ARE the contract.
DEVICE_W, DEVICE_H = 1080, 2400
FRAME_W, FRAME_H = 840, 1876

LOCKSCREEN = "pixel6_lockscreen_1080x2400.xml"
KEEP = "pixel6_keep_browse_1080x2400.xml"
ALL_FIXTURES = [LOCKSCREEN, KEEP]

_BBOX_RE = re.compile(r'data-bbox="([^"]*)"')
# Non-negative on purpose. The old pattern (r"^-?\d+ ...") admitted NEGATIVE
# coordinates, so the whole suite would have gone green on values that violate
# frame containment before a single assertion looked at magnitude.
_FOUR_INTS_RE = re.compile(r"^\d+ \d+ \d+ \d+$")


def load(name: str) -> str:
    path = FIXTURES / name
    assert path.is_file(), f"missing real-device fixture {path} — do NOT substitute a synthetic dump"
    return path.read_text(encoding="utf-8")


def bboxes(encoded: str) -> list[str]:
    return _BBOX_RE.findall(encoded)


def bbox_ints(encoded: str) -> list[tuple[int, int, int, int]]:
    out = []
    for raw in bboxes(encoded):
        x1, y1, x2, y2 = (int(v) for v in raw.split())
        out.append((x1, y1, x2, y2))
    return out


def export(name: str) -> str:
    return parse_device_xml(load(name), width=DEVICE_W, height=DEVICE_H)


# --------------------------------------------------------------------------
# the resize contract
# --------------------------------------------------------------------------


def test_smart_resize_dims_pixel6():
    """840x1876 is exactly smart_resize_dims(2400, 1080). Note the (height, width) order."""
    assert smart_resize_dims(DEVICE_H, DEVICE_W) == (FRAME_H, FRAME_W)


def test_smart_resize_dims_second_resolution():
    """A second, independent resolution pins the resize FUNCTION, not just one answer.

    1080x1920 (portrait FHD) -> 924x1680 under the same 1605632-pixel budget. Both
    dims stay multiples of 28 and 924*1680 = 1552320 <= 1605632. Derived by running
    the function, then frozen: any change to the budget, the 28-pixel factor or the
    floor/round arithmetic moves these numbers.
    """
    assert smart_resize_dims(1920, 1080) == (1680, 924)
    assert smart_resize_dims(1080, 1920) == (924, 1680)


def test_smart_resize_dims_axis_order_is_not_symmetric():
    """Guard the transpose trap: the swapped call returns the transpose, not the same answer."""
    assert smart_resize_dims(DEVICE_W, DEVICE_H) == (FRAME_W, FRAME_H)
    assert smart_resize_dims(DEVICE_W, DEVICE_H) != smart_resize_dims(DEVICE_H, DEVICE_W)


def test_resized_mode_requires_dimensions():
    """"resized" without width/height must raise, never silently fall back to device px."""
    raw = load(KEEP)
    with pytest.raises(ValueError):
        StructuredXmlParser().parse(raw, coord_mode="resized")


# --------------------------------------------------------------------------
# the EXP08 dialect
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_emits_data_bbox_and_no_legacy_attrs(name):
    """data-bbox is the coordinate carrier; bounds= and index= must be gone entirely."""
    encoded = export(name)
    assert "data-bbox" in encoded
    assert 'bounds="' not in encoded
    assert 'index="' not in encoded


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_data_bbox_is_four_space_separated_ints(name):
    encoded = export(name)
    found = bboxes(encoded)
    assert found, f"{name} produced no data-bbox at all"
    for raw in found:
        assert _FOUR_INTS_RE.match(raw), f"malformed data-bbox {raw!r} in {name}"
        assert "," not in raw, f"comma-separated data-bbox {raw!r} in {name}"
        # Shape alone is not enough: "9999 9999 9999 9999" is four ints too. Pin the
        # magnitude here as well so this test cannot be green on out-of-frame values.
        x1, y1, x2, y2 = (int(v) for v in raw.split())
        assert max(x1, x2) <= FRAME_W, f"x beyond {FRAME_W} in data-bbox {raw!r} in {name}"
        assert max(y1, y2) <= FRAME_H, f"y beyond {FRAME_H} in data-bbox {raw!r} in {name}"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_coordinates_lie_inside_the_export_frame(name):
    """x within 0..840, y within 0..1876 — kept as DISTINCT bounds on purpose.

    A single combined max would pass even if the x/y scales were transposed.
    """
    boxes = bbox_ints(export(name))
    assert boxes, f"{name} produced no data-bbox — this guard would pass vacuously"
    for x1, y1, x2, y2 in boxes:
        assert 0 <= x1 <= FRAME_W, f"x1={x1} outside 0..{FRAME_W} in {name}"
        assert 0 <= x2 <= FRAME_W, f"x2={x2} outside 0..{FRAME_W} in {name}"
        assert 0 <= y1 <= FRAME_H, f"y1={y1} outside 0..{FRAME_H} in {name}"
        assert 0 <= y2 <= FRAME_H, f"y2={y2} outside 0..{FRAME_H} in {name}"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_boxes_are_well_ordered(name):
    boxes = bbox_ints(export(name))
    assert boxes, f"{name} produced no data-bbox — this guard would pass vacuously"
    for x1, y1, x2, y2 in boxes:
        assert x1 <= x2, f"x1={x1} > x2={x2} in {name}"
        assert y1 <= y2, f"y1={y1} > y2={y2} in {name}"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_absolute_mode_really_differs_from_resized(name):
    """Device space overflows the export frame — proof the two modes are not the same call.

    The device is 1080x2400, so absolute coordinates must exceed the 840 x-bound.
    If this ever passes trivially, parse_device_xml is silently emitting device px.
    """
    absolute = parse_device_xml_absolute(load(name))
    flat = [v for box in bbox_ints(absolute) for v in box]
    assert flat, f"{name} produced no data-bbox in absolute mode"
    assert max(flat) > FRAME_W, "absolute mode produced no coordinate beyond the resized frame"
    assert absolute != export(name)


# --------------------------------------------------------------------------
# Pillow must not be on the export path
# --------------------------------------------------------------------------


def test_importing_the_package_does_not_import_pil():
    """A fresh interpreter must import monkey_collector.xml with PIL absent from sys.modules.

    Run out-of-process because this process has almost certainly imported PIL
    already (pytest collection, other tests), which would make an in-process
    assertion vacuous.
    """
    code = (
        "import sys; import monkey_collector.xml as m; "
        "assert m.smart_resize_dims(2400, 1080) == (1876, 840); "
        "print('PIL' in sys.modules)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 0, f"subprocess import failed: {proc.stderr}"
    assert proc.stdout.strip() == "False", f"PIL was imported: stdout={proc.stdout!r}"


def test_pillow_is_actually_installed():
    """Makes the laziness test meaningful: PIL is present, the parser just declines to load it."""
    proc = subprocess.run([sys.executable, "-c", "import PIL"], capture_output=True, text=True)
    assert proc.returncode == 0, "Pillow is not installed, so the laziness test proves nothing"


# --------------------------------------------------------------------------
# vendoring sanity + Keep regression guard
# --------------------------------------------------------------------------


def test_vendored_parser_satisfies_the_vendored_abc():
    """The import fix is a real import: the concrete parser IS a parser_base.Parser."""
    assert isinstance(StructuredXmlParser(), Parser)


def test_keep_fixture_regression():
    """Google Keep BrowseActivity, 41 raw nodes, single package com.google.android.keep."""
    encoded = export(KEEP)
    assert encoded.strip(), "Keep fixture produced empty output"
    root = ET.fromstring(encoded)  # raises on malformed output
    elements = list(root.iter())
    assert len(elements) >= 5, f"Keep fixture collapsed to {len(elements)} elements"
    assert all("data-bbox" in el.attrib for el in elements), "an element lost its data-bbox"


def test_lockscreen_fixture_regression():
    encoded = export(LOCKSCREEN)
    assert encoded.strip()
    elements = list(ET.fromstring(encoded).iter())
    assert len(elements) >= 5, f"lockscreen fixture collapsed to {len(elements)} elements"


# --------------------------------------------------------------------------
# GOLDEN VALUES — the coordinates themselves, pinned
#
# Every number below was produced by RUNNING the parser over the real fixture at
# 1080x2400 and then frozen as a literal; none of it is hand-computed. Its whole
# job is to fail when the transform's arithmetic moves. The suite that shipped
# before had 32 tests and NOT ONE pinned a coordinate value: fault injection into
# `_resize_and_add_point` that halved both scales, and a second injection that
# forced x_scale = y_scale = 0.0 (making every box "0 0 0 0"), both left 32/32
# green. These literals are the fix — any scale change moves them.
# --------------------------------------------------------------------------

KEEP_GOLDEN_BBOXES: list[tuple[int, int, int, int]] = [
    (0, 0, 840, 1876),
    (644, 1581, 807, 1745),
    (0, 0, 840, 1876),
    (215, 744, 625, 1083),
    (9, 100, 831, 232),
    (9, 117, 107, 216),
    (123, 109, 711, 224),
    (172, 141, 364, 190),
    (488, 109, 702, 224),
    (488, 116, 587, 215),
    (587, 116, 702, 215),
    (727, 117, 831, 216),
]
KEEP_GOLDEN_ELEMENT_COUNT = 12

LOCKSCREEN_GOLDEN_BBOXES: list[tuple[int, int, int, int]] = [
    (0, 0, 840, 1876),
    (0, 0, 840, 1876),
    (33, 412, 131, 510),
    (59, 412, 104, 510),
    (0, 0, 840, 1876),
    (0, 0, 840, 1876),
    (0, 0, 840, 100),
    (39, 0, 374, 100),
    (612, 27, 790, 73),
    (618, 27, 719, 73),
    (618, 27, 667, 73),
    (674, 38, 707, 63),
    (719, 37, 768, 63),
    (0, 0, 840, 1876),
    (0, 100, 840, 1876),
    (58, 137, 374, 371),
    (407, 205, 592, 303),
    (407, 209, 576, 250),
    (407, 250, 549, 299),
    (332, 1294, 508, 1471),
    (0, 1712, 840, 1810),
]
LOCKSCREEN_GOLDEN_ELEMENT_COUNT = 21


def test_keep_golden_bboxes_are_exact():
    """Every data-bbox of the Keep dump, in document order, pinned to the exact value."""
    assert bbox_ints(export(KEEP)) == KEEP_GOLDEN_BBOXES


def test_keep_golden_element_count():
    """12 elements, each carrying exactly one data-bbox — no element lost or duplicated."""
    encoded = export(KEEP)
    elements = list(ET.fromstring(encoded).iter())
    assert len(elements) == KEEP_GOLDEN_ELEMENT_COUNT
    assert len(bboxes(encoded)) == KEEP_GOLDEN_ELEMENT_COUNT


def test_keep_golden_root_box_spans_the_whole_frame():
    """The root node is [0,0][1080,2400] on the device, so it must land on the full frame.

    Stated separately from the list above because it is the one box whose value is
    derivable from the contract alone: it IS (0, 0, FRAME_W, FRAME_H). If the scales
    ever change, this fails with a message that reads as geometry, not as a diff.
    """
    assert bbox_ints(export(KEEP))[0] == (0, 0, FRAME_W, FRAME_H)


def test_lockscreen_golden_bboxes_are_exact():
    """Second real screen, same treatment — 21 boxes pinned in document order."""
    encoded = export(LOCKSCREEN)
    assert bbox_ints(encoded) == LOCKSCREEN_GOLDEN_BBOXES
    assert len(list(ET.fromstring(encoded).iter())) == LOCKSCREEN_GOLDEN_ELEMENT_COUNT


def test_golden_boxes_are_not_uniformly_scaled():
    """The goldens really do encode ANISOTROPIC scaling, so a single-factor bug fails.

    The device frame maps to (840, 1876). Applying the X scale to the Y axis would
    give 1867, and the Y scale to the X axis would give 844 — both wrong, both still
    inside the nominal frame. That is why the goldens above are pinned per axis.
    """
    x_scale = FRAME_W / DEVICE_W
    y_scale = FRAME_H / DEVICE_H
    assert x_scale != y_scale
    assert round(DEVICE_W * x_scale) == FRAME_W
    assert round(DEVICE_H * y_scale) == FRAME_H
    assert round(DEVICE_H * x_scale) == 1867 != FRAME_H
    assert round(DEVICE_W * y_scale) == 844 != FRAME_W
    # and the goldens really carry that asymmetry: the root box is the full frame
    assert KEEP_GOLDEN_BBOXES[0] == (0, 0, FRAME_W, FRAME_H)


# --------------------------------------------------------------------------
# the frame cross-check: wrong width/height must not encode silently
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_source_frame_is_derived_from_the_dump_itself(name):
    """Both real dumps report their own 1080x2400 frame, with no caller input."""
    assert source_frame(load(name)) == (DEVICE_W, DEVICE_H)


def test_mismatched_dimensions_raise_and_name_both_frames():
    """1440x3120 over a 1080x2400 dump used to encode silently. Now it raises.

    The old failure was invisible: it produced an 840x1848 frame (~25% too small)
    and every box stayed strictly inside the nominal 840x1876 bound, so the
    containment test above passed on wrong coordinates.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_device_xml(load(KEEP), width=1440, height=3120)
    message = str(excinfo.value)
    assert "1080x2400" in message, message
    assert "1440x3120" in message, message


def test_strict_frame_false_reproduces_the_old_silent_behaviour():
    """The opt-out is explicit and still wrong — pinned so the escape hatch stays honest.

    smart_resize_dims(3120, 1440) == (1848, 840), so the Keep root node scales to
    (0, 0, 630, 1422) instead of (0, 0, 840, 1876): ~25% small, and — this is the
    trap — entirely inside the nominal frame.
    """
    boxes = bbox_ints(parse_device_xml(load(KEEP), width=1440, height=3120, strict_frame=False))
    assert boxes[0] == (0, 0, 630, 1422)
    assert boxes != KEEP_GOLDEN_BBOXES
    assert all(x2 <= FRAME_W and y2 <= FRAME_H for _, _, x2, y2 in boxes), (
        "the wrong-frame boxes are INSIDE the export frame — that is why containment "
        "checks alone could never catch this"
    )


def test_matching_dimensions_still_encode_under_strict_frame():
    """The guard must not fire on the correct call — 1080x2400 goes through untouched."""
    assert bbox_ints(parse_device_xml(load(KEEP), width=DEVICE_W, height=DEVICE_H)) == (
        KEEP_GOLDEN_BBOXES
    )


# --------------------------------------------------------------------------
# degenerate input: empty, nodeless, malformed, unparseable bounds
#
# The only synthesised XML in this file. A real device never emits any of these,
# which is exactly why they have to be written by hand.
# --------------------------------------------------------------------------

_SYNTHETIC_ROOT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hierarchy rotation="0">'
    '<node index="0" text="" class="android.widget.FrameLayout" package="p" content-desc=""'
    ' checkable="false" checked="false" clickable="false" enabled="true" focusable="false"'
    ' focused="false" scrollable="false" long-clickable="false" password="false" selected="false"'
    ' bounds="[0,0][1080,2400]">{child}</node></hierarchy>'
)


def _synthetic_leaf(bounds_attr: str) -> str:
    return (
        '<node index="0" text="hello" class="android.widget.TextView" package="p"'
        ' content-desc="" checkable="false" checked="false" clickable="false" enabled="true"'
        ' focusable="false" focused="false" scrollable="false" long-clickable="false"'
        f' password="false" selected="false" {bounds_attr} />'
    )


#: A node with NO bounds attribute at all — the shape that really produces "0 0 0 0".
MISSING_BOUNDS_DUMP = _SYNTHETIC_ROOT.format(child=_synthetic_leaf(""))
#: A node whose bounds is present but unparseable.
GARBAGE_BOUNDS_DUMP = _SYNTHETIC_ROOT.format(child=_synthetic_leaf('bounds="garbage"'))

NODELESS_DUMP = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hierarchy rotation="0" />'
MALFORMED_DUMP = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hierarchy><node'


@contextmanager
def captured_warnings() -> Iterator[list[str]]:
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


@pytest.mark.parametrize(
    ("label", "raw"),
    [("empty", ""), ("whitespace", "   \n\t  "), ("nodeless", NODELESS_DUMP)],
)
def test_nodeless_input_raises_a_typed_error_that_says_so(label, raw):
    """Was: AttributeError: 'NoneType' object has no attribute 'iter', from inside CPython.

    That message named neither XML nor the dump. All three empty shapes now raise
    EmptyUiDumpError (a ValueError) whose text says the dump contained no nodes.
    """
    with pytest.raises(EmptyUiDumpError) as excinfo:
        parse_device_xml(raw, width=DEVICE_W, height=DEVICE_H)
    assert "no nodes" in str(excinfo.value), f"{label}: {excinfo.value}"


def test_malformed_xml_raises_a_message_naming_the_problem():
    """Not an EmptyUiDumpError — broken syntax is a different failure from an empty screen."""
    with pytest.raises(ValueError) as excinfo:
        parse_device_xml(MALFORMED_DUMP, width=DEVICE_W, height=DEVICE_H)
    assert not isinstance(excinfo.value, EmptyUiDumpError)
    message = str(excinfo.value)
    assert "malformed" in message and "XML" in message, message


def test_nodeless_input_also_guarded_in_absolute_mode():
    with pytest.raises(EmptyUiDumpError):
        parse_device_xml_absolute(NODELESS_DUMP)


def test_missing_bounds_warns_about_degenerate_boxes_in_both_modes():
    """A node with no bounds becomes data-bbox="0 0 0 0" silently. Now it is logged.

    A WARNING, deliberately not an error: a real screen can legitimately contain a
    zero-area node, and refusing to encode the screen would lose the whole step.
    """
    for mode, call in (
        ("resized", lambda: parse_device_xml(MISSING_BOUNDS_DUMP, width=DEVICE_W, height=DEVICE_H)),
        ("absolute", lambda: parse_device_xml_absolute(MISSING_BOUNDS_DUMP)),
    ):
        with captured_warnings() as messages:
            encoded = call()
        assert 'data-bbox="0 0 0 0"' in encoded, mode
        assert len(messages) == 1, f"{mode}: {messages}"
        assert "degenerate" in messages[0], messages[0]
        assert messages[0].startswith("1 of 1"), f"count missing from warning: {messages[0]}"


def test_real_fixtures_emit_no_degenerate_warning():
    """The happy path stays silent, so the warning keeps meaning something."""
    with captured_warnings() as messages:
        export(KEEP)
        export(LOCKSCREEN)
        parse_device_xml_absolute(load(KEEP))
    assert messages == [], messages


def test_unparseable_bounds_raise_a_message_that_names_bounds():
    """CORRECTION to the audit note: bounds="garbage" does NOT become "0 0 0 0".

    It reaches int() inside the frozen transform and dies as `invalid literal for
    int() with base 10: 'garbage'`. Only an ABSENT bounds attribute hits the
    '[0,0][0,0]' default (see the test above). The transform is untouched; the
    wrapper re-raises chained with a message that at least names bounds.
    """
    for call in (
        lambda: parse_device_xml(GARBAGE_BOUNDS_DUMP, width=DEVICE_W, height=DEVICE_H),
        lambda: parse_device_xml_absolute(GARBAGE_BOUNDS_DUMP),
    ):
        with pytest.raises(ValueError) as excinfo:
            call()
        message = str(excinfo.value)
        assert "bounds" in message, message
        assert "garbage" in message, message
