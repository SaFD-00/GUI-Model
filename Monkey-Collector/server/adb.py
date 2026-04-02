"""ADB command wrapper using subprocess."""

import os
import re
import shutil
import subprocess
import time
from typing import Optional

from loguru import logger

# Characters that need escaping for adb shell input text
_SPECIAL_CHARS = re.compile(r'([\\\"\'`\s&|;<>()$!~{}*?#])')


def _find_adb() -> str:
    """Locate the adb binary, checking PATH then common SDK locations."""
    found = shutil.which("adb")
    if found:
        return found
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidate = os.path.join(android_home, "platform-tools", "adb")
        if os.path.isfile(candidate):
            return candidate
    # macOS default location
    default = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    if os.path.isfile(default):
        return default
    return "adb"  # fallback, will raise FileNotFoundError if missing


def _escape_text_for_adb(text: str) -> str:
    """Escape text for safe use with ``adb shell input text``."""
    text = text.replace(" ", "%s")
    text = _SPECIAL_CHARS.sub(r'\\\1', text)
    return text


class AdbClient:
    """Wrapper for ADB shell commands."""

    def __init__(self, device_serial: Optional[str] = None):
        self.device_serial = device_serial
        self._adb = _find_adb()

    def _cmd_prefix(self) -> list[str]:
        if self.device_serial:
            return [self._adb, "-s", self.device_serial]
        return [self._adb]

    def shell(self, command: str, timeout: Optional[int] = None) -> str:
        """Run an ADB shell command and return stdout."""
        cmd = self._cmd_prefix() + ["shell", command]
        logger.debug(f"ADB: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0 and result.stderr:
            logger.warning(f"ADB stderr: {result.stderr.strip()}")
        return result.stdout.strip()

    def launch_app(self, package: str) -> str:
        """Launch an app's main launcher activity via am start."""
        resolve_output = self.shell(
            f"cmd package resolve-activity --brief "
            f"-a android.intent.action.MAIN "
            f"-c android.intent.category.LAUNCHER {package}"
        )
        for line in reversed(resolve_output.strip().split("\n")):
            line = line.strip()
            if "/" in line:
                return self.shell(f"am start -n {line}")
        # Fallback: let Android resolve the intent (no random events)
        return self.shell(
            f"am start -a android.intent.action.MAIN "
            f"-c android.intent.category.LAUNCHER {package}"
        )

    def force_stop(self, package: str) -> str:
        """Force stop an app."""
        return self.shell(f"am force-stop {package}")

    def get_device_resolution(self) -> tuple[int, int]:
        """Get device screen resolution."""
        output = self.shell("wm size")
        size_str = output.split(":")[-1].strip()
        w, h = size_str.split("x")
        return int(w), int(h)

    def press_back(self) -> str:
        """Press the back button."""
        return self.shell("input keyevent KEYCODE_BACK")

    def press_home(self) -> str:
        """Press the home button."""
        return self.shell("input keyevent KEYCODE_HOME")

    def tap(self, x: int, y: int) -> str:
        """Tap at the given (x, y) coordinates."""
        return self.shell(f"input tap {x} {y}")

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> str:
        """Perform a swipe gesture."""
        return self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

    def input_text(self, text: str) -> str:
        """Type text into the currently focused input field."""
        if not text:
            return ""
        escaped = _escape_text_for_adb(text)
        return self.shell(f"input text {escaped}")

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> str:
        """Long-press at (x, y) via a zero-movement swipe."""
        return self.swipe(x, y, x, y, duration_ms)

    def install(self, apk_path: str) -> str:
        """Install an APK."""
        cmd = self._cmd_prefix() + ["install", "-r", apk_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.stdout.strip()

    def get_current_package(self) -> str:
        """Return the package name of the current foreground app."""
        output = self.shell(
            "dumpsys activity activities | grep mResumedActivity"
        )
        match = re.search(r'(\S+/\S+)', output)
        if match:
            activity = match.group(1).strip()
            if "/" in activity:
                return activity.split("/", 1)[0]
            return activity
        return ""

    def wait_for_idle(self, timeout: float = 2.0) -> None:
        """Wait for the UI to settle after an action."""
        time.sleep(min(timeout, 1.0))
