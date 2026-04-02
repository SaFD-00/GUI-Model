"""Tests for server.collector — main collection orchestration (integration)."""

import re
from unittest.mock import MagicMock, patch

import pytest

from server.actions import Tap
from server.collector import Collector
from tests.fixtures.xml_samples import SIMPLE_XML


def _make_xml_signal(xml=SIMPLE_XML, pkg="com.test.app", is_first=False):
    return ("xml", xml, {
        "top_package": pkg,
        "target_package": pkg,
        "is_first_screen": is_first,
    })


def _make_collector(mock_adb, signals, max_steps=10):
    """Create a Collector with all dependencies mocked."""
    from server.explorer import SmartExplorer
    from server.server import CollectionServer
    from server.storage import DataWriter

    mock_explorer = MagicMock(spec=SmartExplorer)
    mock_explorer.select_action.return_value = Tap(x=500, y=500, element_index=0)
    mock_explorer.has_left_app.return_value = False

    mock_server = MagicMock(spec=CollectionServer)
    mock_server.is_client_connected.return_value = True
    mock_server.wait_for_package.return_value = "com.test.app"
    mock_server.get_latest_signal.side_effect = signals

    mock_writer = MagicMock(spec=DataWriter)
    mock_writer.step_count = 0
    mock_writer.save_xml.return_value = "/tmp/xml/0000.xml"
    mock_writer.save_screenshot.return_value = "/tmp/screenshots/0000.png"

    collector = Collector(
        adb=mock_adb,
        explorer=mock_explorer,
        server=mock_server,
        writer=mock_writer,
        max_steps=max_steps,
        action_delay=0,
        xml_timeout=0.1,
    )

    return collector, mock_explorer, mock_server, mock_writer


@pytest.mark.integration
class TestRunSessionHappyPath:
    @patch("server.collector.time.sleep")
    def test_three_steps(self, mock_sleep, mock_adb):
        signals = [
            _make_xml_signal(),
            _make_xml_signal(),
            _make_xml_signal(),
            ("finish", None, None),
        ]
        collector, explorer, server, writer = _make_collector(mock_adb, signals)

        session_id = collector.run(package="com.test.app")

        assert session_id != ""
        assert "com.test.app" in session_id
        assert re.search(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", session_id)
        assert explorer.select_action.call_count == 3
        assert explorer.execute_action.call_count == 3
        assert writer.save_xml.call_count == 3
        writer.finalize_session.assert_called_once()


@pytest.mark.integration
class TestRunSessionNoChangeRetry:
    @patch("server.collector.time.sleep")
    def test_retry_then_continue(self, mock_sleep, mock_adb):
        signals = [
            _make_xml_signal(),           # step 0: normal
            ("no_change", None, None),    # step 1: retry 1
            ("no_change", None, None),    # step 1: retry 2
            _make_xml_signal(),           # step 2: normal
            ("finish", None, None),
        ]
        collector, explorer, server, writer = _make_collector(mock_adb, signals)

        session_id = collector.run(package="com.test.app")

        assert session_id != ""
        # Element exclusion should have been called
        assert explorer.exclude_element.call_count >= 1


@pytest.mark.integration
class TestRunSessionExternalApp:
    @patch("server.collector.time.sleep")
    def test_recovery_escalation(self, mock_sleep, mock_adb):
        # 4 external_app signals: first 3 → return_to_app, 4th → recover
        signals = [
            _make_xml_signal(),
            ("external_app", None, {"detected_package": "com.other"}),
            ("external_app", None, {"detected_package": "com.other"}),
            ("external_app", None, {"detected_package": "com.other"}),
            ("external_app", None, {"detected_package": "com.other"}),
            _make_xml_signal(),
            ("finish", None, None),
        ]
        collector, explorer, server, writer = _make_collector(mock_adb, signals)

        session_id = collector.run(package="com.test.app")

        assert session_id != ""
        assert explorer.return_to_app.call_count == 3
        assert explorer.recover.call_count == 1


@pytest.mark.integration
class TestRunSessionFinish:
    @patch("server.collector.time.sleep")
    def test_finish_signal(self, mock_sleep, mock_adb):
        signals = [
            _make_xml_signal(),
            ("finish", None, None),
        ]
        collector, explorer, server, writer = _make_collector(mock_adb, signals)

        session_id = collector.run(package="com.test.app")

        assert session_id != ""
        writer.finalize_session.assert_called_once()


@pytest.mark.integration
class TestRunSessionTimeout:
    @patch("server.collector.time.sleep")
    def test_max_timeouts(self, mock_sleep, mock_adb):
        # 5 consecutive timeouts (None) should end session
        signals = [None, None, None, None, None]
        collector, explorer, server, writer = _make_collector(mock_adb, signals)

        session_id = collector.run(package="com.test.app")

        assert session_id != ""
        writer.finalize_session.assert_called_once()


@pytest.mark.integration
class TestRunSessionNoConnection:
    @patch("server.collector.time.sleep")
    def test_no_client(self, mock_sleep, mock_adb):
        from server.explorer import SmartExplorer
        from server.server import CollectionServer
        from server.storage import DataWriter

        mock_server = MagicMock(spec=CollectionServer)
        mock_server.is_client_connected.return_value = False

        collector = Collector(
            adb=mock_adb,
            explorer=MagicMock(spec=SmartExplorer),
            server=mock_server,
            writer=MagicMock(spec=DataWriter),
            max_steps=5,
            action_delay=0,
            xml_timeout=0.1,
        )

        session_id = collector.run(package="com.test.app")
        assert session_id == ""
