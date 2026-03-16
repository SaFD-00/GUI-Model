"""App escape detection monitoring and data filtering."""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FallbackMonitor:
    """Monitors app escape events and tracks fallback state."""

    max_back_attempts: int = 3
    allowed_packages: list[str] = field(default_factory=lambda: [
        "com.android.systemui",
        "com.android.permissioncontroller",
    ])

    # Runtime state
    consecutive_escapes: int = 0
    total_escapes: int = 0
    flagged_steps: set[int] = field(default_factory=set)

    def on_external_app(self, step: int, payload: dict) -> bool:
        """Process external app event. Returns True if step should be flagged."""
        self.consecutive_escapes += 1
        self.total_escapes += 1
        self.flagged_steps.add(step)

        detected_pkg = payload.get("detected_package", payload.get("top", ""))
        target_pkg = payload.get("target_package", payload.get("target", ""))

        logger.warning(
            f"External app at step {step}: {detected_pkg} "
            f"(consecutive: {self.consecutive_escapes})"
        )
        return True

    def on_valid_step(self):
        """Reset consecutive escape counter on valid step."""
        self.consecutive_escapes = 0

    def should_force_restart(self) -> bool:
        """Check if too many consecutive escapes warrant a force restart."""
        return self.consecutive_escapes >= self.max_back_attempts

    def is_step_flagged(self, step: int) -> bool:
        """Check if a step is flagged as external app."""
        return step in self.flagged_steps

    def get_stats(self) -> dict:
        return {
            "total_escapes": self.total_escapes,
            "flagged_steps": len(self.flagged_steps),
            "consecutive_escapes": self.consecutive_escapes,
        }
