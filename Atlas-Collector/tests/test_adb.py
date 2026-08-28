"""AdbClient contract tests. No device, no real subprocess, ever.

The entire subprocess layer is replaced by :class:`FakeDevice`, which models the
two facts that made the old ``dump_ui`` dangerous:

1. ``/sdcard/window_dump.xml`` PERSISTS between calls (live-confirmed on the
   target Pixel 6), so a previous screen is always sitting there.
2. A failing ``uiautomator dump`` does not necessarily print the token ``ERROR``
   -- a swallowed timeout, a permission blip and a secure-window refusal can all
   come back quiet.

Put together, the old code ``cat``-ed the PREVIOUS screen back as a fresh
observation. Everything below pins that it now raises instead: a stale buffer is
corpus corruption nothing downstream can detect, so it must never be returned.

Escaping is pinned as exact literal output. The expectations are hardcoded, NOT
recomputed with ``shlex.quote`` -- a test that re-derives the implementation
proves only that the implementation equals itself.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

import pytest

from atlas_collector import adb
from atlas_collector.adb import (
    REMOTE_DUMP_PATH,
    AdbClient,
    AdbError,
    TextEscapeError,
    UiDumpError,
    escape_text_for_adb,
)

SERIAL = "FAKE19101FDF"
FAKE_ADB = "/fake/sdk/platform-tools/adb"

SCREEN_A = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<hierarchy rotation="0">'
    '<node index="0" text="Screen A" bounds="[0,0][1080,2400]" />'
    "</hierarchy>"
)
#: The previous screen, left behind on the device by an earlier dump.
SCREEN_STALE = SCREEN_A.replace("Screen A", "Screen STALE")
TTY_BANNER = "UI hierarchy dumped to: /dev/tty"
# Note the misspelling: Android really does print "hierchary" for the file form.
FILE_BANNER = f"UI hierchary dumped to: {REMOTE_DUMP_PATH}"


# ---------------------------------------------------------------------------
# The fake device
# ---------------------------------------------------------------------------


class FakeDevice:
    """Stands in for ``subprocess.run`` against one attached device.

    Every knob below exists to reproduce a real failure that was observed or is
    plausible on the Pixel 6; nothing here talks to hardware.
    """

    def __init__(self) -> None:
        #: Every adb invocation, in order, with the ``-s <serial>`` prefix removed.
        self.commands: list[str] = []
        #: The device filesystem. Seed REMOTE_DUMP_PATH to model persistence.
        self.remote: dict[str, str] = {}
        self.serials: list[str] = [SERIAL]

        # -- knobs -----------------------------------------------------------
        self.tty_stdout: str = f"{SCREEN_A}\n{TTY_BANNER}\n"
        self.tty_returncode: int = 0
        self.tty_timeout: bool = False
        self.file_dump_output: str = f"{FILE_BANNER}\n"
        #: What `uiautomator dump <path>` writes; None means it writes nothing.
        self.file_dump_writes: str | None = SCREEN_A
        self.file_dump_timeout: bool = False
        #: False models an `rm -f` that reports success but removes nothing.
        self.rm_works: bool = True

    # -- plumbing ------------------------------------------------------------

    @staticmethod
    def _payload(value: str, text: bool) -> Any:
        return value if text else value.encode()

    def _done(
        self, argv: list[str], rc: int = 0, stdout: str = "", stderr: str = "", *, text: bool = False
    ) -> subprocess.CompletedProcess[Any]:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=rc,
            stdout=self._payload(stdout, text),
            stderr=self._payload(stderr, text),
        )

    def run(
        self,
        argv: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        assert argv[0] == FAKE_ADB, f"unexpected binary: {argv[0]!r}"
        args = list(argv[1:])
        if args[:1] == ["-s"]:
            assert args[1] in self.serials, f"command addressed to a stale serial: {args[1]!r}"
            args = args[2:]
        self.commands.append(" ".join(args))

        if args == ["devices"]:
            listing = "List of devices attached\n" + "".join(f"{s}\tdevice\n" for s in self.serials)
            return self._done(argv, 0, listing, text=text)
        if not args:
            raise AssertionError("FakeDevice got an empty adb invocation")
        if args[0] in {"kill-server", "start-server", "wait-for-device"}:
            return self._done(argv, 0, "", text=text)
        if args[0] == "shell":
            rc, out = self._shell(" ".join(args[1:]))
            return self._done(argv, rc, out, text=text)
        if args[0] == "exec-out":
            return self._exec_out(argv, " ".join(args[1:]), text=text)
        raise AssertionError(f"FakeDevice got an unexpected adb call: {args!r}")

    # -- command handlers ----------------------------------------------------

    def _shell(self, cmd: str) -> tuple[int, str]:
        if cmd.startswith("rm -f "):
            if self.rm_works:
                self.remote.pop(cmd.split()[2], None)
            return 0, ""
        if cmd.startswith("[ -e "):
            return 0, "EXISTS\n" if cmd.split()[2] in self.remote else "GONE\n"
        if cmd.startswith("uiautomator dump "):
            if self.file_dump_timeout:
                raise subprocess.TimeoutExpired(cmd, 30)
            if self.file_dump_writes is not None:
                self.remote[cmd.split()[2]] = self.file_dump_writes
            return 0, self.file_dump_output
        if cmd == "wm size":
            return 0, "Physical size: 1080x2400\n"
        return 0, ""

    def _exec_out(
        self, argv: list[str], cmd: str, *, text: bool
    ) -> subprocess.CompletedProcess[Any]:
        if cmd == "uiautomator dump /dev/tty":
            if self.tty_timeout:
                raise subprocess.TimeoutExpired(cmd, 30)
            return self._done(argv, self.tty_returncode, self.tty_stdout, text=text)
        if cmd.startswith("cat "):
            path = cmd.split()[1]
            if path not in self.remote:
                return self._done(
                    argv, 1, "", f"cat: {path}: No such file or directory", text=text
                )
            return self._done(argv, 0, self.remote[path], text=text)
        raise AssertionError(f"FakeDevice got an unexpected exec-out: {cmd!r}")


@pytest.fixture
def device(monkeypatch: pytest.MonkeyPatch) -> FakeDevice:
    dev = FakeDevice()
    # adb.py holds `import subprocess`, so the module attribute is the only seam.
    monkeypatch.setattr(adb, "_find_adb", lambda: FAKE_ADB)
    monkeypatch.setattr(adb.subprocess, "run", dev.run)
    return dev


@pytest.fixture
def client(device: FakeDevice) -> AdbClient:
    return AdbClient()


def _index_of(commands: list[str], prefix: str) -> int:
    for i, cmd in enumerate(commands):
        if cmd.startswith(prefix):
            return i
    raise AssertionError(f"no command starting with {prefix!r} in {commands!r}")


# ---------------------------------------------------------------------------
# Construction and serial resolution (the constructor must not touch USB)
# ---------------------------------------------------------------------------


def test_construction_touches_no_usb(device):
    AdbClient()
    assert device.commands == []


def test_serial_is_resolved_on_the_first_command(client, device):
    client.shell("echo hi")
    assert device.commands == ["devices", "shell echo hi"]


def test_reconnect_redetects_an_unpinned_serial(client, device):
    assert client.serial == SERIAL
    device.serials = ["OTHER-SERIAL"]
    assert client.reconnect() == "OTHER-SERIAL"
    assert "kill-server" in device.commands


# ---------------------------------------------------------------------------
# (1) dump freshness
# ---------------------------------------------------------------------------


def test_ui_dump_error_is_an_adb_error():
    # A sibling class would slip through the M4 loop's `except AdbError`.
    assert issubclass(UiDumpError, AdbError)
    assert issubclass(UiDumpError, RuntimeError)


def test_dump_ui_uses_the_transfer_free_stdout_path(client, device):
    assert client.dump_ui() == SCREEN_A
    assert "exec-out uiautomator dump /dev/tty" in device.commands
    # The happy path must never go near the shared file: no file, no staleness.
    assert not any(REMOTE_DUMP_PATH in cmd for cmd in device.commands)


@pytest.mark.parametrize(
    "raw",
    [
        f"{SCREEN_A}\n{TTY_BANNER}\n",  # banner after the XML
        f"{TTY_BANNER}\n{SCREEN_A}",  # banner before it
        f"{TTY_BANNER}\n{SCREEN_A}\n{TTY_BANNER}\n",  # both sides
        f"{SCREEN_A}\r\n{TTY_BANNER}\r\n",  # CRLF-ish tail
    ],
    ids=["banner-after", "banner-before", "banner-both", "crlf-tail"],
)
def test_dump_ui_slices_the_banner_off_structurally(client, device, raw):
    device.tty_stdout = raw
    assert client.dump_ui() == SCREEN_A


def test_stale_dump_is_refused_when_rm_is_a_noop(client, device):
    """The discriminating regression test for the HIGH finding.

    The stale file is present, ``rm -f`` reports success but removes nothing, and
    the dump fails QUIETLY (exit 0, no ``ERROR`` token, writes nothing). Under
    exactly these conditions ``cat`` would return well-formed, non-empty XML of
    the PREVIOUS screen -- so the non-empty and well-formed checks both pass and
    only the post-``rm`` existence guard can catch it.
    """
    device.remote[REMOTE_DUMP_PATH] = SCREEN_STALE
    device.tty_stdout = "ERROR: could not get idle state.\n"
    device.rm_works = False
    device.file_dump_output = ""
    device.file_dump_writes = None

    with pytest.raises(UiDumpError) as excinfo:
        client.dump_ui()

    message = str(excinfo.value)
    assert "still exists after" in message
    assert "Screen STALE" not in message
    assert not any(cmd.startswith("exec-out cat") for cmd in device.commands), (
        "the guard must fire BEFORE anything reads the shared file"
    )


def test_stale_dump_is_refused_when_the_dump_fails_quietly(client, device):
    """Same corruption, other half of the guard: rm works, the dump stays quiet."""
    device.remote[REMOTE_DUMP_PATH] = SCREEN_STALE
    device.tty_stdout = "ERROR: could not get idle state.\n"
    device.file_dump_output = ""  # no "dumped to", and no "ERROR" either
    device.file_dump_writes = None

    with pytest.raises(UiDumpError) as excinfo:
        client.dump_ui()

    assert "did not report success" in str(excinfo.value)
    assert not any(cmd.startswith("exec-out cat") for cmd in device.commands)


def test_dump_ui_never_returns_the_previous_screen(client, device):
    device.tty_returncode = 1  # force the file fallback
    device.tty_stdout = ""
    assert client.dump_ui() == SCREEN_A
    assert device.remote[REMOTE_DUMP_PATH] == SCREEN_A  # the file persists...

    device.rm_works = False  # ...and the next dump fails without clearing it
    device.file_dump_output = ""
    device.file_dump_writes = None
    with pytest.raises(UiDumpError):
        client.dump_ui()


def test_file_fallback_clears_the_remote_path_before_dumping(client, device):
    device.remote[REMOTE_DUMP_PATH] = SCREEN_STALE
    device.tty_returncode = 1
    device.tty_stdout = ""

    assert client.dump_ui() == SCREEN_A

    order = device.commands
    assert (
        _index_of(order, "shell rm -f")
        < _index_of(order, "shell [ -e")
        < _index_of(order, "shell uiautomator dump")
        < _index_of(order, "exec-out cat")
    )


def test_a_swallowed_timeout_never_yields_a_buffer(client, device):
    device.tty_timeout = True
    device.file_dump_timeout = True

    with pytest.raises(UiDumpError) as excinfo:
        client.dump_ui()

    message = str(excinfo.value)
    assert "both dump paths failed" in message
    assert "timed out" in message


def test_both_paths_failing_names_both(client, device):
    device.tty_stdout = "ERROR: could not get idle state.\n"
    device.file_dump_output = "ERROR: could not get idle state.\n"
    device.file_dump_writes = None

    with pytest.raises(UiDumpError) as excinfo:
        client.dump_ui()

    message = str(excinfo.value)
    assert "/dev/tty" in message
    assert REMOTE_DUMP_PATH in message


def test_a_dump_that_reports_success_is_not_rejected_for_saying_error(client, device):
    # The mirror of the original bug would be a too-strict gate: rejecting a good
    # dump because its output happens to contain the substring "ERROR".
    device.tty_returncode = 1  # force the file fallback
    device.tty_stdout = ""
    device.file_dump_output = f"WARN: ERROR_PRONE_SURFACE ignored\n{FILE_BANNER}\n"

    assert client.dump_ui() == SCREEN_A


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "no output at all"),
        ("   \n\n", "no output at all"),
        ("ERROR: could not get idle state.\n", "returned no XML"),
        (SCREEN_A[: SCREEN_A.index("</hierarchy>")], "truncated"),
        ('<?xml version="1.0"?><hierarchy><node></hierarchy>', "not well-formed"),
    ],
    ids=["empty", "blank", "no-xml", "truncated", "malformed"],
)
def test_unusable_dump_output_is_rejected_by_the_named_check(client, device, raw, reason):
    device.tty_stdout = raw
    device.file_dump_output = raw
    device.file_dump_writes = raw or None

    with pytest.raises(UiDumpError) as excinfo:
        client.dump_ui()

    assert reason in str(excinfo.value)


def test_a_node_less_hierarchy_is_returned_but_warned_about(client, device):
    empty = '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"></hierarchy>'
    device.tty_stdout = f"{empty}\n{TTY_BANNER}\n"

    warnings: list[str] = []
    sink = adb.logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        assert client.dump_ui() == empty
    finally:
        adb.logger.remove(sink)

    # Well-formed but node-less is a legitimate (if odd) observation, so it is
    # returned -- but it must never pass silently.
    assert any("no <node> children" in warning for warning in warnings)


# ---------------------------------------------------------------------------
# (2) escape_text_for_adb -- exact, hardcoded expectations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello", "hello"),
        ("Atlas", "Atlas"),
        ("", "''"),
        ("hello world", "hello%sworld"),
        ("  leading and trailing  ", "%s%sleading%sand%strailing%s%s"),
        ("100%", "100%"),
        ("%", "%"),
        ("a%b", "a%b"),
        ("50% off", "50%%soff"),
        ("email@example.com", "email@example.com"),
        ("path/to/file.txt", "path/to/file.txt"),
        ("3+4=7", "3+4=7"),
        ("$(whoami)", "'$(whoami)'"),
        ("rm -rf /; echo pwned", "'rm%s-rf%s/;%secho%spwned'"),
        ("Wi-Fi & data", "'Wi-Fi%s&%sdata'"),
        ("a'b", '\'a\'"\'"\'b\''),
        ("`$\"'\\&|;<>()", "'`$\"'\"'\"'\\&|;<>()'"),
        ("smile \U0001f600", "'smile%s\U0001f600'"),
    ],
    ids=[
        "plain-word",
        "capitalised-word",
        "empty",
        "phrase-with-space",
        "runs-of-spaces",
        "trailing-percent",
        "bare-percent",
        "percent-mid-word",
        "percent-then-space",
        "at-sign",
        "slashes",
        "plus-equals",
        "command-substitution",
        "shell-injection-attempt",
        "ampersand",
        "single-quote",
        "shell-metacharacters",
        "astral-emoji",
    ],
)
def test_escape_pins_exact_output(text, expected):
    assert escape_text_for_adb(text) == expected


def test_escaped_text_is_exactly_one_shell_word_that_round_trips():
    # Property check in the other direction from shlex.quote: whatever we emit
    # must survive the device shell as ONE argument whose only transformation is
    # the documented " " -> "%s".
    for text in ["hello", "hello world", "`$\"'\\&|;<>()", "rm -rf /; echo pwned", "50% off"]:
        escaped = escape_text_for_adb(text)
        assert shlex.split(escaped) == [text.replace(" ", "%s")]


@pytest.mark.parametrize(
    "text",
    ["tab\there", "line1\nline2", "cr\rhere", "nul\x00here", "del\x7fhere", "vertical\x0btab"],
    ids=["tab", "newline", "cr", "nul", "del", "vtab"],
)
def test_escape_rejects_control_characters(text):
    # The old escaper emitted backslash+control here: backslash-newline is an sh
    # LINE CONTINUATION, so 'line1\nline2' reached the device as 'line1line2',
    # and a literal tab survived into `input text`, which splits on it.
    with pytest.raises(TextEscapeError) as excinfo:
        escape_text_for_adb(text)
    assert "control character" in str(excinfo.value)


@pytest.mark.parametrize(
    "text",
    ["100%s", "%s", "a %s b", "%s%s", "trailing %s"],
)
def test_escape_rejects_a_literal_percent_s(text):
    # '%s' is the space escape `input text` consumes; the old escaper passed
    # '100%s' through untouched, so it arrived on the device as '100 '.
    with pytest.raises(TextEscapeError) as excinfo:
        escape_text_for_adb(text)
    assert "%s" in str(excinfo.value)


def test_escape_never_emits_a_backslash_newline_or_raw_control_char():
    for text in ["hello world", "50% off", "`$\"'\\&|;<>()", "a'b"]:
        escaped = escape_text_for_adb(text)
        assert "\\\n" not in escaped
        assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in escaped)


# ---------------------------------------------------------------------------
# The typed action that consumes the escaper
# ---------------------------------------------------------------------------


def test_text_sends_the_escaped_form_but_the_original_is_what_gets_recorded(client, device):
    client.text("hello world")
    assert device.commands[-1] == "shell input text hello%sworld"
    # The EXP08 triple must carry the ORIGINAL text; the escaped form is
    # transport only, and the two are demonstrably not the same string.
    assert escape_text_for_adb("hello world") != "hello world"


def test_text_propagates_the_escape_error_without_touching_the_device(client, device):
    assert client.serial == SERIAL  # resolve the serial first; only `text` is at stake
    device.commands.clear()
    with pytest.raises(TextEscapeError):
        client.text("line1\nline2")
    assert device.commands == []
