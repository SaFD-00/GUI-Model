"""ADB shell monkey execution and monitoring."""

import subprocess
import logging
from typing import Optional
from .client import AdbClient

logger = logging.getLogger(__name__)


class MonkeyRunner:
    """Manages adb shell monkey execution."""

    def __init__(self, adb: AdbClient):
        self.adb = adb

    def run(
        self,
        package: str,
        events: int,
        throttle: int = 500,
        pct_touch: int = 40,
        pct_motion: int = 20,
        pct_nav: int = 15,
        pct_majornav: int = 10,
        pct_syskeys: int = 10,
        pct_appswitch: int = 0,
        pct_anyevent: int = 5,
        seed: Optional[int] = None,
    ) -> subprocess.Popen:
        """Run monkey with specified parameters.

        Returns a Popen process that can be waited on.
        """
        cmd_parts = [
            f"monkey -p {package}",
            f"--throttle {throttle}",
            f"--pct-touch {pct_touch}",
            f"--pct-motion {pct_motion}",
            f"--pct-nav {pct_nav}",
            f"--pct-majornav {pct_majornav}",
            f"--pct-syskeys {pct_syskeys}",
            f"--pct-appswitch {pct_appswitch}",
            f"--pct-anyevent {pct_anyevent}",
        ]
        if seed is not None:
            cmd_parts.append(f"-s {seed}")
        cmd_parts.append(f"-v {events}")

        command = " ".join(cmd_parts)
        logger.info(f"Starting monkey: {command}")
        return self.adb.shell_popen(command)

    def parse_monkey_log(self, line: str) -> Optional[dict]:
        """Parse a monkey verbose log line into an event dict.

        Monkey -v output format examples:
            :Sending Touch (ACTION_DOWN): 0:(540.0,1200.0)
            :Sending Touch (ACTION_UP): 0:(540.0,1200.0)
            :Sending Trackball (ACTION_MOVE): 0:(-3.0,4.0)
            :Sending Key (ACTION_DOWN): 0    // KEYCODE_BACK
        """
        if not line.startswith(":Sending"):
            return None

        line = line.strip()

        if "Touch" in line and "ACTION_DOWN" in line:
            # Parse tap coordinates
            try:
                coords = line.split(":(")[1].rstrip(")")
                x, y = coords.split(",")
                return {
                    "type": "tap",
                    "x": float(x),
                    "y": float(y),
                }
            except (IndexError, ValueError):
                return None

        elif "Trackball" in line and "ACTION_MOVE" in line:
            try:
                coords = line.split(":(")[1].rstrip(")")
                dx, dy = coords.split(",")
                return {
                    "type": "swipe",
                    "dx": float(dx),
                    "dy": float(dy),
                }
            except (IndexError, ValueError):
                return None

        elif "Key" in line:
            # Extract key code from comment if available
            try:
                parts = line.split("//")
                key = parts[1].strip() if len(parts) > 1 else "UNKNOWN"
                return {"type": "key", "key": key}
            except (IndexError, ValueError):
                return None

        return None
