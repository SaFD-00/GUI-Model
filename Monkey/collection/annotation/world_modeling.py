"""Generate GUI World Modeling annotations (GUI-Model stage1 format).

Produces ShareGPT-format data for UI state transition prediction.
Output is compatible with gui-model_stage1.jsonl.

Given the current screen as HTML-style XML and an action description,
the model predicts the next screen's HTML-style XML after the action.
"""

import json

from loguru import logger

from collection.annotation.xml_parser import UIElement
from collection.annotation.xml_encoder import encode_to_html_xml, indent_xml


SYSTEM_PROMPT = (
    "You are a mobile UI transition predictor.\n"
    "Given the current screen represented as html-style XML and an action "
    "description, predict the next screen's html-style XML after the action "
    "is executed."
)


def _find_element_at(
    elements: list[UIElement], x: int, y: int
) -> UIElement | None:
    """Find the smallest element containing the point (x, y).

    When multiple elements overlap at the given coordinates, the
    smallest (most specific) one is returned.

    Args:
        elements: List of UI elements to search.
        x: Horizontal pixel coordinate.
        y: Vertical pixel coordinate.

    Returns:
        The smallest UIElement containing (x, y), or None.
    """
    candidates: list[UIElement] = []
    for elem in elements:
        left, top, right, bottom = elem.bounds
        if left <= x <= right and top <= y <= bottom:
            candidates.append(elem)
    if not candidates:
        return None
    # Return the smallest (most specific) element
    return min(candidates, key=lambda e: e.area)


def _map_monkey_event(
    event: dict, elements: list[UIElement]
) -> dict | None:
    """Map a monkey event to a GUI-Model action format.

    Args:
        event: Monkey event dict with type, coordinates, etc.
        elements: UI elements from the before state.

    Returns:
        Action dict with type, params, and index, or None.
    """
    event_type = event.get("type", "")

    if event_type == "tap":
        x, y = event.get("x", 0), event.get("y", 0)
        target = _find_element_at(elements, x, y)
        if target is None:
            return None
        return {
            "type": "Click",
            "params": {},
            "index": target.index,
        }
    elif event_type == "swipe":
        # Support both random monkey (dx/dy) and Smart Monkey (x1/y1/x2/y2) formats
        if "x1" in event and "x2" in event:
            dx = event.get("x2", 0) - event.get("x1", 0)
            dy = event.get("y2", 0) - event.get("y1", 0)
        else:
            dx = event.get("dx", 0)
            dy = event.get("dy", 0)
        if abs(dy) >= abs(dx):
            direction = "down" if dy > 0 else "up"
        else:
            direction = "right" if dx > 0 else "left"
        return {
            "type": "Scroll",
            "params": {"direction": direction},
            "index": -1,
        }
    elif event_type in ("text", "input_text"):
        return {
            "type": "Type",
            "params": {"text": event.get("text", "")},
            "index": -1,
        }
    elif event_type in ("back", "press_back"):
        return {
            "type": "Back",
            "params": {},
            "index": -1,
        }
    elif event_type == "long_press":
        x, y = event.get("x", 0), event.get("y", 0)
        target = _find_element_at(elements, x, y)
        return {
            "type": "LongClick",
            "params": {},
            "index": target.index if target else -1,
        }
    elif event_type == "press_home":
        return {
            "type": "Home",
            "params": {},
            "index": -1,
        }
    return None


def generate(
    before_xml: str,
    after_xml: str,
    event: dict,
    before_elements: list[UIElement],
    screenshot_path: str,
) -> dict | None:
    """Generate a world modeling training example.

    Encodes before and after screen states as HTML-style XML and maps
    the monkey event to a structured action format, producing a
    ShareGPT-format training example for state transition prediction.

    Args:
        before_xml: Raw uiautomator XML before the action.
        after_xml: Raw uiautomator XML after the action.
        event: Monkey event dict with type, coordinates, etc.
        before_elements: Parsed UIElements from before_xml.
        screenshot_path: Relative path to the before screenshot.

    Returns:
        ShareGPT-format dict compatible with gui-model_stage1.jsonl,
        or None if no meaningful state change occurred.
    """
    before_encoded = encode_to_html_xml(before_xml)
    after_encoded = encode_to_html_xml(after_xml)

    if not before_encoded or not after_encoded:
        logger.debug("Skipping world model: empty encoded XML")
        return None

    # Skip if no state change
    if before_encoded == after_encoded:
        logger.debug("Skipping world model: no state change detected")
        return None

    # Map event to action
    action = _map_monkey_event(event, before_elements)
    if action is None:
        # Fallback: generic click if we have coordinates
        x, y = event.get("x", 0), event.get("y", 0)
        if x > 0 and y > 0:
            target = _find_element_at(before_elements, x, y)
            action = {
                "type": "Click",
                "params": {},
                "index": target.index if target else -1,
            }
        else:
            return None

    # Pretty-print for readability
    before_pretty = indent_xml(before_encoded)
    after_pretty = indent_xml(after_encoded)
    action_json = json.dumps(action, indent=2)

    logger.debug(
        f"Generated world model example: action={action['type']}, "
        f"screenshot={screenshot_path}"
    )

    return {
        "messages": [
            {"from": "system", "value": SYSTEM_PROMPT},
            {
                "from": "human",
                "value": (
                    f"<image>\n## Current State\n{before_pretty}\n\n"
                    f"## Action\n{action_json}"
                ),
            },
            {"from": "gpt", "value": after_pretty},
        ],
        "images": [screenshot_path],
    }
