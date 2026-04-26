"""Tests for server.cli — CLI argument parsing."""

from unittest.mock import patch

import pytest


class TestRunArgsParsing:
    def test_defaults(self):
        from server.cli import main

        with patch(
            "sys.argv", ["monkey-collect", "run", "--apps", "all"]
        ), patch("server.cli.cmd_run") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.apps == ["all"]
            assert args.steps == 100
            assert args.seed == 42
            assert args.port == 12345
            assert args.input_mode == "api"
            assert args.new_session is False
            assert args.force is False

    def test_apps_required(self):
        from server.cli import main

        with patch("sys.argv", ["monkey-collect", "run"]), \
                pytest.raises(SystemExit):
            main()

    def test_all_flags(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "run",
            "--apps", "com.test.app", "com.other.app",
            "--steps", "50",
            "--seed", "99",
            "--port", "54321",
            "--new-session",
            "--force",
            "--input-mode", "random",
        ]), patch("server.cli.cmd_run") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.apps == ["com.test.app", "com.other.app"]
            assert args.steps == 50
            assert args.seed == 99
            assert args.port == 54321
            assert args.new_session is True
            assert args.force is True
            assert args.input_mode == "random"

class TestConvertArgsParsing:
    def test_required_args(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "convert",
            "--session", "/data/raw/session1",
            "--output", "/data/output.jsonl",
            "--images-dir", "/data/images",
        ]), patch("server.cli.cmd_convert") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.session == "/data/raw/session1"
            assert args.output == "/data/output.jsonl"
            assert args.images_dir == "/data/images"

    def test_with_label(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "convert",
            "--session", "/data/raw/s1",
            "--output", "/out.jsonl",
            "--images-dir", "/img",
            "--label", "5",
        ]), patch("server.cli.cmd_convert") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.label == 5


class TestConvertAllArgsParsing:
    def test_required_args(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "convert-all",
            "--raw-dir", "/data/raw",
            "--output", "/data/output.jsonl",
            "--images-dir", "/data/images",
        ]), patch("server.cli.cmd_convert_all") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.raw_dir == "/data/raw"
            assert args.output == "/data/output.jsonl"
            assert args.images_dir == "/data/images"


class TestNoCommand:
    def test_no_command_exits(self):
        """No command -> SystemExit(1)."""
        from server.cli import main

        with patch("sys.argv", ["monkey-collect"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


class TestRunArgsDefaults:
    def test_default_output(self):
        """Default output value."""
        from server.cli import main

        with patch(
            "sys.argv", ["monkey-collect", "run", "--apps", "all"]
        ), patch("server.cli.cmd_run") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.output == "data/raw"
            assert args.delay == 1500


class TestSyncInstalledArgsParsing:
    def test_defaults(self):
        from server.cli import main

        with patch(
            "sys.argv", ["monkey-collect", "sync-installed"]
        ), patch("server.cli.cmd_sync_installed") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.apps_csv == "catalog/apps.csv"

    def test_custom_apps_csv(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "sync-installed",
            "--apps-csv", "/tmp/apps.csv",
        ]), patch("server.cli.cmd_sync_installed") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.apps_csv == "/tmp/apps.csv"

class TestResetArgsParsing:
    def test_all_flag(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "reset", "--all", "--yes",
        ]), patch("server.cli.cmd_reset") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.all is True
            assert args.yes is True
            assert args.dry_run is False
            assert args.output == "data/raw"
            assert args.apps is None

    def test_packages(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "reset",
            "--apps", "com.a,com.b",
            "--output", "/tmp/out",
            "--dry-run",
        ]), patch("server.cli.cmd_reset") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.apps == "com.a,com.b"
            assert args.output == "/tmp/out"
            assert args.dry_run is True


class TestPageMapArgsParsing:
    def test_required_args(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "page-map",
            "--session", "/data/raw/s1",
        ]), patch("server.cli.cmd_page_map") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.session == "/data/raw/s1"
            assert args.threshold == 0.85
            assert args.output is None
            assert args.no_open is False

    def test_threshold_and_no_open(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "page-map",
            "--session", "/data/raw/s1",
            "--threshold", "0.5",
            "--output", "/tmp/graph.html",
            "--no-open",
        ]), patch("server.cli.cmd_page_map") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.threshold == 0.5
            assert args.output == "/tmp/graph.html"
            assert args.no_open is True


class TestPageMapAllArgsParsing:
    def test_defaults(self):
        from server.cli import main

        with patch("sys.argv", ["monkey-collect", "page-map-all"]), \
                patch("server.cli.cmd_page_map_all") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.raw_dir == "data/raw"
            assert args.threshold == 0.85
            assert args.no_open is False


class TestRegenerateArgsParsing:
    def test_defaults(self):
        from server.cli import main

        with patch("sys.argv", ["monkey-collect", "regenerate"]), \
                patch("server.cli.cmd_regenerate") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.raw_dir == "data/raw"

    def test_custom_raw_dir(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "regenerate",
            "--raw-dir", "/data/other",
        ]), patch("server.cli.cmd_regenerate") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.raw_dir == "/data/other"
