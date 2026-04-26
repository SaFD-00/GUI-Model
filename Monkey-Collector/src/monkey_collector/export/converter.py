"""Convert raw collection sessions to gui-model_stage1.jsonl format.

Produces ShareGPT-format data for UI state transition prediction (World Modeling).
"""

import json
import shutil
from pathlib import Path

from loguru import logger

from monkey_collector.xml.ui_tree import UIElement, parse_uiautomator_xml

SYSTEM_PROMPT = (
    "You are a mobile UI transition predictor.\n"
    "Given the current screen represented as html-style XML and an action "
    "description, predict the next screen's html-style XML after the action "
    "is executed."
)


def _find_element_at(
    elements: list[UIElement], x: int, y: int
) -> UIElement | None:
    """Find the smallest element containing the point (x, y)."""
    candidates: list[UIElement] = []
    for elem in elements:
        left, top, right, bottom = elem.bounds
        if left <= x <= right and top <= y <= bottom:
            candidates.append(elem)
    if not candidates:
        return None
    return min(candidates, key=lambda e: e.area)


def _map_event_to_action(
    event: dict, elements: list[UIElement]
) -> dict | None:
    """Map a collector event to GUI-Model action format."""
    event_type = event.get("action_type", "")
    element_index = event.get("element_index", -1)

    if event_type == "tap":
        # Use recorded element_index if available, fallback to coordinate lookup
        if element_index >= 0:
            idx = element_index
        else:
            x, y = event.get("x", 0), event.get("y", 0)
            target = _find_element_at(elements, x, y)
            idx = target.index if target else -1
        return {
            "type": "Click",
            "params": {},
            "default": True,
            "index": idx,
        }
    elif event_type == "swipe":
        if "x1" in event and "x2" in event:
            dx = event.get("x2", 0) - event.get("x1", 0)
            dy = event.get("y2", 0) - event.get("y1", 0)
        else:
            dx, dy = 0, -1  # default up
        if abs(dy) >= abs(dx):
            direction = "Down" if dy > 0 else "Up"
        else:
            direction = "Right" if dx > 0 else "Left"
        return {
            "type": "Swipe",
            "params": {"direction": direction},
            "default": False,
            "index": element_index,
        }
    elif event_type == "input_text":
        return {
            "type": "Input",
            "params": {"text": event.get("text", "")},
            "default": False,
            "index": element_index,
        }
    elif event_type == "press_back":
        return {
            "type": "Back",
            "params": {},
            "default": False,
            "index": -1,
        }
    elif event_type == "long_press":
        if element_index < 0:
            x, y = event.get("x", 0), event.get("y", 0)
            target = _find_element_at(elements, x, y)
            element_index = target.index if target else -1
        return {
            "type": "LongClick",
            "params": {},
            "default": False,
            "index": element_index,
        }
    elif event_type == "press_home":
        return {
            "type": "Home",
            "params": {},
            "default": False,
            "index": -1,
        }
    return None


def generate_example(
    before_encoded_xml: str,
    after_encoded_xml: str,
    event: dict,
    screenshot_path: str,
    before_elements: list[UIElement] | None = None,
) -> dict | None:
    """Generate a world modeling training example.

    Args:
        before_encoded_xml: Encoded XML (_encoded.xml) for the before state.
        after_encoded_xml: Encoded XML (_encoded.xml) for the after state.
        event: Collector event dict.
        screenshot_path: Relative image path for the JSONL record.
        before_elements: UIElement list from raw XML for coordinate-based
            element lookup. Pass None if coordinate fallback is not needed.

    Returns:
        ShareGPT-format dict compatible with gui-model_stage1.jsonl,
        or None if no meaningful state change.
    """
    if not before_encoded_xml or not after_encoded_xml:
        return None

    if before_encoded_xml == after_encoded_xml:
        return None

    # Map event to action
    action = _map_event_to_action(event, before_elements or [])
    if action is None:
        return None

    action_json = json.dumps(action, indent=2)

    return {
        "messages": [
            {"from": "system", "value": SYSTEM_PROMPT},
            {
                "from": "human",
                "value": (
                    f"<image>\n## Current State\n{before_encoded_xml}\n\n"
                    f"## Action\n{action_json}"
                ),
            },
            {"from": "gpt", "value": after_encoded_xml},
        ],
        "images": [screenshot_path],
    }


class Converter:
    """Convert raw session data to gui-model_stage1.jsonl."""

    def __init__(self, output_path: str, images_dir: str):
        self.output_path = Path(output_path)
        self.images_dir = Path(images_dir)
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def convert_session(
        self, session_dir: str, session_label: int
    ) -> int:
        """Convert a single session to JSONL examples.

        Returns:
            Number of examples generated.
        """
        session = Path(session_dir)
        xml_dir = session / "xml"
        screenshots_dir = session / "screenshots"
        events_path = session / "events.jsonl"

        # Load raw XML files for step enumeration and element lookup
        raw_xml_files = sorted(
            f for f in xml_dir.glob("*.xml") if "_" not in f.stem
        )
        if len(raw_xml_files) < 2:
            logger.warning(f"Session {session_dir}: not enough XML files")
            return 0

        # Load events with transition=true
        events: dict[int, dict] = {}
        if events_path.exists():
            for line in events_path.read_text().splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("transition", True):
                    events[ev.get("step", -1)] = ev

        count = 0
        for i in range(len(raw_xml_files) - 1):
            step_idx = int(raw_xml_files[i].stem)
            next_step_idx = int(raw_xml_files[i + 1].stem)

            # Read encoded XML (_encoded.xml) for training data
            before_encoded_path = xml_dir / f"{step_idx:04d}_encoded.xml"
            after_encoded_path = xml_dir / f"{next_step_idx:04d}_encoded.xml"

            if not before_encoded_path.exists() or not after_encoded_path.exists():
                logger.debug(
                    f"Encoded XML not found for step {step_idx} or {next_step_idx}"
                )
                continue

            before_encoded = before_encoded_path.read_text()
            after_encoded = after_encoded_path.read_text()

            # Read raw XML for element coordinate lookup
            before_raw = raw_xml_files[i].read_text()
            before_elements = parse_uiautomator_xml(before_raw)

            # Find matching event
            event = events.get(step_idx, {})
            if not event:
                # Try to find event by sequential index
                event = self._find_event_by_index(events, i)

            # Image naming: episode_{label:06d}_step_{step:04d}.png
            image_name = f"episode_{session_label:06d}_step_{count + 1:04d}.png"
            image_rel = f"GUI-Model/images/{image_name}"

            # Copy screenshot
            src_screenshot = screenshots_dir / f"{step_idx:04d}.png"
            if not src_screenshot.exists():
                src_screenshot = screenshots_dir / f"{i:04d}.png"
            if not src_screenshot.exists():
                logger.debug(f"Screenshot not found for step {step_idx}")
                continue

            example = generate_example(
                before_encoded, after_encoded, event, image_rel, before_elements
            )
            if example is None:
                continue

            # Copy image
            dest_image = self.images_dir / image_name
            shutil.copy2(src_screenshot, dest_image)

            # Append to JSONL
            with open(self.output_path, "a") as f:
                f.write(json.dumps(example, ensure_ascii=False) + "\n")

            count += 1

        logger.info(
            f"Converted session {session.name}: {count} examples generated"
        )
        return count

    def convert_all(self, raw_dir: str) -> int:
        """Convert all sessions in a directory.

        Returns:
            Total number of examples generated.
        """
        raw = Path(raw_dir)
        sessions = sorted(
            [d for d in raw.iterdir() if d.is_dir() and (d / "xml").exists()]
        )

        if not sessions:
            logger.warning(f"No sessions found in {raw_dir}")
            return 0

        total = 0
        for label, session_dir in enumerate(sessions, start=1):
            n = self.convert_session(str(session_dir), label)
            total += n

        logger.info(f"Total: {total} examples from {len(sessions)} sessions")
        return total

    @staticmethod
    def _find_event_by_index(
        events: dict[int, dict], index: int
    ) -> dict:
        """Fallback: find event by sequential position."""
        sorted_keys = sorted(events.keys())
        if index < len(sorted_keys):
            return events[sorted_keys[index]]
        return {}
