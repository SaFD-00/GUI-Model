"""Tests for monkey_collector.cli — CLI argument parsing.

Host-pull rebuild pending: every device-push subcommand was torn down along
with the modules it depended on. Only the no-command fallback survives.
"""

from unittest.mock import patch

import pytest


class TestNoCommand:
    def test_no_command_exits(self):
        """No command -> SystemExit(1)."""
        from monkey_collector.cli import main

        with patch("sys.argv", ["monkey-collect"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
