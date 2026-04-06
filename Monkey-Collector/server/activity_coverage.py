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
        self.start_time: float = 0.0
        self._initialized = False

    def initialize(self, session_dir: str, total_activities: list[str]) -> None:
        """Set total activities and create CSV file with header.

        Resets internal state so the tracker can be reused across sessions.
        """
        self.csv_path = os.path.join(session_dir, "activity_coverage.csv")
        self.total_activities = total_activities
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

    def get_coverage(self) -> float:
        """Current coverage ratio."""
        total = max(len(self.total_activities), 1)
        return len(self.visited_activities) / total

    def get_visited_count(self) -> int:
        """Number of unique activities visited."""
        return len(self.visited_activities)
