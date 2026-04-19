"""Tests for server.cli — CLI argument parsing."""

from unittest.mock import patch

import pytest


class TestRunArgsParsing:
    def test_defaults(self):
        from server.cli import main

        with patch("sys.argv", ["monkey-collect", "run"]), patch("server.cli.cmd_run") as mock_cmd:
                main()
                args = mock_cmd.call_args[0][0]
                assert args.app is None
                assert args.steps == 100
                assert args.seed == 42
                assert args.port == 12345
                assert args.single is False
                assert args.input_mode == "api"

    def test_all_flags(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "run",
            "--app", "com.test.app",
            "--steps", "50",
            "--seed", "99",
            "--port", "54321",
            "--single",
            "--input-mode", "random",
        ]), patch("server.cli.cmd_run") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.app == "com.test.app"
            assert args.steps == 50
            assert args.seed == 99
            assert args.port == 54321
            assert args.single is True
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
    def test_default_output_device(self):
        """Default output and device values."""
        from server.cli import main

        with patch("sys.argv", ["monkey-collect", "run"]), patch("server.cli.cmd_run") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.output == "data/raw"
            assert args.device is None
            assert args.delay == 1500


class TestCollectBatchArgsParsing:
    def test_required_args(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "collect-batch",
            "--avds", "monkey-0,monkey-1",
        ]), patch("server.cli.cmd_collect_batch") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.avds == "monkey-0,monkey-1"
            assert args.apps_csv == "apps.csv"
            assert args.apks_dir == "apks"
            assert args.parallel == 2
            assert args.host_port_base == 6000
            assert args.boot_timeout == 180.0
            assert args.uninstall_after is False
            assert args.dry_run is False
            assert args.categories is None
            assert args.priorities is None

    def test_all_flags(self):
        from server.cli import main

        with patch("sys.argv", [
            "monkey-collect", "collect-batch",
            "--apps-csv", "/tmp/apps.csv",
            "--apks-dir", "/tmp/apks",
            "--avds", "a,b,c",
            "--parallel", "3",
            "--categories", "Shopping,Media",
            "--priorities", "High",
            "--output", "/tmp/out",
            "--steps", "50",
            "--seed", "7",
            "--delay", "500",
            "--input-mode", "random",
            "--host-port-base", "7000",
            "--boot-timeout", "60",
            "--uninstall-after",
            "--new-session",
            "--dry-run",
        ]), patch("server.cli.cmd_collect_batch") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.apps_csv == "/tmp/apps.csv"
            assert args.apks_dir == "/tmp/apks"
            assert args.avds == "a,b,c"
            assert args.parallel == 3
            assert args.categories == "Shopping,Media"
            assert args.priorities == "High"
            assert args.steps == 50
            assert args.input_mode == "random"
            assert args.host_port_base == 7000
            assert args.boot_timeout == 60
            assert args.uninstall_after is True
            assert args.new_session is True
            assert args.dry_run is True

    def test_avds_is_required(self):
        from server.cli import main

        with patch("sys.argv", ["monkey-collect", "collect-batch"]), \
                pytest.raises(SystemExit):
            main()


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
