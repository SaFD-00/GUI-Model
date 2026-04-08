"""Tests for server.adb — ADB command wrapper."""

import subprocess
from unittest.mock import patch

import pytest

from server.adb import AdbClient, _escape_text_for_adb, _find_adb


class TestEscapeText:
    def test_spaces(self):
        assert _escape_text_for_adb("hello world") == "hello%sworld"

    def test_special_chars(self):
        result = _escape_text_for_adb("test&value")
        assert "\\&" in result

    def test_quotes(self):
        result = _escape_text_for_adb('say "hi"')
        assert '\\"' in result

    def test_no_escaping_needed(self):
        assert _escape_text_for_adb("simple") == "simple"

    def test_empty(self):
        assert _escape_text_for_adb("") == ""


class TestFindAdb:
    def test_found_in_path(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/adb")
        assert _find_adb() == "/usr/bin/adb"

    def test_android_home(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        monkeypatch.setenv("ANDROID_HOME", "/opt/android-sdk")
        monkeypatch.setattr("os.path.isfile", lambda p: "platform-tools/adb" in p)
        assert "platform-tools/adb" in _find_adb()


@pytest.fixture
def mock_subprocess():
    with patch("server.adb.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        yield mock_run


@pytest.fixture
def adb_client(mock_subprocess):
    with patch("server.adb._find_adb", return_value="/usr/bin/adb"):
        return AdbClient()


class TestShell:
    def test_calls_subprocess(self, adb_client, mock_subprocess):
        adb_client.shell("ls /sdcard")
        mock_subprocess.assert_called_once()
        args = mock_subprocess.call_args[0][0]
        assert args == ["/usr/bin/adb", "shell", "ls /sdcard"]

    def test_with_device_serial(self, mock_subprocess):
        with patch("server.adb._find_adb", return_value="/usr/bin/adb"):
            client = AdbClient(device_serial="emulator-5554")
        client.shell("ls")
        args = mock_subprocess.call_args[0][0]
        assert args[:3] == ["/usr/bin/adb", "-s", "emulator-5554"]


class TestAdbCommands:
    def test_get_device_resolution(self, adb_client, mock_subprocess):
        mock_subprocess.return_value.stdout = "Physical size: 1080x1920"
        w, h = adb_client.get_device_resolution()
        assert w == 1080
        assert h == 1920

    def test_tap(self, adb_client, mock_subprocess):
        adb_client.tap(100, 200)
        args = mock_subprocess.call_args[0][0]
        assert "input tap 100 200" in " ".join(args)

    def test_swipe(self, adb_client, mock_subprocess):
        adb_client.swipe(10, 20, 30, 40, 300)
        args = mock_subprocess.call_args[0][0]
        assert "input swipe 10 20 30 40 300" in " ".join(args)

    def test_input_text(self, adb_client, mock_subprocess):
        adb_client.input_text("hello world")
        args = mock_subprocess.call_args[0][0]
        cmd = " ".join(args)
        assert "input text" in cmd
        assert "hello%sworld" in cmd

    def test_input_text_empty(self, adb_client, mock_subprocess):
        result = adb_client.input_text("")
        assert result == ""
        mock_subprocess.assert_not_called()

    def test_press_back(self, adb_client, mock_subprocess):
        adb_client.press_back()
        args = mock_subprocess.call_args[0][0]
        assert "KEYCODE_BACK" in " ".join(args)

    def test_press_home(self, adb_client, mock_subprocess):
        adb_client.press_home()
        args = mock_subprocess.call_args[0][0]
        assert "KEYCODE_HOME" in " ".join(args)

    def test_get_current_package(self, adb_client, mock_subprocess):
        mock_subprocess.return_value.stdout = (
            "    mResumedActivity: ActivityRecord{abc123 u0 com.test.app/.MainActivity t42}"
        )
        result = adb_client.get_current_package()
        assert result == "com.test.app"

    def test_get_current_package_empty(self, adb_client, mock_subprocess):
        mock_subprocess.return_value.stdout = ""
        assert adb_client.get_current_package() == ""

    def test_long_press_uses_swipe(self, adb_client, mock_subprocess):
        adb_client.long_press(100, 200, 1000)
        args = mock_subprocess.call_args[0][0]
        assert "input swipe 100 200 100 200 1000" in " ".join(args)

    def test_launch_app(self, adb_client, mock_subprocess):
        mock_subprocess.return_value.stdout = "priority=0 preferredOrder=0 match=0x00108000 specificIndex=-1 isDefault=true\ncom.test.app/.MainActivity"
        adb_client.launch_app("com.test.app")
        # Should have been called at least twice (resolve + start)
        assert mock_subprocess.call_count >= 2

    def test_clear_text_field(self, adb_client, mock_subprocess):
        adb_client.clear_text_field()
        assert mock_subprocess.call_count == 3  # MOVE_END + Ctrl+A + DEL

    def test_get_current_activity(self, adb_client, mock_subprocess):
        mock_subprocess.return_value.stdout = (
            "    mResumedActivity: ActivityRecord{abc123 u0 com.test.app/.MainActivity t42}"
        )
        result = adb_client.get_current_activity()
        assert result == "com.test.app/.MainActivity"

    def test_get_current_activity_empty(self, adb_client, mock_subprocess):
        mock_subprocess.return_value.stdout = ""
        assert adb_client.get_current_activity() == ""

    def test_get_declared_activities_from_packages_section(self, adb_client, mock_subprocess):
        """Packages section lists ALL manifest activities (not just intent-filtered)."""
        mock_subprocess.return_value.stdout = (
            "Activity Resolver Table:\n"
            "  Non-Data Actions:\n"
            "      android.intent.action.MAIN:\n"
            "        abc1234 com.test.app/.MainActivity filter abc\n"
            "  Receiver Resolver Table:\n"
            "      some.action:\n"
            "\n"
            "Packages:\n"
            "  Package [com.test.app] (abcdef):\n"
            "    userId=10123\n"
            "    activities:\n"
            "      com.test.app/.MainActivity\n"
            "        flags=0x0\n"
            "      com.test.app/.DetailActivity\n"
            "        flags=0x0\n"
            "      com.test.app/.SettingsActivity\n"
            "        flags=0x0\n"
            "    receivers:\n"
            "      com.test.app/.MyReceiver\n"
        )
        result = adb_client.get_declared_activities("com.test.app")
        # Should find all 3 activities from Packages section, not just 1 from Resolver
        assert result == [
            "com.test.app/.DetailActivity",
            "com.test.app/.MainActivity",
            "com.test.app/.SettingsActivity",
        ]

    def test_get_declared_activities_fallback_to_resolver(self, adb_client, mock_subprocess):
        """Falls back to Activity Resolver Table when Packages section is absent."""
        mock_subprocess.return_value.stdout = (
            "Activity Resolver Table:\n"
            "  Non-Data Actions:\n"
            "      android.intent.action.MAIN:\n"
            "        abc1234 com.test.app/.MainActivity filter abc\n"
            "        def5678 com.test.app/.SettingsActivity filter def\n"
            "  Receiver Resolver Table:\n"
            "      some.action:\n"
        )
        result = adb_client.get_declared_activities("com.test.app")
        assert result == ["com.test.app/.MainActivity", "com.test.app/.SettingsActivity"]

    def test_get_declared_activities_empty(self, adb_client, mock_subprocess):
        mock_subprocess.return_value.stdout = ""
        result = adb_client.get_declared_activities("com.test.app")
        assert result == []

    def test_get_declared_activities_exception(self, adb_client, mock_subprocess):
        mock_subprocess.side_effect = Exception("timeout")
        result = adb_client.get_declared_activities("com.test.app")
        assert result == []
