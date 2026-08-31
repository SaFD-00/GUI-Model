"""A READ-ONLY view of one collected app, as the export sees it.

WHY THIS EXISTS RATHER THAN A SECOND READER
===========================================

The reviewer is judging what the export will ship, so this module answers every
question with the export's own code: :func:`~monkey_collector.export.observation_size`
for the frame, :func:`~monkey_collector.export.encode_screen` for the html-like
XML, :func:`~monkey_collector.export.translate_action` for the action payload,
:func:`~monkey_collector.pagematch.dominant_package` for the foreign check. What
the human sees is byte-identical to what lands in ``stage1_*.jsonl``.

That is a deliberate refusal of the cheaper option. ``xml/__init__.py`` exports
:func:`source_frame`, which derives the frame from the dump itself and would let
this module skip reading the PNG -- but ``encode_screen``'s docstring records the
measurement that closes that door: a permission dialog spanning
``[28,822][1052,1642]`` makes the largest node a partial window, and 6 of 26
triples were discarded as "frame mismatch" when nothing was mismatched. The
export reads the frame off the SCREENSHOT and passes ``strict_frame=False``. So
does this. Two derivations would eventually disagree, and the tool would be
lying about the corpus it is supposed to be filtering.

READ-ONLY IS A HARD CONSTRAINT, NOT A PREFERENCE
================================================

A collection is normally still running while someone reviews the apps that
finished. Nothing here writes, moves or creates anything under ``raw/`` or
``runtime/``; the only writes this package makes are verdicts and caches under
``review/`` (:mod:`monkey_collector.review.store`). Concretely that means:

* ``triples.jsonl`` is read with a TOLERANT parser. ``Session.read_triples``
  calls ``json.loads`` on every line and dies on the half-written last line that
  an appending collector legitimately leaves behind.
* an observation directory can hold ``raw.xml`` with no ``screenshot.png`` yet;
* ``metadata.json`` for the app being collected right now is absent or stale, so
  nothing here may require it.

GROUPS: THE MEASUREMENT THAT SHAPED THIS TOOL
=============================================

Grouping is by the EXPORTED form -- ``(before html, translated action, after
html)`` -- not by the raw triple. ``element_index`` and ``duration_ms`` do not
survive ``translate_action``, so grouping on the raw action splits records that
are byte-identical in the shipped corpus and under-counts duplicates.

Measured on the 2026-08-30 sweep (10 apps, 5,679 exportable triples): 1,573 of
them (27.7%) are repeats of a triple already in the corpus. It is not spread
evenly -- ``code.name.monkey.retromusic`` has 411 exportable triples covering 42
distinct records, one of which repeats 369 times, and
``com.simplemobiletools.gallery.pro`` is 79.8% repeats. Those two are collection
failures wearing a corpus costume, which is why :attr:`AppSummary.distinct`
is surfaced next to the raw count rather than buried in a filter.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from monkey_collector.export import (
    encode_screen,
    observation_size,
    out_of_frame_points,
    resized_frame,
    translate_action,
)
from monkey_collector.pagematch import dominant_package
from monkey_collector.review.store import identity_key, write_json_atomic
from monkey_collector.session import DUMP_NAME, SCREENSHOT_NAME
from monkey_collector.xml import DEFAULT_MAX_PIXELS

#: A session whose ``triples.jsonl`` was touched this recently is treated as
#: still being collected. Generous on purpose: the loop can spend a minute on a
#: single recovery, and calling a live app "finished" is the error that matters
#: (it invites reviewing a tail that is still moving).
LIVE_WINDOW_SEC = 300.0

#: How many encoded screens stay in memory. Bounded because a screen holds both
#: the raw dump and its html-like encoding (~60KB together) and an app has up to
#: ~1,200 of them: an unbounded memo is ~70MB per app and the server holds
#: several apps. The memo only has to span CONSECUTIVE triples -- step N's
#: `after` is step N+1's `before` -- so a small window removes the double
#: encoding without holding the app.
SCREEN_MEMO = 16

#: Why the export would drop a triple. Mirrors ``ExportStats``' counters, so the
#: reviewer's "export 대상만" scope is the exporter's own decision rather than a
#: re-implementation that can drift from it.
DROP_UNCHANGED = "unchanged"
DROP_UNKNOWN_ACTION = "unknown_action"
DROP_MISSING_FILES = "missing_files"
DROP_FOREIGN = "foreign"
DROP_UNPARSABLE = "unparsable"
DROP_DUPLICATE_STEP = "duplicate_step"


@dataclass
class Screen:
    """One observation, encoded exactly as the export would encode it."""

    index: int
    exists: bool = False
    has_shot: bool = False
    size: tuple[int, int] = (0, 0)
    measured: bool = False
    html: str = ""
    raw: str = ""
    package: str = ""
    nodes: int = 0
    error: str = ""

    @property
    def frame(self) -> tuple[int, int]:
        if not all(self.size):
            return (0, 0)
        return resized_frame(self.size[0], self.size[1])


@dataclass
class StepRow:
    """One triple plus everything the reviewer needs to judge it."""

    step: int
    before: int
    after: int
    action: dict[str, Any]
    changed: bool
    page_changed: bool
    reason: str
    from_page: int
    to_page: int
    #: "" when the export would ship it; otherwise one of the DROP_* constants.
    drop: str = ""
    key: str = ""
    group: str = ""
    #: 0 for the first member of a group, 1.. for each repeat.
    rank: int = 0
    group_size: int = 1
    same_html: bool = False
    coord_out_of_frame: int = 0
    before_size: tuple[int, int] = (0, 0)
    after_size: tuple[int, int] = (0, 0)
    min_nodes: int = 0
    mark: dict[str, Any] = field(default_factory=dict)

    @property
    def exportable(self) -> bool:
        return not self.drop

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "before": self.before,
            "after": self.after,
            "action": self.action,
            "changed": self.changed,
            "page_changed": self.page_changed,
            "reason": self.reason,
            "from_page": self.from_page,
            "to_page": self.to_page,
            "drop": self.drop,
            "exportable": self.exportable,
            "key": self.key,
            "group": self.group,
            "rank": self.rank,
            "group_size": self.group_size,
            "same_html": self.same_html,
            "coord_out_of_frame": self.coord_out_of_frame,
            "before_size": list(self.before_size),
            "after_size": list(self.after_size),
            "min_nodes": self.min_nodes,
            "mark": self.mark,
        }


@dataclass
class AppSummary:
    """The app-card numbers. ``distinct`` is the one that exposes a stuck run."""

    package: str
    triples: int = 0
    exportable: int = 0
    distinct: int = 0
    redundant: int = 0
    largest_group: int = 0
    live: bool = False
    complete: bool = False
    device: tuple[int, int] = (0, 0)
    landscape_obs: int = 0
    stop_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "triples": self.triples,
            "exportable": self.exportable,
            "distinct": self.distinct,
            "redundant": self.redundant,
            "largest_group": self.largest_group,
            "live": self.live,
            "complete": self.complete,
            "device": list(self.device),
            "landscape_obs": self.landscape_obs,
            "stop_reason": self.stop_reason,
        }


def read_triples_tolerant(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Every complete line of a live ``triples.jsonl``, plus a skipped count.

    The collector appends while this runs, so the final line can be a prefix of
    a JSON object. Skipping it is correct and temporary -- it will be complete
    on the next read. Anything malformed EARLIER in the file is a real problem,
    but it is still only worth one record, never the whole app.
    """
    if not path.is_file():
        return [], 0
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(row, dict) and "step" in row:
            rows.append(row)
        else:
            skipped += 1
    return rows, skipped


def action_mark(action: dict[str, Any], size: tuple[int, int]) -> dict[str, Any]:
    """How to draw this action over the ``before`` screenshot.

    Seven action types, three shapes -- and two of them carry no coordinate at
    all, which is why this returns a KIND rather than a point. ``press_back`` /
    ``press_home`` / ``open_app`` drawn as a dot would put a target at (0,0),
    which reads as "it tapped the top-left corner". ``input_text`` has x/y but
    the export deliberately drops them, so it is drawn as a soft marker with the
    text, never as a tap target.

    Points are DEVICE pixels together with the frame they belong to; the page
    turns them into percentages, so one mark is correct on a thumbnail, on the
    full image and in the blink overlay with no arithmetic repeated in the UI.
    """
    kind = str(action.get("action_type") or "")
    width, height = size

    def point(x: Any, y: Any) -> list[float] | None:
        try:
            fx, fy = float(x), float(y)
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return [fx, fy]

    if kind in ("tap", "long_press"):
        spot = point(action.get("x"), action.get("y"))
        return {"kind": "point" if spot else "none", "points": [spot] if spot else [],
                "label": kind, "emphasis": "strong"}
    if kind == "swipe":
        start = point(action.get("x1"), action.get("y1"))
        end = point(action.get("x2"), action.get("y2"))
        if start and end:
            return {"kind": "arrow", "points": [start, end], "label": "swipe",
                    "emphasis": "strong"}
        return {"kind": "none", "points": [], "label": "swipe", "emphasis": "strong"}
    if kind == "input_text":
        spot = point(action.get("x"), action.get("y"))
        text = str(action.get("text") or "")
        return {"kind": "point" if spot else "none", "points": [spot] if spot else [],
                "label": f"input_text: {text}"[:80], "emphasis": "soft"}
    return {"kind": "none", "points": [], "label": kind or "?", "emphasis": "strong"}


class AppCorpus:
    """One package's collected session, read only, encoded like the export.

    Screens are memoised per instance: consecutive triples share an observation
    (step N's ``after`` is step N+1's ``before``), so a naive pass would encode
    every dump twice.
    """

    def __init__(
        self,
        raw_root: str | Path,
        package: str,
        *,
        device_size: tuple[int, int] = (1080, 2400),
        max_pixels: int = DEFAULT_MAX_PIXELS,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.raw_root = Path(raw_root)
        self.package = package
        self.app_dir = self.raw_root / package
        self.max_pixels = max_pixels
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._screens: dict[int, Screen] = {}
        self._meta: dict[str, Any] | None = None
        self._rows: list[StepRow] | None = None
        self._fallback = device_size

    # -- disk ---------------------------------------------------------------

    @property
    def triples_path(self) -> Path:
        return self.app_dir / "triples.jsonl"

    def metadata(self) -> dict[str, Any]:
        """``metadata.json`` or ``{}``. Never required: a live app has none."""
        if self._meta is None:
            path = self.app_dir / "metadata.json"
            try:
                self._meta = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - absent or half-written is normal
                self._meta = {}
        return self._meta

    def device_size(self) -> tuple[int, int]:
        """The session's ``wm size``, used ONLY as a per-observation fallback."""
        meta = self.metadata()
        width, height = meta.get("device_width"), meta.get("device_height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return (width, height)
        return self._fallback

    def is_live(self) -> bool:
        """Whether the collector still looks to be appending to this app."""
        try:
            age = time.time() - self.triples_path.stat().st_mtime
        except OSError:
            return False
        return age < LIVE_WINDOW_SEC and not self.metadata().get("completed_at")

    def observation_dir(self, index: int) -> Path:
        return self.app_dir / "observations" / f"{index:04d}"

    # -- screens ------------------------------------------------------------

    def screen(self, index: int) -> Screen:
        cached = self._screens.get(index)
        if cached is not None:
            return cached
        while len(self._screens) >= SCREEN_MEMO:
            self._screens.pop(next(iter(self._screens)))
        directory = self.observation_dir(index)
        dump = directory / DUMP_NAME
        shot = directory / SCREENSHOT_NAME
        screen = Screen(index=index, exists=dump.is_file(), has_shot=shot.is_file())
        if screen.exists:
            size, measured = observation_size(shot, self.device_size())
            screen.size, screen.measured = size, measured
            try:
                screen.raw = dump.read_text(encoding="utf-8")
                screen.package = dominant_package(screen.raw)
                screen.html = encode_screen(screen.raw, size[0], size[1], self.max_pixels)
                screen.nodes = screen.html.count("<")
            except Exception as error:  # noqa: BLE001 - one bad dump costs one screen
                screen.error = f"{type(error).__name__}: {error}"
        self._screens[index] = screen
        return screen

    # -- rows ---------------------------------------------------------------

    def rows(self, *, refresh: bool = False) -> list[StepRow]:
        """Every triple, classified and grouped. Cached on disk when settled."""
        if self._rows is not None and not refresh:
            return self._rows
        cached = None if refresh else self._read_cache()
        if cached is not None:
            self._rows = cached
            return cached
        rows = self._compute_rows()
        self._rows = rows
        self._write_cache(rows)
        return rows

    def _compute_rows(self) -> list[StepRow]:
        raw_rows, skipped = read_triples_tolerant(self.triples_path)
        if skipped:
            logger.debug("{}: skipped {} unreadable triple line(s)", self.package, skipped)
        rows: list[StepRow] = []
        seen_steps: set[int] = set()
        groups: dict[str, int] = {}
        for raw in raw_rows:
            try:
                step = int(raw["step"])
                before = int(raw["before"])
                after = int(raw["after"])
            except (KeyError, TypeError, ValueError):
                continue
            raw_action = raw.get("action")
            action: dict[str, Any] = raw_action if isinstance(raw_action, dict) else {}
            row = StepRow(
                step=step,
                before=before,
                after=after,
                action=action,
                changed=bool(raw.get("changed")),
                page_changed=bool(raw.get("page_changed")),
                reason=str(raw.get("reason") or ""),
                from_page=int(raw.get("from_page", -1) or -1),
                to_page=int(raw.get("to_page", -1) or -1),
            )
            before_screen = self.screen(before)
            after_screen = self.screen(after)
            row.before_size = before_screen.size
            row.after_size = after_screen.size
            row.mark = action_mark(action, before_screen.size)
            row.key = identity_key(before_screen.raw, after_screen.raw, action)
            row.min_nodes = min(
                before_screen.nodes or 0, after_screen.nodes or 0
            ) if before_screen.html and after_screen.html else 0

            # Drop classification, in the exporter's own order so the reason a
            # reviewer sees is the reason the export would record.
            payload = translate_action(action, device_size=before_screen.size or self.device_size())
            if not row.changed:
                row.drop = DROP_UNCHANGED
            elif payload is None:
                row.drop = DROP_UNKNOWN_ACTION
            elif not (before_screen.exists and after_screen.exists and before_screen.has_shot):
                row.drop = DROP_MISSING_FILES
            elif (before_screen.package and before_screen.package != self.package) or (
                after_screen.package and after_screen.package != self.package
            ):
                row.drop = DROP_FOREIGN
            elif before_screen.error or after_screen.error:
                row.drop = DROP_UNPARSABLE
            elif step in seen_steps:
                row.drop = DROP_DUPLICATE_STEP
            seen_steps.add(step)

            if payload is not None and all(before_screen.size):
                row.coord_out_of_frame = out_of_frame_points(payload, before_screen.frame)
            if before_screen.html and after_screen.html:
                row.same_html = before_screen.html.strip() == after_screen.html.strip()

            # Group identity spans EXPORTABLE rows only: a dropped row is not in
            # the corpus, so counting it as a duplicate of one that is would
            # inflate every redundancy number on the app card.
            if row.exportable:
                row.group = identity_key(before_screen.html, after_screen.html, payload or {})
                row.rank = groups.get(row.group, 0)
                groups[row.group] = row.rank + 1
            rows.append(row)

        sizes = dict(groups)
        for row in rows:
            if row.group:
                row.group_size = sizes.get(row.group, 1)
        return rows

    def summary(self, *, refresh: bool = False) -> AppSummary:
        rows = self.rows(refresh=refresh)
        exportable = [r for r in rows if r.exportable]
        sizes: dict[str, int] = {}
        for row in exportable:
            sizes[row.group] = sizes.get(row.group, 0) + 1
        meta = self.metadata()
        # Read off the rows, not off `self._screens`: a cached analysis never
        # populates the screen memo, and a landscape count that silently reads 0
        # would hide exactly the rotation this corpus is known to contain
        # (265 of markor's observations are 2400x1080).
        landscape = len(
            {
                index
                for row in rows
                for index, size in ((row.before, row.before_size), (row.after, row.after_size))
                if all(size) and size[0] > size[1]
            }
        )
        return AppSummary(
            package=self.package,
            triples=len(rows),
            exportable=len(exportable),
            distinct=len(sizes),
            redundant=sum(c - 1 for c in sizes.values()),
            largest_group=max(sizes.values(), default=0),
            live=self.is_live(),
            complete=bool(meta.get("completed_at")),
            device=self.device_size(),
            landscape_obs=landscape,
            stop_reason=str(meta.get("stop_reason") or ""),
        )

    # -- cache --------------------------------------------------------------

    def _cache_path(self) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / "rows" / f"{self.package}.json"

    def _stamp(self) -> dict[str, Any]:
        """What must be identical for a cached analysis to still be valid."""
        try:
            stat = self.triples_path.stat()
        except OSError:
            return {}
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "version": 2}

    def _read_cache(self) -> list[StepRow] | None:
        path = self._cache_path()
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a bad cache is just a cold start
            return None
        if payload.get("stamp") != self._stamp() or not self._stamp():
            return None
        rows: list[StepRow] = []
        for raw in payload.get("rows", []):
            rows.append(
                StepRow(
                    step=raw["step"],
                    before=raw["before"],
                    after=raw["after"],
                    action=raw["action"],
                    changed=raw["changed"],
                    page_changed=raw["page_changed"],
                    reason=raw["reason"],
                    from_page=raw["from_page"],
                    to_page=raw["to_page"],
                    drop=raw["drop"],
                    key=raw["key"],
                    group=raw["group"],
                    rank=raw["rank"],
                    group_size=raw["group_size"],
                    same_html=raw["same_html"],
                    coord_out_of_frame=raw["coord_out_of_frame"],
                    before_size=tuple(raw["before_size"]),
                    after_size=tuple(raw["after_size"]),
                    min_nodes=raw["min_nodes"],
                    mark=raw["mark"],
                )
            )
        return rows

    def _write_cache(self, rows: list[StepRow]) -> None:
        path = self._cache_path()
        stamp = self._stamp()
        # A live app is still growing: caching it would freeze a tail that moves.
        if path is None or not stamp or self.is_live():
            return
        write_json_atomic(path, {"stamp": stamp, "rows": [r.as_dict() for r in rows]})


def discover(raw_root: str | Path) -> list[str]:
    """Packages with a collected session under *raw_root*."""
    root = Path(raw_root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "triples.jsonl").is_file())


__all__ = [
    "DROP_DUPLICATE_STEP",
    "DROP_FOREIGN",
    "DROP_MISSING_FILES",
    "DROP_UNCHANGED",
    "DROP_UNKNOWN_ACTION",
    "DROP_UNPARSABLE",
    "AppCorpus",
    "AppSummary",
    "Screen",
    "StepRow",
    "action_mark",
    "discover",
    "read_triples_tolerant",
]
