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
            assert args.delay == 1000
