"""Action space definitions for Android GUI interactions."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass
class Action:
    """Base class for all Android GUI actions."""

    action_type: str = "unknown"
    element_index: int = -1

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            result[f.name] = value
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Action:
        action_type = d.get("action_type", "unknown")
        target_cls = ACTION_REGISTRY.get(action_type, cls)
        valid_keys = {f.name for f in fields(target_cls)}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return target_cls(**filtered)


@dataclass
class Tap(Action):
    """Tap on a specific coordinate or UI element."""

    action_type: str = "tap"
    x: int = 0
    y: int = 0


@dataclass
class Swipe(Action):
    """Swipe gesture from (x1, y1) to (x2, y2)."""

    action_type: str = "swipe"
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    duration_ms: int = 300


@dataclass
class InputText(Action):
    """Type text into a focused or specified input field."""

    action_type: str = "input_text"
    text: str = ""
    x: int = 0
    y: int = 0


@dataclass
class PressBack(Action):
    """Press the Android back button."""

    action_type: str = "press_back"


@dataclass
class PressHome(Action):
    """Press the Android home button."""

    action_type: str = "press_home"


@dataclass
class LongPress(Action):
    """Long-press on a specific coordinate or UI element."""

    action_type: str = "long_press"
    x: int = 0
    y: int = 0
    duration_ms: int = 1000


# ---------------------------------------------------------------------------
# Registry & factory
# ---------------------------------------------------------------------------

ACTION_REGISTRY: dict[str, type[Action]] = {
    "tap": Tap,
    "swipe": Swipe,
    "input_text": InputText,
    "press_back": PressBack,
    "press_home": PressHome,
    "long_press": LongPress,
}


def action_from_dict(d: dict[str, Any]) -> Action:
    """Factory function: create the appropriate Action subclass from *d*."""
    if not d:
        raise ValueError("Cannot create an action from an empty dict")
    return Action.from_dict(d)
