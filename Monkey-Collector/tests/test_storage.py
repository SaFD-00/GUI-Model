"""Tests for server.storage — DataWriter session data storage."""

import json
import os

import pytest

from server.storage import DataWriter


@pytest.fixture
def writer(tmp_path):
    w = DataWriter(base_dir=str(tmp_path))
    w.init_session("com.test.app_2026-04-02_10-00-00", "com.test.app")
    return w


class TestInitSession:
    def test_creates_directories(self, writer, tmp_path):
        session_dir = tmp_path / "com.test.app_2026-04-02_10-00-00"
        assert (session_dir / "screenshots").is_dir()
        assert (session_dir / "xml").is_dir()

    def test_writes_metadata(self, writer, tmp_path):
        meta_path = tmp_path / "com.test.app_2026-04-02_10-00-00" / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["session_id"] == "com.test.app_2026-04-02_10-00-00"
        assert meta["package"] == "com.test.app"
        assert meta["started_at"] is not None
        assert meta["completed_at"] is None
        assert meta["total_steps"] == 0
        assert meta["external_app_events"] == 0


class TestSaveScreenshot:
    def test_save(self, writer, tmp_path):
        path = writer.save_screenshot(b"\x89PNG_fake_data")
        assert "0000.png" in path
        saved = (tmp_path / "com.test.app_2026-04-02_10-00-00" / "screenshots" / "0000.png").read_bytes()
        assert saved == b"\x89PNG_fake_data"


class TestSaveXml:
    def test_increments_step(self, writer, tmp_path):
        assert writer.step_count == 0

        path1 = writer.save_xml("<xml>first</xml>")
        assert "0000.xml" in path1
        assert writer.step_count == 1

        path2 = writer.save_xml("<xml>second</xml>")
        assert "0001.xml" in path2
        assert writer.step_count == 2

        content = (tmp_path / "com.test.app_2026-04-02_10-00-00" / "xml" / "0001.xml").read_text()
        assert content == "<xml>second</xml>"

    def test_saves_five_variants(self, writer, tmp_path):
        """save_xml should produce 5 XML files for valid uiautomator input."""
        from tests.fixtures.xml_samples import SIMPLE_XML

        writer.save_xml(SIMPLE_XML)

        xml_dir = tmp_path / "com.test.app_2026-04-02_10-00-00" / "xml"
        assert (xml_dir / "0000.xml").exists()
        assert (xml_dir / "0000_parsed.xml").exists()
        assert (xml_dir / "0000_hierarchy.xml").exists()
        assert (xml_dir / "0000_encoded.xml").exists()
        assert (xml_dir / "0000_pretty.xml").exists()

    def test_encoded_has_no_bounds(self, writer, tmp_path):
        """Encoded XML should have no bounds attributes."""
        import xml.etree.ElementTree as ET

        from tests.fixtures.xml_samples import SIMPLE_XML

        writer.save_xml(SIMPLE_XML)
        xml_dir = tmp_path / "com.test.app_2026-04-02_10-00-00" / "xml"
        encoded = (xml_dir / "0000_encoded.xml").read_text()
        root = ET.fromstring(encoded)
        for el in root.iter():
            assert "bounds" not in el.attrib

    def test_invalid_xml_still_saves_raw(self, writer, tmp_path):
        """Invalid XML should still save raw file without crashing."""
        writer.save_xml("<not valid!!!")
        xml_dir = tmp_path / "com.test.app_2026-04-02_10-00-00" / "xml"
        assert (xml_dir / "0000.xml").exists()
        assert not (xml_dir / "0000_parsed.xml").exists()


class TestLogEvent:
    def test_appends_jsonl(self, writer, tmp_path):
        writer.log_event({"action_type": "tap", "x": 100, "y": 200})
        writer.log_event({"action_type": "swipe", "step": 1})

        events_path = tmp_path / "com.test.app_2026-04-02_10-00-00" / "events.jsonl"
        lines = events_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["action_type"] == "tap"
        assert json.loads(lines[1])["action_type"] == "swipe"


class TestLogExternalApp:
    def test_logs_and_increments(self, writer, tmp_path):
        writer.log_external_app({"detected_package": "com.other"})

        # Check event written
        events_path = tmp_path / "com.test.app_2026-04-02_10-00-00" / "events.jsonl"
        lines = events_path.read_text().strip().split("\n")
        event = json.loads(lines[0])
        assert event["type"] == "external_app"
        assert event["detected_package"] == "com.other"

        # Check metadata counter
        meta = json.loads(
            (tmp_path / "com.test.app_2026-04-02_10-00-00" / "metadata.json").read_text()
        )
        assert meta["external_app_events"] == 1


class TestFinalizeSession:
    def test_updates_metadata(self, writer, tmp_path):
        writer.save_xml("<xml>a</xml>")
        writer.save_xml("<xml>b</xml>")
        writer.finalize_session()

        meta = json.loads(
            (tmp_path / "com.test.app_2026-04-02_10-00-00" / "metadata.json").read_text()
        )
        assert meta["completed_at"] is not None
        assert meta["total_steps"] == 2


class TestMultipleSteps:
    def test_sequential_operations(self, writer, tmp_path):
        for i in range(3):
            writer.save_screenshot(f"png_data_{i}".encode())
            writer.save_xml(f"<xml>step_{i}</xml>")
            writer.log_event({"step": i, "action_type": "tap"})

        assert writer.step_count == 3

        screenshots_dir = tmp_path / "com.test.app_2026-04-02_10-00-00" / "screenshots"
        xml_dir = tmp_path / "com.test.app_2026-04-02_10-00-00" / "xml"
        assert len(list(screenshots_dir.iterdir())) == 3
        # 3 raw files; parsed variants may or may not exist depending on XML validity
        raw_files = [f for f in xml_dir.iterdir() if "_" not in f.stem]
        assert len(raw_files) == 3


class TestFinalizeNoMetadata:
    def test_no_crash(self, tmp_path):
        """finalize_session when metadata.json doesn't exist -> no crash."""
        w = DataWriter(base_dir=str(tmp_path))
        w.session_dir = str(tmp_path / "nonexistent_session")
        os.makedirs(w.session_dir, exist_ok=True)
        # No metadata.json exists
        w.finalize_session()  # should not raise


class TestReinitSession:
    def test_reinit_resets_state(self, tmp_path):
        """Re-initializing session resets step_count."""
        w = DataWriter(base_dir=str(tmp_path))
        w.init_session("session_1", "com.test.app")
        w.save_xml("<xml>a</xml>")
        w.save_xml("<xml>b</xml>")
        assert w.step_count == 2

        w.init_session("session_2", "com.test.app")
        assert w.step_count == 0
        assert "session_2" in w.session_dir


class TestIncrementMetadata:
    def test_increment_twice(self, tmp_path):
        """_increment_metadata twice -> value is 2."""
        w = DataWriter(base_dir=str(tmp_path))
        w.init_session("test_session", "com.test.app")
        w._increment_metadata("external_app_events")
        w._increment_metadata("external_app_events")

        meta_path = tmp_path / "test_session" / "metadata.json"
        meta = json.loads(meta_path.read_text())
        assert meta["external_app_events"] == 2
