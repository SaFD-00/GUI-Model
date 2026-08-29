"""AdbClient contract tests. No device, no real subprocess, ever.

Ported from ``Atlas-Collector/tests/test_adb.py`` (the FakeDevice section below
is that file, renamed to this package) and merged with the surviving cases from
Monkey-Collector's pre-host-pull ``tests/integration/test_adb.py`` -- the ones
that still exercise a method that exists in the merged client. Cases that
pinned removed API (``_escape_text_for_adb``, ``_resolve_avd_serial``, the
no-arg ``AdbClient()`` autodetecting an AVD, ``get_device_resolution``,
``get_current_package``, the old two-call ``launch_app``) are gone: there is
nothing left in the client for them to protect.

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

from monkey_collector import adb
from monkey_collector.adb import (
    CLEAN_LAUNCH_FLAGS,
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
        #: What `cmd package resolve-activity --brief` prints for the launcher
        #: query. None models a package with no launcher icon, which resolves to
        #: ANOTHER package's resolver activity rather than to nothing.
        self.launcher_component: str | None = "com.test.app/.Main"
        #: Non-empty models `am start` refusing (it reports failure on stdout,
        #: with returncode 0).
        self.am_start_error: str = ""
        #: `dumpsys activity activities | grep ResumedActivity` output. None
        #: models grep finding no match, which a REAL device shell reports as a
        #: nonzero exit -- not empty stdout at exit 0.
        self.resumed_activity_output: str | None = None
        #: `dumpsys input_method` output (no grep involved, always exit 0).
        self.input_method_dump: str = ""
        #: `dumpsys package <pkg>` output (no grep involved, always exit 0).
        self.package_dump_output: str = ""

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
        if cmd.startswith("dumpsys activity activities"):
            # Models `... | grep ResumedActivity`: no match is a NONZERO exit.
            if self.resumed_activity_output is None:
                return 1, ""
            return 0, self.resumed_activity_output
        if cmd == "dumpsys input_method":
            return 0, self.input_method_dump
        if cmd.startswith("dumpsys package "):
            return 0, self.package_dump_output
        if cmd.startswith("cmd package resolve-activity"):
            component = self.launcher_component or "android/com.android.internal.app.ResolverActivity"
            return 0, f"priority=0 preferredOrder=0 match=0x108000 isDefault=true\n{component}\n"
        if cmd.startswith("am start "):
            if self.am_start_error:
                return 0, f"Starting: Intent {{ ... }}\nError: {self.am_start_error}\n"
            return 0, "Starting: Intent { ... }\n"
        if cmd.startswith("monkey -p "):
            return 0, "Events injected: 1\n"
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


def _shell_commands(device: FakeDevice) -> list[str]:
    return [c[len("shell ") :] for c in device.commands if c.startswith("shell ")]


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
# Locating the adb binary
# ---------------------------------------------------------------------------


class TestFindAdb:
    def test_found_in_path(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/adb")
        assert adb._find_adb() == "/usr/bin/adb"

    def test_android_home(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        monkeypatch.setenv("ANDROID_HOME", "/opt/android-sdk")
        monkeypatch.setattr("os.path.isfile", lambda p: "platform-tools/adb" in p)
        assert "platform-tools/adb" in adb._find_adb()


# ---------------------------------------------------------------------------
# (1) dump freshness
# ---------------------------------------------------------------------------


def test_ui_dump_error_is_an_adb_error():
    # A sibling class would slip through the collection loop's `except AdbError`.
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
    # tests/conftest.py disables the "monkey_collector" logger namespace for the
    # whole session so other tests stay quiet; re-enable it just for this test,
    # since it is the WARNING itself under test, not incidental noise.
    adb.logger.enable("monkey_collector")
    try:
        assert client.dump_ui() == empty
    finally:
        adb.logger.disable("monkey_collector")
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
# The typed action that consumes the escaper: text() and its alias input_text()
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


def test_input_text_is_the_primary_name_for_the_inputtext_action(client, device):
    """`InputText.action_type == "input_text"`, so the executor must be named that."""
    client.input_text("hello world")
    assert device.commands[-1] == "shell input text hello%sworld"


def test_input_text_and_text_never_drift_apart(client, device):
    """`text` is an alias of `input_text`, not a second implementation."""
    client.input_text("50% off")
    via_input_text = device.commands[-1]
    device.commands.clear()
    client.text("50% off")
    via_text = device.commands[-1]
    assert via_input_text == via_text


def test_clear_text_field_sends_move_end_select_all_then_delete(client, device):
    client.clear_text_field()
    assert _shell_commands(device) == [
        "input keyevent KEYCODE_MOVE_END",
        "input keycombination 113 29",
        "input keyevent KEYCODE_DEL",
    ]


# ---------------------------------------------------------------------------
# Launching: resumed (monkey) vs cold (force-stop + CLEAR_TASK)
# ---------------------------------------------------------------------------


def test_a_plain_launch_is_monkey_and_stops_nothing(client, device):
    client.launch_app("com.test.app")
    commands = _shell_commands(device)
    assert commands == ["monkey -p com.test.app -c android.intent.category.LAUNCHER 1"]


def test_a_clean_launch_force_stops_then_starts_with_clear_task(client, device):
    client.launch_app("com.test.app", clean=True)
    commands = _shell_commands(device)
    assert commands[0] == "am force-stop com.test.app"
    start = commands[-1]
    assert start.startswith("am start -a android.intent.action.MAIN")
    assert "-n com.test.app/.Main" in start
    # CLEAR_TASK is the half that fixes the reproduced failure, and it is only
    # legal alongside NEW_TASK. Assert the value, not merely that a flag exists.
    assert f"-f {CLEAN_LAUNCH_FLAGS}" in start
    assert int(CLEAN_LAUNCH_FLAGS, 16) == 0x10000000 | 0x00008000
    # force-stop alone was measured to be insufficient (a foreign activity on
    # top of our task survives it), so the start must not be skipped -- and
    # monkey, which merely RESUMES that task, must not be what runs.
    assert not any(c.startswith("monkey") for c in commands)


def test_a_clean_launch_falls_back_to_monkey_when_there_is_no_launcher_activity(client, device):
    device.launcher_component = None
    client.launch_app("com.test.app", clean=True)
    commands = _shell_commands(device)
    assert commands[0] == "am force-stop com.test.app"
    # The resolver activity belongs to another package; adopting it would start
    # a chooser dialog and call it the app.
    assert not any(c.startswith("am start") for c in commands)
    assert commands[-1].startswith("monkey -p com.test.app")


def test_a_clean_launch_falls_back_to_monkey_when_am_start_reports_an_error(client, device):
    device.am_start_error = "Activity not started"
    client.launch_app("com.test.app", clean=True)
    commands = _shell_commands(device)
    assert any(c.startswith("am start") for c in commands)
    assert commands[-1].startswith("monkey -p com.test.app")


def test_the_launcher_component_is_resolved_once_per_package(client, device):
    for _ in range(3):
        client.launch_app("com.test.app", clean=True)
    resolves = [c for c in _shell_commands(device) if c.startswith("cmd package resolve-activity")]
    assert len(resolves) == 1


def test_a_failed_launch_still_raises(client, device):
    device.launcher_component = None
    device.file_dump_output = ""

    def no_such_package(cmd):
        if cmd.startswith("monkey"):
            return 0, "Error: Unable to find package"
        return FakeDevice._shell(device, cmd)

    device._shell = no_such_package  # type: ignore[method-assign]
    with pytest.raises(AdbError):
        client.launch_app("com.test.app", clean=True)


# ---------------------------------------------------------------------------
# Plain gestures and keys -- tap, swipe, long_press, press_back, press_home
# ---------------------------------------------------------------------------


def test_tap_sends_device_pixel_coordinates(client, device):
    client.tap(100, 200)
    assert _shell_commands(device)[-1] == "input tap 100 200"


def test_swipe_sends_both_endpoints_and_duration(client, device):
    client.swipe(10, 20, 30, 40, 300)
    assert _shell_commands(device)[-1] == "input swipe 10 20 30 40 300"


def test_long_press_is_a_zero_movement_swipe(client, device):
    client.long_press(100, 200, 1000)
    assert _shell_commands(device)[-1] == "input swipe 100 200 100 200 1000"


def test_press_back_sends_the_back_keyevent(client, device):
    client.press_back()
    assert _shell_commands(device)[-1] == "input keyevent KEYCODE_BACK"


def test_press_home_sends_the_home_keyevent(client, device):
    client.press_home()
    assert _shell_commands(device)[-1] == "input keyevent KEYCODE_HOME"


def test_hide_keyboard_sends_escape_not_back(client, device):
    # ESC dismisses the IME without popping the activity back stack; a real
    # Back could exit the app to the launcher instead of just closing the IME.
    client.hide_keyboard()
    assert _shell_commands(device)[-1] == "input keyevent KEYCODE_ESCAPE"


# ---------------------------------------------------------------------------
# IME state -- is_keyboard_shown
# ---------------------------------------------------------------------------


def test_is_keyboard_shown_reflects_the_live_dumpsys_fixture(client, device):
    device.input_method_dump = (
        "  mShowRequested=true mShowExplicitlyRequested=false mShowForced=false "
        "mInputShown=true"
    )
    assert client.is_keyboard_shown() is True


def test_is_keyboard_shown_false_when_hidden(client, device):
    device.input_method_dump = (
        "  mShowRequested=false mShowExplicitlyRequested=false mShowForced=false "
        "mInputShown=false"
    )
    assert client.is_keyboard_shown() is False


def test_is_keyboard_shown_swallows_a_dropped_link_to_false(client, device):
    """A wedged `adb shell` on this probe must not stall or crash the loop."""

    def boom(cmd: str) -> tuple[int, str]:
        return 1, "error: closed"

    device._shell = boom  # type: ignore[method-assign]
    assert client.is_keyboard_shown() is False


# ---------------------------------------------------------------------------
# Activity coverage -- get_current_activity / get_declared_activities
# ---------------------------------------------------------------------------


def test_get_current_activity_parses_the_resumed_activity(client, device):
    device.resumed_activity_output = (
        "    mResumedActivity: ActivityRecord{abc123 u0 com.test.app/.MainActivity t42}\n"
    )
    assert client.get_current_activity() == "com.test.app/.MainActivity"


def test_get_current_activity_returns_empty_when_grep_finds_no_match(client, device):
    """grep exits NONZERO on no match; `shell()` now raises AdbError for that.

    get_current_activity must keep its documented "empty string on failure"
    contract rather than let a common, not exotic, path raise.
    """
    device.resumed_activity_output = None
    assert client.get_current_activity() == ""


def test_get_declared_activities_from_packages_section(client, device):
    """Packages section lists ALL manifest activities (not just intent-filtered)."""
    device.package_dump_output = (
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
    result = client.get_declared_activities("com.test.app")
    # Should find all 3 activities from Packages section, not just 1 from Resolver
    assert result == [
        "com.test.app/.DetailActivity",
        "com.test.app/.MainActivity",
        "com.test.app/.SettingsActivity",
    ]


def test_get_declared_activities_fallback_to_resolver(client, device):
    """Falls back to Activity Resolver Table when Packages section is absent."""
    device.package_dump_output = (
        "Activity Resolver Table:\n"
        "  Non-Data Actions:\n"
        "      android.intent.action.MAIN:\n"
        "        abc1234 com.test.app/.MainActivity filter abc\n"
        "        def5678 com.test.app/.SettingsActivity filter def\n"
        "  Receiver Resolver Table:\n"
        "      some.action:\n"
    )
    result = client.get_declared_activities("com.test.app")
    assert result == ["com.test.app/.MainActivity", "com.test.app/.SettingsActivity"]


def test_get_declared_activities_empty(client, device):
    device.package_dump_output = ""
    assert client.get_declared_activities("com.test.app") == []


def test_get_declared_activities_returns_empty_on_any_failure(client, device):
    def boom(cmd: str) -> tuple[int, str]:
        raise RuntimeError("timeout")

    device._shell = boom  # type: ignore[method-assign]
    assert client.get_declared_activities("com.test.app") == []
