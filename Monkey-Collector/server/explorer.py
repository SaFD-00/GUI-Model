"""Smart Explorer with XML-aware action selection.

Unlike random monkey (adb shell monkey), SmartExplorer:
1. Parses XML to identify interactable elements
2. Detects EditText fields and fills them with text
3. Detects app exit and automatically returns
4. Uses weighted action selection (tap 60%, back 10%, etc.)
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List

from loguru import logger

from server.adb import AdbClient
from server.xml_parser import UITree
from server.actions import (
    Action, Tap, Swipe, InputText, PressBack, PressHome, LongPress,
)

# Default action weights
DEFAULT_WEIGHTS: Dict[str, float] = {
    "tap": 0.60,
    "press_back": 0.10,
    "swipe": 0.10,
    "input_text": 0.10,
    "long_press": 0.05,
    "press_home": 0.00,
}

# Sample texts for EditText filling
SAMPLE_TEXTS = [
    "Hello World",
    "Test Note",
    "Meeting at 3pm",
    "Shopping list",
    "Important memo",
    "John Doe",
    "test@example.com",
    "12345",
    "New item",
    "Quick note",
]


class SmartExplorer:
    """XML-aware action selector and executor for Android apps."""

    def __init__(
        self,
        adb: AdbClient,
        config: Dict[str, Any] | None = None,
    ):
        config = config or {}
        self.adb = adb
        self.action_weights = config.get("action_weights", DEFAULT_WEIGHTS)
        self.action_delay_ms = config.get("action_delay_ms", 500)
        self.screen_width = config.get("screen_width", 1080)
        self.screen_height = config.get("screen_height", 1920)
        self.seed = config.get("seed", 42)
        self.sample_texts = config.get("sample_texts", SAMPLE_TEXTS)
        self._rng = random.Random(self.seed)

    def select_action(self, ui_tree: UITree) -> Action:
        """Select an action based on UI state and weights."""
        clickable = ui_tree.get_clickable_elements()
        editable = ui_tree.get_editable_elements()
        scrollable = ui_tree.get_scrollable_elements()

        # Build available actions with adjusted weights
        weights = dict(self.action_weights)

        # If there are editable fields, boost input_text weight
        if editable:
            weights["input_text"] = max(weights.get("input_text", 0.1), 0.25)

        # If no clickable elements, reduce tap weight
        if not clickable:
            weights["tap"] = 0.05

        # If no scrollable elements, reduce swipe weight
        if not scrollable:
            weights["swipe"] = 0.02

        # Normalize weights
        total = sum(weights.values())
        if total == 0:
            return PressBack()

        normalized = {k: v / total for k, v in weights.items()}

        # Weighted selection
        action_type = self._weighted_choice(normalized)

        return self._create_action(action_type, clickable, editable, scrollable)

    def execute_action(self, action: Action) -> None:
        """Execute an action on the device via ADB."""
        if isinstance(action, Tap):
            self.adb.tap(action.x, action.y)
        elif isinstance(action, Swipe):
            self.adb.swipe(
                action.x1, action.y1, action.x2, action.y2, action.duration_ms
            )
        elif isinstance(action, InputText):
            self.adb.input_text(action.text)
        elif isinstance(action, PressBack):
            self.adb.press_back()
        elif isinstance(action, PressHome):
            self.adb.press_home()
        elif isinstance(action, LongPress):
            self.adb.long_press(action.x, action.y, action.duration_ms)

    def has_left_app(self, package: str) -> bool:
        """Check if the device is no longer in the target app."""
        try:
            current = self.adb.get_current_package()
            if not current:
                return False
            return current != package
        except Exception:
            return False

    def return_to_app(self, package: str) -> None:
        """Return to the target app after leaving it."""
        try:
            self.adb.press_back()
            time.sleep(0.5)
            current = self.adb.get_current_package()
            if current != package:
                self.adb.launch_app(package)
                time.sleep(2)
        except Exception:
            self.adb.launch_app(package)
            time.sleep(2)

    def recover(self, package: str) -> None:
        """Recover from an error state."""
        try:
            self.adb.press_home()
            time.sleep(1)
            self.adb.launch_app(package)
            time.sleep(2)
        except Exception:
            logger.error("Recovery failed for package %s", package)

    def _create_action(
        self,
        action_type: str,
        clickable: list,
        editable: list,
        scrollable: list,
    ) -> Action:
        """Create a concrete action instance."""
        if action_type == "tap" and clickable:
            elem = self._rng.choice(clickable)
            cx, cy = elem.center
            return Tap(x=cx, y=cy, element_index=elem.index)

        if action_type == "input_text" and editable:
            elem = self._rng.choice(editable)
            cx, cy = elem.center
            # First tap to focus the field
            self.adb.tap(cx, cy)
            time.sleep(0.3)
            text = self._rng.choice(self.sample_texts)
            return InputText(text=text, element_index=elem.index)

        if action_type == "swipe":
            if scrollable:
                elem = self._rng.choice(scrollable)
                cx, cy = elem.center
                return Swipe(
                    x1=cx, y1=cy + 200,
                    x2=cx, y2=cy - 200,
                    duration_ms=300,
                    element_index=elem.index,
                )
            else:
                mid_x = self.screen_width // 2
                return Swipe(
                    x1=mid_x, y1=self.screen_height * 3 // 4,
                    x2=mid_x, y2=self.screen_height // 4,
                    duration_ms=300,
                )

        if action_type == "long_press" and clickable:
            elem = self._rng.choice(clickable)
            cx, cy = elem.center
            return LongPress(x=cx, y=cy, element_index=elem.index)

        if action_type == "press_home":
            return PressHome()

        if action_type == "press_back":
            return PressBack()

        # Fallback: random tap on a clickable element or random coordinates
        if clickable:
            elem = self._rng.choice(clickable)
            cx, cy = elem.center
            return Tap(x=cx, y=cy, element_index=elem.index)

        return Tap(
            x=self._rng.randint(100, self.screen_width - 100),
            y=self._rng.randint(200, self.screen_height - 200),
        )

    def _weighted_choice(self, weights: Dict[str, float]) -> str:
        """Weighted random selection from action types."""
        items = list(weights.items())
        values = [w for _, w in items]
        chosen = self._rng.choices([k for k, _ in items], weights=values, k=1)
        return chosen[0]
