"""Session-based raw data storage."""

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime

from loguru import logger


class DataWriter:
    """Writes raw collection data to session directories.

    Directory structure:
        data/raw/{session_id}/
        ├── metadata.json
        ├── screenshots/0000.png, 0001.png, ...
        ├── xml/0000.xml, 0001.xml, ...
        └── events.jsonl
    """

    def __init__(self, base_dir: str = "data/raw"):
        self.base_dir = base_dir
        self.session_dir: str | None = None
        self.step_count = 0

    def init_session(self, session_id: str, app_package: str):
        """Initialize a new session directory."""
        self.session_dir = os.path.join(self.base_dir, session_id)
        self.step_count = 0

        os.makedirs(os.path.join(self.session_dir, "screenshots"), exist_ok=True)
        os.makedirs(os.path.join(self.session_dir, "xml"), exist_ok=True)

        meta = {
            "session_id": session_id,
            "package": app_package,
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "total_steps": 0,
            "external_app_events": 0,
        }
        self._write_metadata(meta)
        logger.info(f"Session initialized: {self.session_dir}")

    def save_screenshot(self, image_data: bytes) -> str:
        """Save screenshot data. Returns file path."""
        path = os.path.join(self.session_dir, "screenshots", f"{self.step_count:04d}.png")
        with open(path, "wb") as f:
            f.write(image_data)
        return path

    def save_xml(self, xml_content: str) -> str:
        """Save raw XML and 4 parsed variants. Increments step count.

        Files produced per step::

            {step}.xml              raw uiautomator dump
            {step}_parsed.xml       semantic HTML tags + bounds + index
            {step}_hierarchy.xml    structure only (no text/bounds/index)
            {step}_encoded.xml      bounds removed, index only (LLM input)
            {step}_pretty.xml       pretty-printed encoded
        """
        xml_dir = os.path.join(self.session_dir, "xml")
        step = self.step_count

        # 1. raw
        raw_path = os.path.join(xml_dir, f"{step:04d}.xml")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(xml_content)

        # 2-5. parsed variants
        try:
            from server.parser.structured_parser import (
                StructuredXmlParser,
                hierarchy_parse,
                indent_xml,
            )

            parser = StructuredXmlParser()
            parsed = parser.parse(xml_content)

            if parsed:
                # 2. parsed (bounds + index)
                parsed_path = os.path.join(xml_dir, f"{step:04d}_parsed.xml")
                with open(parsed_path, "w", encoding="utf-8") as f:
                    f.write(parsed)

                # 3. hierarchy (structure only)
                hierarchy = hierarchy_parse(xml_content)
                if hierarchy:
                    hierarchy_path = os.path.join(xml_dir, f"{step:04d}_hierarchy.xml")
                    with open(hierarchy_path, "w", encoding="utf-8") as f:
                        f.write(hierarchy)

                # 4. encoded (bounds removed)
                encoded = parser._clear_bounds(parser.views)
                encoded_str = ET.tostring(ET.fromstring(encoded), encoding="unicode")
                encoded_path = os.path.join(xml_dir, f"{step:04d}_encoded.xml")
                with open(encoded_path, "w", encoding="utf-8") as f:
                    f.write(encoded_str)

                # 5. pretty (encoded pretty-print)
                pretty = indent_xml(encoded_str)
                pretty_path = os.path.join(xml_dir, f"{step:04d}_pretty.xml")
                with open(pretty_path, "w", encoding="utf-8") as f:
                    f.write(pretty)
        except Exception as e:
            logger.warning(f"XML parsing failed for step {step}: {e}")

        self.step_count += 1
        return raw_path

    def log_event(self, event: dict):
        """Append an event to the events JSONL file."""
        path = os.path.join(self.session_dir, "events.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def log_external_app(self, payload: dict):
        """Log external app detection event."""
        event = {"type": "external_app", "step": self.step_count, **payload}
        self.log_event(event)
        self._increment_metadata("external_app_events")

    def finalize_session(self):
        """Finalize session metadata."""
        meta_path = os.path.join(self.session_dir, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            meta["completed_at"] = datetime.now().isoformat()
            meta["total_steps"] = self.step_count
            self._write_metadata(meta)
        logger.info(f"Session finalized: {self.step_count} steps")

    def _write_metadata(self, meta: dict):
        meta_path = os.path.join(self.session_dir, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def save_page_graph(self, graph_data: dict) -> str:
        """Save page graph JSON. Returns file path."""
        path = os.path.join(self.session_dir, "page_graph.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, indent=2, ensure_ascii=False)
        return path

    def _increment_metadata(self, key: str):
        meta_path = os.path.join(self.session_dir, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            meta[key] = meta.get(key, 0) + 1
            self._write_metadata(meta)
