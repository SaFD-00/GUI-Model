"""Export collected triples as EXP08 Stage-1 (NEXT_STATE_PREDICTION) jsonl.

This module's whole job is to satisfy a contract it does not own. The consumer
is ``Implicit-World-Modeling/scripts/build_exp08_data.py``, whose INPUT is
``data/AndroidControl/EXP08_stage1_state.jsonl``; every invariant below was read
off that file's 60,871 real records, not inferred from the builder's code.

THE RECORD
==========

    {"messages": [...], "images": ["myset/images/episode_{EP}_step_{STEP}.jpg"]}

Exactly two top-level keys. **No ``sample_id``** — the builder's downstream
(``build_wm_formats.py``) injects one as a positional index, and emitting our own
would collide with it. ``messages`` is three dicts keyed ``from``/``value``
(ShareGPT, not ``role``/``content``) in the order system, human, gpt:

    system  the verbatim literal in :mod:`atlas_collector._exp08_prompt`
    human   "Current UI State:\\n{XML}\\n\\n[Screenshot]\\n<image>\\n\\nAction:\\n<action>{JSON}</action>"
    gpt     the next state's bare html-like XML, no wrapper

The human turn is **XML-FIRST**. Stage 2 is image-first, and the builder relies
on this one's ordering literally: ``_copy_ratio_of_rec`` splits the human turn on
``"Current UI State:"`` and then on ``"[Screenshot]"`` to recover the current
XML. Emit stage-1 with stage-2's ordering and that split silently yields garbage,
so the Hungarian copy-ratio filter mis-scores every record rather than erroring.

TWO TRAPS THAT FAIL SILENTLY
============================

**Step padding.** Episode and step identity live ONLY in the image filename;
there is no episode or step field anywhere in the record. Episode is unpadded,
step is zero-padded to four digits. ``step_2`` still matches the builder's
regex, so nothing aborts — it resolves to a file that does not exist, the length
filter gets ``None`` back, and the record is dropped exactly as if it had merely
been too long. :func:`~atlas_collector.session.image_name` owns this format.

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
    --id-ratio  fraction of the SEEN apps' triples reserved. ID eval measures
                generalisation to unseen screens of a known app.

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

from atlas_collector._exp08_prompt import SYSTEM_PROMPT
from atlas_collector.session import DUMP_NAME, SCREENSHOT_NAME, Session, image_name
from atlas_collector.xml import parse_device_xml

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

#: Directory prefix the builder's image regex expects. It is remapped to a real
#: path by ``build_exp08_data.remap_image``; the literal must match.
IMAGE_PREFIX = "myset/images"

#: JPEG quality for the exported frames. The corpus is .jpg; our capture is .png.
JPEG_QUALITY = 90

#: Output basenames. ``stage1_train`` / ``stage1_test_*`` mirrors the shape of
#: ``data/AndroidControl_EXP08/``, which is the contract this export targets.
#: The ID/OOD split is the part EXP08 could not produce — its own meta records
#: that "원본에 앱 파티션 메타가 없어 앱 단위 분할을 재현할 수 없다".
SPLIT_TRAIN = "train"
SPLIT_ID = "test_id"
SPLIT_OOD = "test_ood"


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
    ood_apps: list[str] = field(default_factory=list)

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
            "ood_apps": sorted(self.ood_apps),
        }


def build_record(
    *,
    before_xml: str,
    after_xml: str,
    action: dict[str, Any],
    image_path: str,
) -> dict[str, Any]:
    """One EXP08 stage-1 record. See the module docstring for every invariant."""
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


def encode_screen(raw_xml: str, width: int, height: int) -> str:
    """Raw uiautomator dump -> html-like XML in the export frame.

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
    return parse_device_xml(pruned, width, height, strict_frame=False).rstrip()


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
        frame: tuple[int, int] = (840, 1876),
        device_size: tuple[int, int] = (1080, 2400),
        ood_apps: float = 0.3,
        id_ratio: float = 0.1,
        seed: int = 8,
        keep_unchanged: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.runtime_dir = Path(runtime_dir)
        self.out_dir = Path(out_dir)
        self.frame = frame
        self.device_size = device_size
        self.ood_apps = ood_apps
        self.id_ratio = id_ratio
        self.seed = seed
        #: Whether triples where the screen did not change are exported. Off by
        #: default: they are legitimate observations but a corpus dominated by
        #: "nothing happened" teaches the model to predict its own input.
        self.keep_unchanged = keep_unchanged
        self.stats = ExportStats()

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
            "{} app(s): {} seen, {} held out for OOD", len(packages), len(seen), len(held_out)
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
                    "frame": list(self.frame),
                    "device_size": list(self.device_size),
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

        # Prefer the resolution the session was actually collected at. Falling
        # back to the configured one is a guess, and a wrong guess rescales every
        # data-bbox silently, so say when it happens.
        device_size = session.device_size()
        if device_size is None:
            device_size = self.device_size
            logger.warning(
                "{} has no recorded device size; assuming {}x{} from config",
                package,
                *device_size,
            )

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
            record = self._build(session, triple, package, images_dir, device_size)
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
    ) -> dict[str, Any] | None:
        before_dir = session.observation_path(triple.before)
        after_dir = session.observation_path(triple.after)
        before_dump = before_dir / DUMP_NAME
        after_dump = after_dir / DUMP_NAME
        before_png = before_dir / SCREENSHOT_NAME
        if not (before_dump.is_file() and after_dump.is_file() and before_png.is_file()):
            self.stats.dropped_missing_files += 1
            return None

        try:
            before_xml = encode_screen(
                before_dump.read_text(encoding="utf-8"), *device_size
            )
            after_xml = encode_screen(
                after_dump.read_text(encoding="utf-8"), *device_size
            )
        except Exception as error:  # noqa: BLE001 - one bad dump must not stop the export
            logger.warning("{} step {}: could not encode ({})", package, triple.step, error)
            self.stats.dropped_unparsable += 1
            return None

        name = image_name(package, triple.step)
        write_jpeg(before_png.read_bytes(), images_dir / name, self.frame)
        return build_record(
            before_xml=before_xml,
            after_xml=after_xml,
            action=triple.action,
            image_path=f"{IMAGE_PREFIX}/{name}",
        )


__all__ = [
    "IMAGE_PREFIX",
    "JPEG_QUALITY",
    "SPLIT_ID",
    "SPLIT_OOD",
    "SPLIT_TRAIN",
    "ExportStats",
    "Exporter",
    "build_record",
    "encode_screen",
    "prune_inverted_nodes",
    "split_apps",
    "write_jpeg",
]
