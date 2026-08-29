"""Session storage: observations, triples, and resumable session state.

THE TRIPLE IS STORED AS TWO REFERENCES, NOT TWO COPIES
======================================================

The collection unit is ``(before_xml, before_screenshot, action, after_xml,
after_screenshot)``. Writing that literally duplicates every screen, because the
*after* of step N and the *before* of step N+1 are the same settled screen.

So observations are written ONCE, numbered, and a triple stores the two indices.
This halves the corpus — screenshots dominate it at ~1.7 MB each — but the real
reason is correctness: the two sides are then guaranteed to be the same bytes.
Capturing them separately would let a background animation, a clock tick or a
notification slide in between, and the corpus would quietly contain triples
whose "before" never actually preceded that action.

WHAT IS RECORDED VS WHAT IS DECIDED LATER
=========================================

Every step is written, including the ones where the screen did not change. A
world model that knows an action does nothing is learning something real, but a
corpus swamped with no-ops is not — so ``changed`` is recorded as a FACT on each
triple and the decision about which triples to train on belongs to export
(milestone 5). Collect facts; filter at export. A filter applied here is
unrecoverable, and there is no way to tell afterwards what was dropped.

THE IMAGE FILENAME CARRIES IDENTITY
===================================

``EXP08_stage1_state.jsonl`` locates a sample's episode and step ONLY by parsing
``myset/images/episode_{EP}_step_{STEP}.jpg`` — there is no episode or step field
anywhere in the record. Episode is UNPADDED, step is ZERO-PADDED TO 4 DIGITS.

That padding is load-bearing and its failure is silent: ``step_2`` still matches
the builder's regex, so nothing aborts; it resolves to a file that does not
exist, the length filter gets ``None`` back, and the record is dropped exactly as
if it had merely been too long. :func:`image_name` is the single place that
formatting lives, so the export path cannot reinvent it differently.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loguru import logger

#: Files written per observation.
SCREENSHOT_NAME = "screenshot.png"
DUMP_NAME = "raw.xml"

#: Step padding in the exported image name. See the module docstring — this is
#: the silent-drop trap, not a cosmetic choice.
STEP_PAD = 4


def image_name(episode: int | str, step: int) -> str:
    """``episode_{EP}_step_{STEP:04d}.jpg`` — episode unpadded, step 4-digit.

    The single source of this format. See the module docstring for why a
    mis-padded step is worse than a malformed one.
    """
    return f"episode_{episode}_step_{step:0{STEP_PAD}d}.jpg"


@dataclass(frozen=True)
class Observation:
    """One settled screen, already written to disk."""

    index: int
    page_key: str
    activity: str
    package: str
    #: Content-aware identity, so a caller can tell "same page" from "same screen".
    state_str: str
    is_new_page: bool
    match_kind: str
    #: True when the screen never settled and the last frame was accepted.
    stabilized: bool = True


@dataclass(frozen=True)
class Triple:
    """One action and the two observations it sits between."""

    step: int
    before: int
    after: int
    action: dict[str, Any]
    #: Whether the action changed the screen at all. A FACT, not a filter —
    #: export decides what to do with it.
    changed: bool
    #: Whether it changed the abstract PAGE, which is a stronger claim than
    #: `changed`: a list can scroll (state differs) without leaving the page.
    page_changed: bool
    reason: str = ""


class Session:
    """Writes one app's collection session and can resume it.

    Layout::

        {data_dir}/{package}/
            observations/{index:04d}/screenshot.png
            observations/{index:04d}/raw.xml
            triples.jsonl
            metadata.json
        {runtime_dir}/apps/{package}/
            activity_coverage.csv
            cost.csv

    Persistent corpus and volatile run state are deliberately separated: a reset
    of one must not silently resurrect the other. A stale ``observations/`` next
    to a fresh metadata file is how a "new" session inherits page numbering it
    never produced.
    """

    def __init__(
        self,
        package: str,
        data_dir: str | Path,
        runtime_dir: str | Path,
        *,
        episode: int | str | None = None,
    ) -> None:
        self.package = package
        self.root = Path(data_dir) / package
        self.runtime = Path(runtime_dir) / "apps" / package
        self.observations_dir = self.root / "observations"
        self.triples_path = self.root / "triples.jsonl"
        self.metadata_path = self.root / "metadata.json"
        #: Episode id for the exported image name. Defaults to the package so a
        #: corpus mixing apps still separates their episodes.
        self.episode: int | str = package if episode is None else episode

        self._next_index = 0
        self._steps = 0
        self._started_at = 0.0
        self._started_monotonic = 0.0

    # -- lifecycle -----------------------------------------------------------

    def open(self, *, resume: bool = True) -> bool:
        """Prepare the directories. Returns True when an existing session resumed.

        Resuming continues the observation numbering rather than restarting it,
        because ``triples.jsonl`` references observations by index — restarting
        would make older triples point at newer screens, and nothing would flag it.
        """
        self.observations_dir.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

        resumed = False
        if resume and self.metadata_path.exists():
            meta = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            # Metadata is only rewritten when a run ENDS, so a session killed
            # mid-flight leaves it saying 0 -- and resuming from 0 overwrites
            # every screen already on disk while triples.jsonl keeps appending.
            # The supervisor restarts on exactly that kind of death, so the
            # filesystem, not the metadata, is the authority on what exists.
            self._next_index = max(int(meta.get("observations", 0)), self._observations_on_disk())
            self._steps = max(int(meta.get("steps", 0)), self._steps_on_disk())
            resumed = self._next_index > 0
            if resumed:
                logger.info(
                    "resuming {} at observation {} (step {})",
                    self.package,
                    self._next_index,
                    self._steps,
                )
        else:
            # A fresh session must not inherit a previous run's numbering.
            self._next_index = 0
            self._steps = 0
            self.triples_path.unlink(missing_ok=True)

        self._started_at = time.time()
        self._started_monotonic = time.monotonic()
        self.write_metadata(completed=False)
        return resumed

    def _observations_on_disk(self) -> int:
        """One past the highest observation index actually written."""
        highest = -1
        if self.observations_dir.is_dir():
            for child in self.observations_dir.iterdir():
                if child.is_dir() and child.name.isdigit():
                    highest = max(highest, int(child.name))
        return highest + 1

    def _steps_on_disk(self) -> int:
        """One past the highest step in ``triples.jsonl``.

        Read rather than counted: a resumed session's triples are appended, so
        the line count and the step numbering are not the same thing.
        """
        if not self.triples_path.is_file():
            return 0
        highest = -1
        for line in self.triples_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                highest = max(highest, int(json.loads(line)["step"]))
            except (ValueError, KeyError, TypeError):
                continue
        return highest + 1

    @property
    def is_complete(self) -> bool:
        """Whether a previous run finished this app, so `run` can skip it."""
        if not self.metadata_path.exists():
            return False
        meta = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        return bool(meta.get("completed_at"))

    @property
    def observation_count(self) -> int:
        return self._next_index

    @property
    def step_count(self) -> int:
        return self._steps

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self._started_monotonic

    # -- writing -------------------------------------------------------------

    def write_observation(
        self,
        *,
        png: bytes,
        raw_xml: str,
        page_key: str,
        activity: str,
        state_str: str,
        is_new_page: bool,
        match_kind: str,
        stabilized: bool = True,
    ) -> Observation:
        """Persist one settled screen and return its handle."""
        index = self._next_index
        directory = self.observations_dir / f"{index:04d}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / SCREENSHOT_NAME).write_bytes(png)
        (directory / DUMP_NAME).write_text(raw_xml, encoding="utf-8")
        self._next_index += 1
        return Observation(
            index=index,
            page_key=page_key,
            activity=activity,
            package=self.package,
            state_str=state_str,
            is_new_page=is_new_page,
            match_kind=match_kind,
            stabilized=stabilized,
        )

    def write_triple(
        self,
        *,
        before: Observation,
        after: Observation,
        action: dict[str, Any],
        reason: str = "",
    ) -> Triple:
        """Append one triple, deriving `changed` / `page_changed` from the pair."""
        triple = Triple(
            step=self._steps,
            before=before.index,
            after=after.index,
            action=action,
            changed=before.state_str != after.state_str,
            page_changed=before.page_key != after.page_key,
            reason=reason,
        )
        with self.triples_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(triple), ensure_ascii=False) + "\n")
        self._steps += 1
        return triple

    def write_metadata(self, *, completed: bool, extra: dict[str, Any] | None = None) -> None:
        """Write the resume/skip record.

        ``completed_at`` is the skip signal for the next run, so it is set ONLY
        when a session ends on its own terms. An interrupted session leaves it
        null and is resumed rather than skipped.

        ``extra`` is written UNDER the session's own fields, never over them.
        `observations` is where the next resume starts writing, and the loop's
        stats dict carries a key of the same name holding its per-run count --
        so letting `extra` win made the resume point stick at the first run's
        total. Measured: Markor resumed at observation 43 three times, so each
        run overwrote the previous run's screens while triples.jsonl kept
        appending. 108 triples ended up carrying 22 duplicate step numbers, and
        `image_name` derives the image filename from the step, so two records
        claimed one JPEG and older triples pointed at newer screens.
        """
        meta: dict[str, Any] = dict(extra or {})
        meta.update(
            {
                "package": str(self.package),
                "episode": self.episode,
                "started_at": self._started_at,
                "completed_at": time.time() if completed else None,
                "observations": self._next_index,
                "steps": self._steps,
                "elapsed_sec": round(self.elapsed_sec, 1),
            }
        )
        self.metadata_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_metadata(self) -> dict[str, Any]:
        """The session's metadata, or an empty dict when it has none yet."""
        if not self.metadata_path.exists():
            return {}
        return dict(json.loads(self.metadata_path.read_text(encoding="utf-8")))

    def device_size(self) -> tuple[int, int] | None:
        """The resolution this session was collected at, if it was recorded.

        Authoritative for export's coordinate transform. Returns None for a
        session predating the field, so the caller can fall back explicitly
        rather than silently assuming a default that may be wrong.
        """
        meta = self.read_metadata()
        width, height = meta.get("device_width"), meta.get("device_height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return (width, height)
        return None

    def observation_path(self, index: int) -> Path:
        return self.observations_dir / f"{index:04d}"

    def read_triples(self) -> list[Triple]:
        """Every triple written so far. Used by export and by tests."""
        if not self.triples_path.exists():
            return []
        return [
            Triple(**json.loads(line))
            for line in self.triples_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


__all__ = [
    "DUMP_NAME",
    "SCREENSHOT_NAME",
    "STEP_PAD",
    "Observation",
    "Session",
    "Triple",
    "image_name",
]
