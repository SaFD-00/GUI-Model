"""Progressive activity-coverage tracking.

Coverage is the headline number for an exploration run — "we reached 31 of this
app's 54 declared Activities" — and the ONLY axis on which two budget settings,
two merge policies, or two explorers can be compared. It is written as a
timeseries, not a final tally, so the shape of the curve (fast rise then a long
flat tail vs. steady climb) is recoverable afterwards.

THE DENOMINATOR IS THE WHOLE PROBLEM
====================================

A coverage percentage is only as trustworthy as the set it divides by, and there
are two ways to get that set, which disagree:

* **Static, from the APK manifest** (``androguard``, cached in
  ``catalog/activities.json``). Fixed across sessions and devices, so two runs
  are comparable. This is the ground truth we want.
* **Dynamic, from ``adb shell dumpsys package``**. Available without the APK,
  but it reflects what this device happens to have installed and enabled, so the
  denominator can shift under you between runs.

:class:`ActivityCoverage` takes the denominator from the caller and records
which source it came from, so a coverage number can never be quoted without its
provenance. When the ground truth is fixed (``allow_dynamic_total=False``, the
default), an activity observed at runtime but ABSENT from it — a system dialog,
a share sheet, a permission prompt, another app's screen — is logged for
traceability but does NOT count toward the numerator and does NOT extend the
denominator. Letting it do either is how a run reports 108% coverage, or how two
runs of the same app become silently incomparable.

``allow_dynamic_total=True`` restores the looser behaviour (unknown activities
of the target package extend the total on first sight). It exists for the case
where no manifest is available at all; it is not a default worth having.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

CSV_COLUMNS = (
    "timestamp_sec",
    "step",
    "activity",
    "counted",
    "unique_visited",
    "total_activities",
    "coverage",
)


def normalize_activity(name: str) -> str:
    """Expand a shorthand component name to its full form.

    ``com.test.app/.MainActivity`` -> ``com.test.app/com.test.app.MainActivity``

    ``dumpsys`` emits the shorthand and an AccessibilityService/uiautomator dump
    emits the full form; without this they are two different strings for one
    Activity, which silently halves coverage.
    """
    name = (name or "").strip()
    if "/" not in name:
        return name
    package, _, activity = name.partition("/")
    if activity.startswith("."):
        activity = package + activity
    elif "." not in activity:
        activity = f"{package}.{activity}"
    return f"{package}/{activity}"


@dataclass
class ActivityCoverage:
    """Records each visited Activity and appends a coverage row per step."""

    package: str
    #: Declared Activities (the denominator), already normalized or not.
    declared: set[str] = field(default_factory=set)
    #: Where `declared` came from — "catalog" (static/androguard) or "dumpsys".
    source: str = "catalog"
    #: When False, runtime activities outside `declared` never affect the numbers.
    allow_dynamic_total: bool = False

    csv_path: Path | None = None
    _visited: set[str] = field(default_factory=set, init=False)
    _started: float = field(default=0.0, init=False)
    _rows: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.declared = {normalize_activity(a) for a in self.declared if a}
        self._started = time.monotonic()

    # -- lifecycle -----------------------------------------------------------

    def open(self, path: str | Path) -> None:
        """Create the CSV and write its header. Resets the timer."""
        self.csv_path = Path(path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(CSV_COLUMNS)
        self._started = time.monotonic()

    # -- recording -----------------------------------------------------------

    def record(self, activity: str, step: int) -> bool:
        """Note that *activity* is on screen at *step*.

        Returns True when this visit ADVANCED coverage, which is the signal the
        explorer wants ("are we still making progress?"), not merely whether the
        activity was in the target package.
        """
        name = normalize_activity(activity)
        counted = self._counts(name)
        advanced = False

        if counted and name not in self._visited:
            self._visited.add(name)
            if self.allow_dynamic_total:
                self.declared.add(name)
            advanced = True

        self._append_row(name, counted, step)
        return advanced

    def _counts(self, name: str) -> bool:
        if not name:
            return False
        if name in self.declared:
            return True
        if not self.allow_dynamic_total:
            # Logged in the CSV for traceability, but outside the fixed truth.
            return False
        return name.startswith(f"{self.package}/")

    def _append_row(self, name: str, counted: bool, step: int) -> None:
        if self.csv_path is None:
            return
        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                [
                    round(time.monotonic() - self._started, 3),
                    step,
                    name,
                    "true" if counted else "false",
                    len(self._visited),
                    len(self.declared),
                    f"{self.coverage:.6f}",
                ]
            )
        self._rows += 1

    # -- reporting -----------------------------------------------------------

    @property
    def unique_visited(self) -> int:
        return len(self._visited)

    @property
    def total(self) -> int:
        return len(self.declared)

    @property
    def coverage(self) -> float:
        """Visited / declared. Zero when the denominator is unknown.

        Returns 0.0 rather than raising on an empty denominator: a missing
        manifest must not abort a collection run, and 0.0 alongside
        ``total == 0`` is self-evidently "not measured" to any reader.
        """
        return (len(self._visited) / len(self.declared)) if self.declared else 0.0

    @property
    def unvisited(self) -> set[str]:
        return self.declared - self._visited

    def summary(self) -> str:
        return (
            f"activity coverage {self.unique_visited}/{self.total} "
            f"({self.coverage * 100:.1f}%) for {self.package} [denominator: {self.source}]"
        )

    def log_summary(self) -> None:
        if not self.declared:
            logger.warning(
                "activity coverage for {} is unmeasured: no declared activities "
                "(no catalog entry and dumpsys returned nothing)",
                self.package,
            )
            return
        logger.info(self.summary())


__all__ = ["CSV_COLUMNS", "ActivityCoverage", "normalize_activity"]
