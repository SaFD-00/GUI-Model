"""Export collected triples as EXP08 Stage-1 (NEXT_STATE_PREDICTION) jsonl.

This module's whole job is to satisfy a contract it does not own. The consumer
is ``Implicit-World-Modeling/scripts/build_exp08_data.py``, whose INPUT is
``data/AndroidControl/EXP08_stage1_state.jsonl``; every invariant below was read
off that file's 60,871 real records, not inferred from the builder's code.
ARCHITECTURE §8 is the specification; AGENTS §2(d) is the short form.

THE RECORD
==========

    {"messages": [...], "images": ["myset/images/episode_{EP}_step_{STEP}.jpg"]}

Exactly two top-level keys. **No ``sample_id``** — the builder's downstream
(``build_wm_formats.py``) injects one as a positional index, and emitting our own
would collide with it. ``messages`` is three dicts keyed ``from``/``value``
(ShareGPT, not ``role``/``content``) in the order system, human, gpt:

    system  the verbatim literal in :mod:`monkey_collector._exp08_prompt`
    human   "Current UI State:\\n{XML}\\n\\n[Screenshot]\\n<image>\\n\\nAction:\\n<action>{JSON}</action>"
    gpt     the next state's bare html-like XML, no wrapper

The human turn is **XML-FIRST**. Stage 2 is image-first, and the builder relies
on this one's ordering literally: ``_copy_ratio_of_rec`` splits the human turn on
``"Current UI State:"`` and then on ``"[Screenshot]"`` to recover the current
XML. Emit stage-1 with stage-2's ordering and that split silently yields garbage,
so the Hungarian copy-ratio filter mis-scores every record rather than erroring.

TWO LAYERS OF ACTION VOCABULARY, TRANSLATED HERE AND ONLY HERE
==============================================================

``domain/actions.py``'s seven types are a fixed internal contract (AGENTS §2(a)):
``tap``/``swipe``/``input_text``/``press_back``/``press_home``/``long_press``/
``open_app``, each serialized with its own fields. EXP08 speaks a different
vocabulary, so :func:`translate_action` maps one onto the other. The collector
records the DOMAIN form on purpose — writing the wire form at collection time
would bake in a coordinate frame the collector cannot undo.

Two fields are deliberately dropped: ``duration_ms`` (``swipe`` / ``long_press``)
has no place in the EXP08 schema, and ``input_text``'s ``x``/``y`` must not
become a ``coordinate`` — the corpus's ``type`` payload is ``{"action", "text"}``
and nothing else. ``wait`` and ``terminate`` exist in the prompt's action space
but never appear in a Stage-1 human turn, so they are never emitted. An
``action_type`` outside the seven is COUNTED and dropped, never passed through:
a payload the corpus has no name for is worse than a missing record.

COORDINATES: THE PART ATLAS GETS WRONG
======================================

``coordinate`` / ``coordinate1`` / ``coordinate2`` live in the SAME resized frame
as ``data-bbox``, not in device pixels. Measured on ubuntu1.fclab 2026-08-29:
``EXP08_stage1_state.jsonl`` has 0 of 20,000 records outside the frame.

``Atlas-Collector`` writes ``adb.tap``'s device pixels straight through — its
``raw/org.tasks/triples.jsonl`` peaks at 1027x2279 and 67 of 657 coordinates fall
outside the frame. The other 590 are *inside* it while pointing at the wrong
place, disagreeing with the ``data-bbox`` of the very element that was tapped.
That is the silent half, and it is why :func:`translate_action` reproduces the
parser's arithmetic exactly (``structured_parser._resize_and_add_point``):

    new_h, new_w = smart_resize_dims(device_height, device_width, max_pixels)
    x_scale, y_scale = new_w / device_width, new_h / device_height
    rx, ry = round(x * x_scale), round(y * y_scale)

``round``, not ``int``: truncation would offset every action by up to a pixel
relative to the boxes it is supposed to land in. The scale is ANISOTROPIC (for
1080x2400: 0.77778 and 0.78167) — a single factor is wrong.

THE FRAME IS DERIVED PER OBSERVATION, NEVER CONFIGURED
=====================================================

Three things must agree or the record contradicts itself: the ``data-bbox``
scale, the action coordinate scale, and the JPEG's pixel size. So all three come
from ONE :func:`resized_frame` call -- fed by the dimensions of THAT
observation's own screenshot (:func:`observation_size`).

Not the session's ``device_width`` / ``device_height``, which is what this used
to do and what the first real-device pilot broke. ``Session.device_size`` comes
from ``wm size``, which reports the PHYSICAL display (1080x2400 on a Pixel 6)
and does not change when the display rotates. The dump and the screenshot are
properties of the CURRENT display and do change. In the pilot something set
``user_rotation=1`` mid-session, and 23 of 77 observations came back 2400x1080
while the session still said 1080x2400. Every one of them was rescaled by the
portrait factors and its screenshot squashed into an 840x1876 canvas: the JPEG,
the boxes and the action each landed in a different space, with a ``data-bbox``
of ``100 58 1867 795`` sitting in a frame 840 wide. Reading the frame off the
screenshot makes that case correct by construction rather than detected.

``before`` and ``after`` get their frames INDEPENDENTLY, because the action can
be what rotated the screen: the prompt's XML belongs to the screen the action
was taken on, and the target XML belongs to the screen it produced. The action
coordinates and the JPEG both follow ``before`` -- that is the screen the tap
happened on.

An observation whose screenshot cannot be measured falls back to the session's
recorded size, and then to the configured device size, each with a warning: a
wrong guess rescales everything silently.

``export.target_size`` is therefore an ASSERTION about the contract
(840x1876 = ``smart_resize_dims(2400, 1080)``), not the frame's source: when a
derived frame disagrees with it, that is counted in ``export_meta.json``'s
``observation_frames`` rather than being used to override the arithmetic. A
landscape record at 1876x840 is a valid Qwen-VL input; the reference corpus
being portrait-only is a property of that corpus, not a constraint on this one.

TWO TRAPS THAT FAIL SILENTLY
============================

**Step padding.** Episode and step identity live ONLY in the image filename;
there is no episode or step field anywhere in the record. Episode is unpadded,
step is zero-padded to four digits. ``step_2`` still matches the builder's
regex, so nothing aborts — it resolves to a file that does not exist, the length
filter gets ``None`` back, and the record is dropped exactly as if it had merely
been too long. :func:`~monkey_collector.session.image_name` owns this format and
this module must not reimplement it.

**The trailing newline.** The parser's ``pretty_xml`` ends with ``lstrip()``,
never ``rstrip()``, so its output carries a trailing ``\\n``. Every one of 3,000
sampled EXP08 ``gpt`` values ends at ``>``. Whatever built the corpus stripped it
downstream; we strip it here, because a gpt target that ends in whitespace the
corpus never contains teaches the model to emit it.

ID AND OOD ARE TWO INDEPENDENT KNOBS
====================================

EXP08 itself has no ID/OOD split — its meta says so outright: "원본에 앱 파티션
메타가 없어 앱 단위 분할을 재현할 수 없다". Having collected per-app, we can, and
the two axes answer different questions:

    --ood-apps  fraction of APPS held out entirely. Their triples never appear in
                train, so OOD eval measures generalisation to an unseen app.
    --id-ratio  fraction of the SEEN apps' triples reserved, drawn per app. ID
                eval measures generalisation to unseen screens of a known app.

They are deliberately separate because collapsing them into one number makes it
impossible to tell which kind of generalisation moved.
"""

from __future__ import annotations

import io
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from loguru import logger

from monkey_collector._exp08_prompt import SYSTEM_PROMPT
from monkey_collector.pagematch import dominant_package
from monkey_collector.session import DUMP_NAME, SCREENSHOT_NAME, Session, image_name
from monkey_collector.xml import DEFAULT_MAX_PIXELS, parse_device_xml, smart_resize_dims

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

#: Directory prefix the builder's image regex expects. It is remapped to a real
#: path by ``build_exp08_data.remap_image``; the literal must match.
IMAGE_PREFIX = "myset/images"

#: JPEG quality for the exported frames. The corpus is .jpg; our capture is .png.
JPEG_QUALITY = 90

#: Output basenames. ``stage1_train`` / ``stage1_test_*`` mirrors the shape of
#: ``data/AndroidControl_EXP08/``, which is the contract this export targets.
SPLIT_TRAIN = "train"
SPLIT_ID = "test_id"
SPLIT_OOD = "test_ood"

#: ``domain/actions.py`` type -> EXP08 wire name (AGENTS §2(a), ARCHITECTURE §9).
#: The internal vocabulary is frozen, so this table is the ONLY place the two
#: layers meet. ``wait`` / ``terminate`` are in the prompt's action space but
#: never occur in a Stage-1 human turn, so nothing maps onto them.
WIRE_NAMES: dict[str, str] = {
    "tap": "click",
    "long_press": "long_press",
    "input_text": "type",
    "swipe": "swipe",
    "press_back": "navigate_back",
    "press_home": "navigate_home",
    "open_app": "open",
}

#: The payload keys that carry a point, in the corpus's own spelling.
COORDINATE_KEYS = ("coordinate", "coordinate1", "coordinate2")


@dataclass
class ExportStats:
    """What was written, and what was dropped and why.

    Drops are counted per reason rather than summed: "3,000 records skipped" is
    not actionable, while "2,900 unchanged, 100 unparsable" tells you whether the
    corpus is healthy or the parser is broken.
    """

    apps: int = 0
    triples_seen: int = 0
    written: dict[str, int] = field(default_factory=dict)
    dropped_unchanged: int = 0
    dropped_unparsable: int = 0
    dropped_missing_files: int = 0
    #: Records whose screen belonged to a DIFFERENT app than the session's.
    dropped_foreign: int = 0
    #: Records whose image name was already claimed by an earlier record.
    dropped_duplicate_step: int = 0
    #: Records whose ``action_type`` is not one of the seven domain actions.
    dropped_unknown_action: int = 0
    ood_apps: list[str] = field(default_factory=list)
    #: ``package -> [device_w, device_h, frame_w, frame_h]``. Written out so a
    #: reader can check the coordinate frame of a shipped corpus without
    #: re-deriving it from the session metadata.
    frames: dict[str, list[int]] = field(default_factory=dict)
    #: Apps whose session recorded no device size, so the config's was assumed.
    assumed_device_size: list[str] = field(default_factory=list)
    #: How many exported records each derived frame produced, keyed
    #: ``"1080x2400->840x1876"``. A corpus that is not one frame throughout says
    #: so here rather than in nobody's head: the pilot that motivated
    #: per-observation frames was 30% landscape and looked uniform.
    observation_frames: dict[str, int] = field(default_factory=dict)
    #: Observations whose screenshot could not be measured and fell back to the
    #: session's recorded size. Every one of these is a rescale on a guess.
    unmeasured_observations: int = 0
    #: Exported points outside their own frame. The contract's headline number
    #: (0 of 20,000 in the canonical corpus); anything but 0 means the rescale
    #: or the recorded device size is wrong.
    coords_out_of_frame: int = 0

    @property
    def total_written(self) -> int:
        return sum(self.written.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "apps": self.apps,
            "triples_seen": self.triples_seen,
            "written": dict(self.written),
            "total_written": self.total_written,
            "dropped_unchanged": self.dropped_unchanged,
            "dropped_unparsable": self.dropped_unparsable,
            "dropped_missing_files": self.dropped_missing_files,
            "dropped_foreign": self.dropped_foreign,
            "dropped_duplicate_step": self.dropped_duplicate_step,
            "dropped_unknown_action": self.dropped_unknown_action,
            "ood_apps": sorted(self.ood_apps),
            "frames": dict(self.frames),
            "assumed_device_size": sorted(self.assumed_device_size),
            "observation_frames": dict(sorted(self.observation_frames.items())),
            "unmeasured_observations": self.unmeasured_observations,
            "coords_out_of_frame": self.coords_out_of_frame,
        }


def resized_frame(
    device_width: int, device_height: int, max_pixels: int = DEFAULT_MAX_PIXELS
) -> tuple[int, int]:
    """``(width, height)`` of the export frame for a device of that size.

    The single derivation point for every frame in this module, so the boxes,
    the action coordinates and the JPEG cannot end up in three different spaces.

    ``smart_resize_dims`` takes ``(height, width)`` and returns ``(height,
    width)``; both are passed and unpacked by name here because a transposed
    call returns ``(840, 1876)`` where ``(1876, 840)`` was meant and still looks
    like a plausible answer.
    """
    new_height, new_width = smart_resize_dims(
        height=device_height, width=device_width, max_pixels=max_pixels
    )
    return new_width, new_height


def observation_size(
    screenshot: Path, fallback: tuple[int, int]
) -> tuple[tuple[int, int], bool]:
    """``((width, height), measured)`` for one observation's screen.

    The screenshot is the authority, not ``wm size``: see the module docstring.
    ``measured`` is False when the file is missing or unreadable and *fallback*
    was used, so the caller can count how much of an export rests on a guess.

    Only the PNG header is touched -- ``Image.open`` is lazy and ``.size`` does
    not decode the pixels, so this costs one small read per observation rather
    than a full decode.
    """
    try:
        from PIL import Image

        with Image.open(screenshot) as image:
            width, height = image.size
        # A size that cannot produce a frame is not a measurement. `smart_resize_dims`
        # aligns to a 28px block and returns (0, 0) below it, which would reach
        # `Image.resize` as "height and width must be > 0" -- one truncated PNG
        # would end the whole export instead of costing one fallback.
        if width > 0 and height > 0 and all(resized_frame(int(width), int(height))):
            return (int(width), int(height)), True
        logger.warning(
            "{}: {}x{} does not resize to a usable frame; falling back to {}x{}",
            screenshot,
            width,
            height,
            fallback[0],
            fallback[1],
        )
    except Exception as error:  # noqa: BLE001 - a guess is better than no record
        logger.debug("could not measure {} ({})", screenshot, error)
    return fallback, False


def translate_action(
    action: dict[str, Any],
    *,
    device_size: tuple[int, int],
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> dict[str, Any] | None:
    """A ``domain/actions.py`` record -> the EXP08 wire payload, or None.

    None means "no EXP08 name for this ``action_type``" — the caller counts it
    and drops the record. Returning something plausible instead would put a
    payload in the corpus that the schema has no name for.

    Coordinates are rescaled into the export frame here, which is the whole
    reason this function exists rather than the triple's ``action`` dict being
    passed through. See the module docstring for the measured consequence of
    not doing it.
    """
    kind = str(action.get("action_type", ""))
    name = WIRE_NAMES.get(kind)
    if name is None:
        return None

    device_width, device_height = device_size
    frame_width, frame_height = resized_frame(device_width, device_height, max_pixels)
    x_scale = frame_width / device_width
    y_scale = frame_height / device_height

    def point(x: Any, y: Any) -> list[int]:
        # `round`, matching the parser's `_resize_and_add_point` exactly: `int`
        # would truncate an action up to a pixel away from the box it belongs in.
        return [round(float(x) * x_scale), round(float(y) * y_scale)]

    if kind in ("tap", "long_press"):
        # `duration_ms` is intentionally absent: EXP08's long_press has no such field.
        return {"action": name, "coordinate": point(action.get("x", 0), action.get("y", 0))}
    if kind == "swipe":
        return {
            "action": name,
            "coordinate1": point(action.get("x1", 0), action.get("y1", 0)),
            "coordinate2": point(action.get("x2", 0), action.get("y2", 0)),
        }
    if kind == "input_text":
        # The domain action carries the x/y it tapped to focus the field. EXP08's
        # `type` payload is {"action", "text"} and nothing else — a `coordinate`
        # here would be a key the corpus never contains.
        return {"action": name, "text": str(action.get("text", ""))}
    if kind == "open_app":
        # `app_name` is what EXP08 names; the package is the only other identity
        # the domain action carries, so it stands in when the name is missing
        # rather than shipping `"app_name": ""`.
        app_name = str(action.get("app_name") or action.get("package") or "")
        return {"action": name, "app_name": app_name}
    return {"action": name}


def out_of_frame_points(payload: dict[str, Any], frame: tuple[int, int]) -> int:
    """How many of a payload's points fall outside ``frame``.

    Counted, never clamped and never dropped: clamping invents geometry, and a
    coordinate outside the frame is a signal that the recorded device size or
    the rescale is wrong — which is exactly the failure this export exists to
    make visible.
    """
    width, height = frame
    outside = 0
    for key in COORDINATE_KEYS:
        pt = payload.get(key)
        if not isinstance(pt, list) or len(pt) != 2:
            continue
        x, y = pt
        if not (0 <= x <= width and 0 <= y <= height):
            outside += 1
    return outside


def build_record(
    *,
    before_xml: str,
    after_xml: str,
    action: dict[str, Any],
    image_path: str,
) -> dict[str, Any]:
    """One EXP08 stage-1 record. See the module docstring for every invariant.

    ``action`` is the WIRE payload from :func:`translate_action`, never a raw
    triple action.
    """
    human = (
        f"Current UI State:\n{before_xml}\n\n"
        f"[Screenshot]\n<image>\n\n"
        f"Action:\n<action>{json.dumps(action)}</action>"
    )
    return {
        "messages": [
            {"from": "system", "value": SYSTEM_PROMPT},
            {"from": "human", "value": human},
            {"from": "gpt", "value": after_xml},
        ],
        "images": [image_path],
    }


def prune_inverted_nodes(raw_xml: str) -> tuple[str, int]:
    """Drop nodes whose bounds are inverted, returning (xml, dropped_count).

    uiautomator emits them for content scrolled past the viewport: measured on
    the real device, a Settings summary line reports
    ``bounds="[221,2390][650,2337]"`` — a top BELOW its bottom, i.e. negative
    height. The parser passes them through faithfully, so they reach the export
    as ``data-bbox="172 1868 506 1827"``.

    The corpus does not contain them. Measured over 3,000 real EXP08 records:
    266,407 boxes, ZERO inverted and ZERO negative. Shipping them would put
    geometry in the training data that the model is never evaluated against and
    that no renderer can draw.

    Children are promoted into the parent rather than deleted with it. Every
    instance observed so far is a childless leaf, but silently dropping a
    subtree because its container was off-screen would remove real content — and
    it would do so invisibly, which is the failure mode this module is built to
    avoid. Clamping is deliberately NOT done: it invents geometry.
    """
    root = ET.fromstring(raw_xml)
    dropped = 0
    changed = True
    while changed:
        changed = False
        for parent in root.iter():
            for child in list(parent):
                match = _BOUNDS_RE.match(child.get("bounds") or "")
                if match is None:
                    continue
                left, top, right, bottom = (int(v) for v in match.groups())
                if left <= right and top <= bottom:
                    continue
                position = list(parent).index(child)
                parent.remove(child)
                for offset, grandchild in enumerate(list(child)):
                    parent.insert(position + offset, grandchild)
                dropped += 1
                changed = True
                break
            if changed:
                break
    if not dropped:
        return raw_xml, 0
    return ET.tostring(root, encoding="unicode"), dropped


def encode_screen(
    raw_xml: str, width: int, height: int, max_pixels: int = DEFAULT_MAX_PIXELS
) -> str:
    """Raw uiautomator dump -> html-like XML in the export frame.

    ``max_pixels`` is threaded rather than defaulted at the call site: the boxes
    this produces and the coordinates :func:`translate_action` produces must come
    out of the same ``smart_resize_dims`` budget, or they land in two frames that
    look equally plausible.

    ``strict_frame=False`` because the dimensions come from the SESSION's own
    recorded ``wm size``, not from a caller's guess. The guard exists to catch a
    mis-declared resolution, and it derives the dump's frame from the largest
    node — which is wrong for a partial window. Measured on a real corpus, a
    permission dialog spanning ``[28,822][1052,1642]`` tripped it, and 6 of 26
    triples (23%) were discarded as "frame mismatch" when nothing was mismatched.
    With an authoritative frame there is no guess left to guard.

    ``rstrip`` is not cosmetic: the parser ends with ``lstrip()`` and leaves a
    trailing newline the corpus never contains. See the module docstring.
    """
    pruned, dropped = prune_inverted_nodes(raw_xml)
    if dropped:
        logger.debug("dropped {} node(s) with inverted bounds", dropped)
    return parse_device_xml(pruned, width, height, max_pixels, strict_frame=False).rstrip()


def write_jpeg(png_bytes: bytes, destination: Path, size: tuple[int, int]) -> None:
    """Convert a captured PNG to the corpus's JPEG at the export frame size.

    Resized here rather than at capture time: collecting at device resolution
    keeps the corpus reusable when the model's pixel budget changes, and the
    frame is a property of the export contract, not of the screen.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="JPEG", quality=JPEG_QUALITY)


def split_apps(
    packages: list[str], ood_fraction: float, seed: int
) -> tuple[list[str], list[str]]:
    """Partition apps into (seen, held-out).

    Held out by APP, never by triple: a single triple of an app leaking into
    train makes "unseen app" false for the whole app, and nothing downstream can
    detect it. Sorted before shuffling so a given seed means the same split
    regardless of filesystem enumeration order.
    """
    if not 0.0 <= ood_fraction <= 1.0:
        raise ValueError(f"ood_apps must be in [0.0, 1.0], got {ood_fraction!r}")
    ordered = sorted(packages)
    rng = random.Random(seed)
    rng.shuffle(ordered)
    held_out = int(round(len(ordered) * ood_fraction))
    # An OOD fraction the user asked for must not silently become zero apps, and
    # must not consume every app either — both make the split meaningless.
    if ood_fraction > 0 and held_out == 0 and len(ordered) > 1:
        held_out = 1
    if held_out >= len(ordered):
        held_out = max(0, len(ordered) - 1)
    return sorted(ordered[held_out:]), sorted(ordered[:held_out])


class Exporter:
    """Turns collected sessions into EXP08 stage-1 jsonl plus its images."""

    def __init__(
        self,
        data_dir: str | Path,
        runtime_dir: str | Path,
        out_dir: str | Path,
        *,
        target_size: tuple[int, int] = (840, 1876),
        device_size: tuple[int, int] = (1080, 2400),
        max_pixels: int = DEFAULT_MAX_PIXELS,
        ood_apps: float = 0.3,
        id_ratio: float = 0.1,
        seed: int = 8,
        keep_unchanged: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.runtime_dir = Path(runtime_dir)
        self.out_dir = Path(out_dir)
        #: The frame the CONTRACT claims (``export.target_size``). Checked
        #: against the derived one per app, never used in its place — see the
        #: module docstring.
        self.target_size: tuple[int, int] = (target_size[0], target_size[1])
        #: Assumed only for a session that recorded no size of its own.
        self.device_size: tuple[int, int] = (device_size[0], device_size[1])
        #: The same visual-token budget the parser resizes boxes with. One value
        #: reaches both the box path and the coordinate path, by construction.
        self.max_pixels = max_pixels
        self.ood_apps = ood_apps
        self.id_ratio = id_ratio
        self.seed = seed
        #: Whether triples where the screen did not change are exported. Off by
        #: default: they are legitimate observations but a corpus dominated by
        #: "nothing happened" teaches the model to predict its own input.
        self.keep_unchanged = keep_unchanged
        self.stats = ExportStats()
        #: Image names already written, so a repeated step cannot overwrite one.
        self._image_names: set[str] = set()

    # -- discovery -----------------------------------------------------------

    def sessions(self) -> list[str]:
        """Packages with a collected session under ``data_dir``."""
        if not self.data_dir.is_dir():
            return []
        return sorted(
            path.name
            for path in self.data_dir.iterdir()
            if (path / "triples.jsonl").is_file()
        )

    # -- the export ----------------------------------------------------------

    def run(self) -> ExportStats:
        packages = self.sessions()
        if not packages:
            logger.warning("no collected sessions under {}", self.data_dir)
            return self.stats

        seen, held_out = split_apps(packages, self.ood_apps, self.seed)
        self.stats.apps = len(packages)
        self.stats.ood_apps = held_out
        logger.info(
            "{} app(s): {} seen, {} held out for OOD",
            len(packages),
            len(seen),
            len(held_out),
        )

        images_dir = self.out_dir / "images"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        handles = {
            split: (self.out_dir / f"stage1_{split}.jsonl").open("w", encoding="utf-8")
            for split in (SPLIT_TRAIN, SPLIT_ID, SPLIT_OOD)
        }
        try:
            for package in packages:
                is_ood = package in held_out
                self._export_app(package, is_ood, handles, images_dir)
        finally:
            for handle in handles.values():
                handle.close()

        (self.out_dir / "export_meta.json").write_text(
            json.dumps(
                {
                    **self.stats.as_dict(),
                    "target_size": list(self.target_size),
                    "fallback_device_size": list(self.device_size),
                    "max_pixels": self.max_pixels,
                    "ood_apps_fraction": self.ood_apps,
                    "id_ratio": self.id_ratio,
                    "seed": self.seed,
                    "keep_unchanged": self.keep_unchanged,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return self.stats

    def _frame_for(self, session: Session, package: str) -> tuple[tuple[int, int], tuple[int, int]]:
        """``(device_size, frame)`` for one app, both authoritative for it.

        Prefers the resolution the session was actually collected at. Falling
        back to the configured one is a guess, and a wrong guess rescales every
        data-bbox AND every action coordinate silently, so say when it happens.
        """
        recorded = session.device_size()
        if recorded is None:
            device_size = self.device_size
            self.stats.assumed_device_size.append(package)
            logger.warning(
                "{} has no recorded device size; assuming {}x{} from config",
                package,
                device_size[0],
                device_size[1],
            )
        else:
            device_size = recorded
        frame = resized_frame(device_size[0], device_size[1], self.max_pixels)
        if frame != self.target_size:
            # Not an error: the frame is DERIVED, and this export follows the
            # derivation. But a corpus whose frame differs from the contract's
            # 840x1876 must not be discovered by reading the pixels later.
            logger.warning(
                "{}: {}x{} resizes to a {}x{} frame, not the configured "
                "export.target_size {}x{}; boxes, action coordinates and the JPEG "
                "all follow the DERIVED frame",
                package,
                device_size[0],
                device_size[1],
                frame[0],
                frame[1],
                self.target_size[0],
                self.target_size[1],
            )
        self.stats.frames[package] = [*device_size, *frame]
        return device_size, frame

    def _export_app(
        self,
        package: str,
        is_ood: bool,
        handles: dict[str, Any],
        images_dir: Path,
    ) -> None:
        session = Session(package, self.data_dir, self.runtime_dir, episode=package)
        triples = session.read_triples()
        if not triples:
            return

        device_size, frame = self._frame_for(session, package)

        # The ID split is drawn per app, so every seen app contributes to ID eval
        # rather than a few apps supplying all of it — otherwise "unseen screens
        # of a known app" is measured on whichever apps the shuffle happened to
        # land on.
        rng = random.Random(f"{self.seed}:{package}")
        eligible = [t for t in triples if self.keep_unchanged or t.changed]
        id_indices: set[int] = set()
        if not is_ood and self.id_ratio > 0 and eligible:
            count = max(1, int(round(len(eligible) * self.id_ratio)))
            id_indices = set(rng.sample(range(len(eligible)), min(count, len(eligible))))

        position = 0
        for triple in triples:
            self.stats.triples_seen += 1
            if not (self.keep_unchanged or triple.changed):
                self.stats.dropped_unchanged += 1
                continue

            index = position
            position += 1
            record = self._build(session, triple, package, images_dir, device_size, frame)
            if record is None:
                continue

            if is_ood:
                split = SPLIT_OOD
            elif index in id_indices:
                split = SPLIT_ID
            else:
                split = SPLIT_TRAIN
            handles[split].write(json.dumps(record, ensure_ascii=False) + "\n")
            self.stats.written[split] = self.stats.written.get(split, 0) + 1

    def _build(
        self,
        session: Session,
        triple: Any,
        package: str,
        images_dir: Path,
        device_size: tuple[int, int],
        frame: tuple[int, int],
    ) -> dict[str, Any] | None:
        # Translated first: it is pure, and an action with no EXP08 name is a
        # property of the triple alone, decidable before touching the disk. The
        # frame it needs is this observation's, resolved just below.
        if translate_action(triple.action, device_size=device_size) is None:
            logger.warning(
                "{} step {}: no EXP08 name for action_type {!r}; dropped",
                package,
                triple.step,
                triple.action.get("action_type"),
            )
            self.stats.dropped_unknown_action += 1
            return None

        before_dir = session.observation_path(triple.before)
        after_dir = session.observation_path(triple.after)
        before_dump = before_dir / DUMP_NAME
        after_dump = after_dir / DUMP_NAME
        before_png = before_dir / SCREENSHOT_NAME
        if not (before_dump.is_file() and after_dump.is_file() and before_png.is_file()):
            self.stats.dropped_missing_files += 1
            return None

        # The frames come from the screenshots, not from the session: `wm size`
        # is the PHYSICAL display and does not rotate, the screenshot does. See
        # the module docstring for the 23-of-77 pilot this is answering.
        # `before` and `after` are resolved independently because the action can
        # be what rotated the screen.
        before_size, before_measured = observation_size(before_png, device_size)
        after_size, after_measured = observation_size(after_dir / SCREENSHOT_NAME, device_size)
        self.stats.unmeasured_observations += (not before_measured) + (not after_measured)
        # Only `before` needs an explicit frame here (the JPEG and the action
        # coordinates). `after` needs none: `encode_screen` takes the DEVICE
        # size and derives the frame inside the parser, out of the same
        # `smart_resize_dims` budget.
        before_frame = resized_frame(before_size[0], before_size[1], self.max_pixels)
        # The action was taken on `before`, so its coordinates and the JPEG both
        # live in that frame; `frame` (the session's) is only the fallback now.
        payload = translate_action(
            triple.action, device_size=before_size, max_pixels=self.max_pixels
        )
        assert payload is not None  # decided above, on the same action_type

        # A screen that belongs to ANOTHER app must not be exported as this
        # app's. The loop deliberately tolerates a few foreign frames because an
        # app's flow spans packages (a share sheet, an account picker), and it
        # persists them. That is right for a helper the app hands off to, and
        # wrong for a whole other app.
        #
        # The reason this is a hard filter and not a quality nicety is
        # `split_apps`: OOD is held out by APP, and its own docstring says a
        # single leaked triple makes "unseen app" false for the whole app with
        # nothing downstream able to detect it. An exported record carries no
        # package, so another target's screen inside this app's train split is
        # exactly that undetectable leak.
        before_raw = before_dump.read_text(encoding="utf-8")
        after_raw = after_dump.read_text(encoding="utf-8")
        # The unparsable guard spans the foreign check too, because a truncated
        # dump reaches `dominant_package` FIRST and raises there -- one bad file
        # would otherwise end the whole export instead of costing one record.
        try:
            for raw in (before_raw, after_raw):
                observed = dominant_package(raw)
                if observed and observed != package:
                    self.stats.dropped_foreign += 1
                    return None
            before_xml = encode_screen(
                before_raw, before_size[0], before_size[1], self.max_pixels
            )
            after_xml = encode_screen(
                after_raw, after_size[0], after_size[1], self.max_pixels
            )
        except Exception as error:  # noqa: BLE001 - one bad dump must not stop the export
            logger.warning(
                "{} step {}: could not read or encode the dump ({})",
                package,
                triple.step,
                error,
            )
            self.stats.dropped_unparsable += 1
            return None

        # Two records must never claim one image. `image_name` derives the file
        # from (package, step), so a repeated step silently overwrites the
        # earlier JPEG and leaves that record pointing at another screen. A
        # resume bug produced exactly that -- 22 duplicated steps in one
        # session -- and nothing downstream noticed, which is the reason this
        # check exists rather than a comment saying it cannot happen.
        name = image_name(package, triple.step)
        if name in self._image_names:
            logger.warning("{} step {}: duplicate image name {}", package, triple.step, name)
            self.stats.dropped_duplicate_step += 1
            return None
        self._image_names.add(name)
        write_jpeg(before_png.read_bytes(), images_dir / name, before_frame)
        # Counted here and not at translation time: the stat means "points in the
        # SHIPPED corpus", so a record that was dropped afterwards must not
        # inflate the one number that says whether the rescale is right.
        self.stats.coords_out_of_frame += out_of_frame_points(payload, before_frame)
        key = f"{before_size[0]}x{before_size[1]}->{before_frame[0]}x{before_frame[1]}"
        self.stats.observation_frames[key] = self.stats.observation_frames.get(key, 0) + 1
        return build_record(
            before_xml=before_xml,
            after_xml=after_xml,
            action=payload,
            image_path=f"{IMAGE_PREFIX}/{name}",
        )


__all__ = [
    "COORDINATE_KEYS",
    "IMAGE_PREFIX",
    "JPEG_QUALITY",
    "SPLIT_ID",
    "SPLIT_OOD",
    "SPLIT_TRAIN",
    "WIRE_NAMES",
    "ExportStats",
    "Exporter",
    "build_record",
    "encode_screen",
    "out_of_frame_points",
    "prune_inverted_nodes",
    "resized_frame",
    "split_apps",
    "translate_action",
    "write_jpeg",
]
