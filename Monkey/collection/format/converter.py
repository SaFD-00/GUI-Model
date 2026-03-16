"""Convert raw session data to processed annotation formats.

Reads raw session directories containing XML dumps, screenshots, and
event logs, then generates multiple annotation types (grounding, OCR,
state diff, element QA, world modeling, captions) in JSONL format.
"""

import json
import shutil
from pathlib import Path

import yaml
from loguru import logger

from collection.annotation import (
    element_qa,
    grounding,
    ocr_extractor,
    state_diff,
    world_modeling,
)
from collection.annotation.llm_annotator import generate_caption
from collection.annotation.xml_parser import parse_uiautomator_xml


class FormatConverter:
    """Convert raw session data to Stage 1 training formats.

    Reads configuration from a YAML file, iterates over raw session
    directories, and produces JSONL output files for each annotation
    type.
    """

    def __init__(self, config_path: str = "configs/collection/default.yaml"):
        """Initialize converter with configuration.

        Args:
            config_path: Path to the collection configuration YAML.
        """
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        self.raw_dir = Path(self.config["collection"]["storage"]["output_dir"])
        self.output_dir = Path(self.config["format"]["output_dir"])
        self.ann_config: dict = self.config.get("annotation", {})

    def process_all(self) -> None:
        """Process all sessions in the raw directory."""
        if not self.raw_dir.exists():
            logger.warning(f"Raw directory not found: {self.raw_dir}")
            return

        session_dirs = sorted(
            d for d in self.raw_dir.iterdir() if d.is_dir()
        )
        logger.info(f"Found {len(session_dirs)} sessions to process")

        for session_dir in session_dirs:
            self.process_session(session_dir.name)

    def process_session(self, session_id: str) -> None:
        """Process a single session into annotation formats.

        Args:
            session_id: Name of the session directory.
        """
        session_dir = self.raw_dir / session_id
        if not session_dir.exists():
            logger.error(f"Session not found: {session_dir}")
            return

        logger.info(f"Processing session: {session_id}")

        # Load metadata
        meta_path = session_dir / "metadata.json"
        metadata: dict = {}
        if meta_path.exists():
            metadata = json.loads(meta_path.read_text())

        app_package = metadata.get("app_package", "unknown")
        resolution: tuple[int, int] = (1080, 2400)  # Default

        # Collect XML files
        xml_dir = session_dir / "xml"
        screenshot_dir = session_dir / "screenshots"
        xml_files = sorted(xml_dir.glob("*.xml"))

        if not xml_files:
            logger.warning(f"No XML files in session {session_id}")
            return

        # Load event flags for external app detection
        flagged_steps: set[int] = set()
        events_list: list[dict] = []
        events_path = session_dir / "events.jsonl"
        if events_path.exists():
            for line in events_path.read_text().splitlines():
                if line.strip():
                    ev = json.loads(line)
                    events_list.append(ev)
                    if ev.get("is_external"):
                        flagged_steps.add(ev["step"])

        # Prepare output directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        images_dir = self.output_dir / "images"
        images_dir.mkdir(exist_ok=True)

        grounding_results: list[dict] = []
        ocr_results: list[dict] = []
        state_diff_results: list[dict] = []
        element_qa_results: list[dict] = []
        world_model_results: list[dict] = []
        caption_results: list[dict] = []

        prev_xml_content: str | None = None
        prev_elements: list | None = None

        for xml_path in xml_files:
            step_str = xml_path.stem  # e.g., "0042"
            step = int(step_str)

            if step in flagged_steps:
                logger.debug(f"Skipping flagged external step {step}")
                continue

            xml_content = xml_path.read_text(encoding="utf-8")
            elements = parse_uiautomator_xml(xml_content)

            # Copy screenshot to output
            screenshot_src = screenshot_dir / f"{step_str}.png"
            if not screenshot_src.exists():
                screenshot_src = screenshot_dir / f"{step_str}.jpg"
            if screenshot_src.exists():
                screenshot_dest = (
                    images_dir
                    / f"{session_id}_{step_str}{screenshot_src.suffix}"
                )
                shutil.copy2(screenshot_src, screenshot_dest)
                image_rel = (
                    f"data/processed/images/{screenshot_dest.name}"
                )
            else:
                image_rel = ""

            base_id = f"monkey_{session_id}_{step_str}"
            base_meta = {
                "source": "monkey_collection",
                "platform": "android",
                "resolution": list(resolution),
                "app_package": app_package,
                "session_id": session_id,
                "step": step,
            }

            # --- Grounding ---
            if self.ann_config.get("grounding", {}).get("enabled", True):
                min_area = self.ann_config.get("grounding", {}).get(
                    "min_element_area", 100
                )
                for i, item in enumerate(
                    grounding.generate(elements, resolution, min_area)
                ):
                    item["id"] = f"{base_id}_grounding_{i}"
                    item["image"] = image_rel
                    item["metadata"] = base_meta
                    grounding_results.append(item)

            # --- OCR ---
            if self.ann_config.get("ocr", {}).get("enabled", True):
                min_len = self.ann_config.get("ocr", {}).get(
                    "min_text_length", 1
                )
                for i, item in enumerate(
                    ocr_extractor.generate(elements, resolution, min_len)
                ):
                    item["id"] = f"{base_id}_ocr_{i}"
                    item["image"] = image_rel
                    item["metadata"] = base_meta
                    ocr_results.append(item)

            # --- Element QA ---
            if self.ann_config.get("element_qa", {}).get("enabled", True):
                tpl_count = self.ann_config.get("element_qa", {}).get(
                    "templates_per_screen", 5
                )
                for i, item in enumerate(
                    element_qa.generate(elements, resolution, tpl_count)
                ):
                    item["id"] = f"{base_id}_element_qa_{i}"
                    item["image"] = image_rel
                    item["metadata"] = base_meta
                    element_qa_results.append(item)

            # --- State diff (needs consecutive pair) ---
            if (
                prev_elements is not None
                and self.ann_config.get("state_diff", {}).get(
                    "enabled", True
                )
            ):
                min_changes = self.ann_config.get("state_diff", {}).get(
                    "min_changes", 1
                )
                diff_item = state_diff.generate(
                    prev_elements, elements, resolution, min_changes
                )
                if diff_item:
                    diff_item["id"] = f"{base_id}_state_diff_0"
                    diff_item["image"] = image_rel
                    diff_item["metadata"] = base_meta
                    state_diff_results.append(diff_item)

            # --- World modeling (needs consecutive XML pair + event) ---
            if (
                prev_xml_content is not None
                and prev_elements is not None
            ):
                # Find matching event for this step transition
                event_data: dict = {"type": "tap", "x": 0, "y": 0}
                for ev in events_list:
                    if ev.get("step") == step - 1:
                        event_data = ev
                        break

                # Build previous screenshot path
                prev_step_str = f"{step - 1:04d}"
                prev_image_rel = (
                    image_rel.replace(
                        f"_{step_str}", f"_{prev_step_str}"
                    )
                    if image_rel
                    else ""
                )

                wm_item = world_modeling.generate(
                    before_xml=prev_xml_content,
                    after_xml=xml_content,
                    event=event_data,
                    before_elements=prev_elements,
                    screenshot_path=prev_image_rel,
                )
                if wm_item:
                    world_model_results.append(wm_item)

            # --- LLM Caption (optional) ---
            if (
                self.ann_config.get("llm_caption", {}).get(
                    "enabled", False
                )
                and image_rel
            ):
                cap_config = self.ann_config["llm_caption"]
                cap_item = generate_caption(
                    str(screenshot_src),
                    provider=cap_config.get("provider", "openai"),
                    model=cap_config.get("model", "gpt-4o-mini"),
                )
                if cap_item:
                    cap_item["id"] = f"{base_id}_caption_0"
                    cap_item["image"] = image_rel
                    cap_item["metadata"] = base_meta
                    caption_results.append(cap_item)

            prev_xml_content = xml_content
            prev_elements = elements

        # Write outputs
        self._write_jsonl("grounding.jsonl", grounding_results)
        self._write_jsonl("ocr.jsonl", ocr_results)
        self._write_jsonl("state_diff.jsonl", state_diff_results)
        self._write_jsonl("element_qa.jsonl", element_qa_results)
        self._write_jsonl("world_modeling.jsonl", world_model_results)
        self._write_jsonl("caption.jsonl", caption_results)

        logger.info(
            f"Session {session_id} processed: "
            f"grounding={len(grounding_results)}, "
            f"ocr={len(ocr_results)}, "
            f"state_diff={len(state_diff_results)}, "
            f"element_qa={len(element_qa_results)}, "
            f"world_model={len(world_model_results)}, "
            f"caption={len(caption_results)}"
        )

    def _write_jsonl(self, filename: str, items: list[dict]) -> None:
        """Append items to a JSONL file.

        Args:
            filename: Output filename (relative to output_dir).
            items: List of dicts to serialize as JSON lines.
        """
        if not items:
            return
        path = self.output_dir / filename
        with open(path, "a", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.debug(f"Wrote {len(items)} items to {path}")
