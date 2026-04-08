"""Activity coverage tracking for progressive measurement.

Logs which Android Activities are visited during exploration to a CSV file,
enabling generation of Progressive Activity Coverage charts.

CSV format:
    timestamp_sec,step,activity,unique_visited,total_activities,coverage
"""

import csv
import os
import time

from loguru import logger


def _normalize_activity_name(name: str) -> str:
    """Expand shorthand activity component name to full format.

    ``com.test.app/.MainActivity`` → ``com.test.app/com.test.app.MainActivity``

    This ensures names from ``dumpsys`` (shorthand) and from the Android
    AccessibilityService (full) map to the same key.
    """
    if "/" not in name:
        return name
    pkg, cls = name.split("/", 1)
    if cls.startswith("."):
        cls = pkg + cls
    return f"{pkg}/{cls}"


class ActivityCoverageTracker:
    """Tracks and persists activity coverage over time and steps."""

    CSV_COLUMNS = [
        "timestamp_sec", "step", "activity",
        "unique_visited", "total_activities", "coverage",
    ]

    def __init__(self):
        self.csv_path: str = ""
        self.visited_activities: set[str] = set()
        self.total_activities: list[str] = []
        self._total_set: set[str] = set()  # normalized names for O(1) lookup
        self.start_time: float = 0.0
        self._initialized = False

    def initialize(self, session_dir: str, total_activities: list[str]) -> None:
        """Set total activities and create CSV file with header.

        Resets internal state so the tracker can be reused across sessions.
        """
        self.csv_path = os.path.join(session_dir, "activity_coverage.csv")
        self.total_activities = total_activities
        self._total_set = {_normalize_activity_name(a) for a in total_activities}
        self.visited_activities = set()
        self.start_time = time.time()

        with open(self.csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
            writer.writeheader()
        self._initialized = True
        logger.info(
            f"Activity coverage tracker initialized: "
            f"{len(total_activities)} total activities, "
            f"csv={self.csv_path}"
        )

    def record(self, activity_name: str, step: int) -> dict:
        """Record a new observation and append to CSV.

        Args:
            activity_name: Current foreground Activity component name.
            step: Interaction step count.

        Returns:
            dict with coverage entry data.
        """
        if activity_name:
            self.visited_activities.add(activity_name)
            # Safety net: dynamically expand total if an unknown activity
            # is visited (e.g. dumpsys missed it or format mismatch).
            normalized = _normalize_activity_name(activity_name)
            if normalized not in self._total_set:
                self.total_activities.append(activity_name)
                self._total_set.add(normalized)

        total = max(len(self.total_activities), 1)
        coverage = len(self.visited_activities) / total
        elapsed = time.time() - self.start_time

        entry = {
            "timestamp_sec": round(elapsed, 2),
            "step": step,
            "activity": activity_name,
            "unique_visited": len(self.visited_activities),
            "total_activities": len(self.total_activities),
            "coverage": round(coverage, 4),
        }

        if self._initialized:
            with open(self.csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
                writer.writerow(entry)

        return entry

    def resume(self, session_dir: str, total_activities: list[str]) -> None:
        """Resume from existing activity_coverage.csv.

        Rebuilds visited_activities from CSV and appends new records.
        """
        self.csv_path = os.path.join(session_dir, "activity_coverage.csv")
        self.total_activities = total_activities
        self._total_set = {_normalize_activity_name(a) for a in total_activities}
        self.visited_activities = set()
        self.start_time = time.time()

        if os.path.exists(self.csv_path):
            with open(self.csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    activity = row.get("activity", "")
                    if activity:
                        self.visited_activities.add(activity)
                        # Expand total for previously visited unknowns too.
                        normalized = _normalize_activity_name(activity)
                        if normalized not in self._total_set:
                            self.total_activities.append(activity)
                            self._total_set.add(normalized)

        self._initialized = True
        logger.info(
            f"Activity coverage tracker resumed: "
            f"{len(self.visited_activities)} previously visited, "
            f"{len(total_activities)} total activities"
        )

    def get_coverage(self) -> float:
        """Current coverage ratio."""
        total = max(len(self.total_activities), 1)
        return len(self.visited_activities) / total

    def get_visited_count(self) -> int:
        """Number of unique activities visited."""
        return len(self.visited_activities)
