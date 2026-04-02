"""Tests for server.storage — DataWriter session data storage."""

import json

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
        assert len(list(xml_dir.iterdir())) == 3
