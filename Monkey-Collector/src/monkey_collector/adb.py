"""Thin ADB client — the ONLY channel Monkey-Collector has to the device.

Host-pull: every observation is fetched by the host, on the host's schedule.
Nothing runs on the device beyond stock ``uiautomator`` / ``screencap`` / ``input``.

Construction takes ``serial: str | None = None`` and must not touch USB --
otherwise merely importing this module in a test would need a device attached.
The serial is resolved **lazily, on first command**.

The USB link to the physical Pixel 6 has been observed to drop mid-session;
:meth:`AdbClient.reconnect` performs ``kill-server`` / ``start-server`` /
``wait-for-device`` and re-resolves the serial. Callers own the retry policy.

ACTUATORS
---------
Monkey's action space (``domain/actions.py``) is served by the following
actuators:

- :meth:`long_press` -- the ``LongPress`` action, a zero-movement :meth:`swipe`.
- :meth:`press_back` / :meth:`press_home` -- ``PressBack`` / ``PressHome``,
  thin wrappers over the generic :meth:`key`.
- :meth:`input_text` / :meth:`clear_text_field` -- the ``InputText`` action.
  :meth:`text` is a plain alias of :meth:`input_text`, so the two never drift
  into subtly different escaping.
- :meth:`is_keyboard_shown` (+ :meth:`_parse_keyboard_shown`) and
  :meth:`hide_keyboard` -- IME handling after a text-input action.
- :meth:`get_current_activity` (+ :meth:`_parse_current_activity`) and
  :meth:`get_declared_activities` (+ its two parse helpers) -- the numerator and
  denominator of activity coverage; ``catalog_activities.py`` names
  ``get_declared_activities`` as its documented dumpsys fallback.

``tap``/``swipe``/``launch_app``/``force_stop``/``shell`` return ``None`` instead
of the shell stdout, because nothing that calls this class depends on that
return value. ``shell`` RAISES :class:`AdbError` on a nonzero exit instead of
just logging -- that raising surfaced a real bug in the two dumpsys-based query
methods: piping through ``grep`` makes "no match" a nonzero exit, which has to
be caught explicitly (see :meth:`get_current_activity`, :meth:`is_keyboard_shown`)
so they keep their documented "empty/false on failure" contract instead of
raising on a common, not exotic, path.

:meth:`current_package` reads the foreground package over a dumpsys query and
returns ``None`` on failure.

Milestone status: this module is infrastructure for the collection loop, which
is NOT implemented yet.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, cast

from loguru import logger

DEFAULT_TIMEOUT = 30.0
#: On-device scratch path for `uiautomator dump` output.
REMOTE_DUMP_PATH = "/sdcard/window_dump.xml"

#: FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK, for `launch_app(clean=True)`.
#: CLEAR_TASK is the half that matters and it is only legal alongside NEW_TASK.
CLEAN_LAUNCH_FLAGS = "0x10008000"

#: Control characters (C0 + DEL). `input text` has no representation for any of
#: them, so they are rejected rather than escaped -- see `escape_text_for_adb`.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_WM_SIZE_RE = re.compile(r"(?:Override|Physical) size:\s*(\d+)x(\d+)")
#: Where a uiautomator hierarchy document starts, whichever way it was dumped.
_XML_START_RE = re.compile(r"<\?xml|<hierarchy\b")
_HIERARCHY_END = "</hierarchy>"


class AdbError(RuntimeError):
    """An adb invocation failed, timed out, or returned unusable output."""


class UiDumpError(AdbError):
    """A UI dump failed a freshness or validity check; no buffer was returned.

    Subclasses :class:`AdbError` deliberately: :meth:`AdbClient.dump_ui` used to
    raise ``AdbError``, and the collection loop catches that. A sibling class would
    slip straight through an ``except AdbError`` and reintroduce the silent
    stale-XML failure this type exists to make loud.
    """


class TextEscapeError(ValueError):
    """Text cannot reach the device through ``input text`` without mutating."""


@dataclass(frozen=True)
class ScreenSize:
    width: int
    height: int

    def as_tuple(self) -> tuple[int, int]:
        return (self.width, self.height)


def _find_adb() -> str:
    """Locate the adb binary: PATH, then the common SDK locations."""
    found = shutil.which("adb")
    if found:
        return found
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidate = os.path.join(android_home, "platform-tools", "adb")
        if os.path.isfile(candidate):
            return candidate
    default = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    if os.path.isfile(default):
        return default
    return "adb"  # fall through; FileNotFoundError surfaces on first call


def escape_text_for_adb(text: str) -> str:
    r"""Quote *text* into ONE shell word for ``adb shell input text``.

    The contract, pinned character-for-character by ``tests/unit/test_adb.py``:

    * ``" " -> "%s"`` is load-bearing: ``input text`` splits its argument on
      whitespace and would otherwise drop everything after the first space.
    * The result is then quoted with :func:`shlex.quote`, so every shell
      metacharacter (`` ` $ " ' \ & | ; < > ( ) ``) reaches the device verbatim
      instead of being interpreted by the device's ``sh``. The empty string
      quotes to ``''``, which types nothing.
    * Control characters (``\t``, ``\n``, ``\r`` and the rest of C0/DEL) are
      **rejected** with :class:`TextEscapeError` instead of being escaped. The
      previous backslash-escaping silently mutated them: ``"line1\nline2"``
      came out as a backslash followed by a real newline, which ``sh`` reads as a
      line continuation -- it deletes both, so the device typed ``line1line2``. A
      literal tab likewise survived into ``input text``, which splits on it. A
      caller that wants a newline or a tab sends the segments as separate
      :meth:`AdbClient.input_text` calls with ``key("KEYCODE_ENTER")`` or
      ``key("KEYCODE_TAB")`` in between.
    * A literal ``"%s"`` is **rejected** for the same reason: it collides with the
      space escape above, so a single ``input text`` call cannot type it
      unambiguously -- it would arrive as a space. A ``%`` that is not followed by
      ``s`` is unambiguous and passes through unchanged.

    The rule behind both rejections: never emit a token whose device-side reading
    is ambiguous. Raising is recoverable; a corpus of quietly mutated text is not.

    NOTE for EXP08: the value returned here is transport only. The ``type``
    action recorded in the triple must carry the ORIGINAL *text*, never this
    escaped form -- the model is trained on what a user typed, not on how adb
    smuggled it onto the device.

    Raises:
        TextEscapeError: *text* holds a control character or a literal ``"%s"``.
    """
    control = _CONTROL_CHARS_RE.search(text)
    if control is not None:
        raise TextEscapeError(
            f"text contains control character {control.group()!r} at offset "
            f"{control.start()}; `input text` cannot type it. Split the text and "
            "send key('KEYCODE_ENTER') / key('KEYCODE_TAB') between the segments."
        )
    if "%s" in text:  # checked BEFORE the substitution below, never after
        raise TextEscapeError(
            "text contains a literal '%s', which collides with the space escape "
            "`input text` uses; a single `input text` call cannot type it."
        )
    return shlex.quote(text.replace(" ", "%s"))


def _extract_hierarchy_xml(raw: str, *, source: str) -> str:
    """Slice the hierarchy document out of *raw* and prove it is usable XML.

    ``uiautomator`` prints a "UI hierarchy dumped to:" banner next to the XML when
    it dumps to stdout, so the document is located STRUCTURALLY -- from the
    ``<?xml``/``<hierarchy`` opening to the last ``</hierarchy>`` -- rather than by
    stripping banner text that varies across Android versions (it is even
    misspelled "hierchary" on some).

    A ``</hierarchy>`` end anchor plus well-formedness already implies the root
    element IS ``<hierarchy>`` (a single-rooted document whose last close tag is
    ``</hierarchy>``), so there is no separate root check to go stale. Node text
    that happens to contain ``</hierarchy>`` cannot fool the anchor either:
    uiautomator escapes ``<``/``>`` inside attribute values.

    Raises:
        UiDumpError: naming which check failed -- empty output, no XML at all,
            truncation, or malformed XML.
    """
    if not raw.strip():
        raise UiDumpError(f"uiautomator dump ({source}) returned no output at all")
    start = _XML_START_RE.search(raw)
    if start is None:
        raise UiDumpError(f"uiautomator dump ({source}) returned no XML: {raw[:200]!r}")
    end = raw.rfind(_HIERARCHY_END)
    if end == -1:
        raise UiDumpError(
            f"uiautomator dump ({source}) is truncated -- no closing "
            f"{_HIERARCHY_END}: {raw[-200:]!r}"
        )
    xml = raw[start.start() : end + len(_HIERARCHY_END)]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise UiDumpError(f"uiautomator dump ({source}) is not well-formed XML: {exc}") from exc
    if len(root) == 0:
        # Well-formed but node-less: a real screen always has at least one node.
        # Not fatal (the caller may legitimately observe an empty window), but it
        # must never pass silently.
        logger.warning(f"[adb] dump ({source}) is well-formed but has no <node> children")
    return xml


class AdbClient:
    """Wraps ``adb`` for one device.

    Args:
        serial: device serial to pin every command to. ``None`` autodetects the
            single online device on first use.
        timeout: default per-command timeout in seconds.
    """

    def __init__(self, serial: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._adb = _find_adb()
        self._serial = serial
        self._timeout = timeout
        #: True when the caller pinned a serial explicitly. Distinct from
        #: ``_resolved``: an autodetected serial is resolved but NOT pinned, and
        #: must be re-detected after a reconnect (the serial can come back
        #: different, and a stale one silently addresses nothing).
        self._pinned = serial is not None
        self._resolved = self._pinned  # a pinned serial needs no probe
        #: package -> its launcher component (or None when it has none). The
        #: answer cannot change while the app stays installed, and a clean
        #: relaunch would otherwise pay a `cmd package` round-trip every time.
        self._launcher_components: dict[str, str | None] = {}

    # -- serial resolution --------------------------------------------------

    @property
    def serial(self) -> str | None:
        """The pinned serial, resolving it on first access if needed."""
        if not self._resolved:
            self._serial = self._autodetect_serial()
            self._resolved = True
        return self._serial

    def _online_serials(self) -> list[str]:
        result = subprocess.run(
            [self._adb, "devices"], capture_output=True, text=True, timeout=10
        )
        serials: list[str] = []
        for line in result.stdout.splitlines()[1:]:  # skip "List of devices attached"
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials

    def _autodetect_serial(self) -> str:
        serials = self._online_serials()
        if not serials:
            raise AdbError(
                "No device is online. Connect the target device and check `adb devices`. "
                "If the USB link dropped mid-session, call AdbClient.reconnect()."
            )
        if len(serials) > 1:
            raise AdbError(
                f"{len(serials)} devices online ({serials}); pin one with device.serial "
                "in config/run.yaml or --serial."
            )
        logger.debug(f"[adb] autodetected serial: {serials[0]}")
        return serials[0]

    # -- command plumbing ---------------------------------------------------

    def _argv(self, args: list[str]) -> list[str]:
        serial = self.serial
        prefix = [self._adb] + (["-s", serial] if serial else [])
        return prefix + args

    def _run(
        self, args: list[str], *, binary: bool = False, timeout: float | None = None
    ) -> subprocess.CompletedProcess[Any]:
        argv = self._argv(args)
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=not binary,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb timed out: {' '.join(args)}") from exc
        if result.returncode != 0:
            stderr = result.stderr if not binary else (result.stderr or b"").decode(errors="replace")
            raise AdbError(f"adb failed ({result.returncode}): {' '.join(args)}\n{stderr}")
        return result

    def shell(self, command: str, *, timeout: float | None = None) -> str:
        """Run ``adb shell <command>`` and return stdout."""
        return cast(str, self._run(["shell", command], timeout=timeout).stdout)

    def reconnect(self) -> str | None:
        """Recover a dropped USB link: kill-server, start-server, wait-for-device.

        The physical link WILL drop during long sessions; this is the documented
        recovery, verified live on the target Pixel 6. Re-resolves the serial
        afterwards (unless one was explicitly pinned) and returns it.
        """
        logger.warning("[adb] reconnecting: kill-server -> start-server -> wait-for-device")
        subprocess.run([self._adb, "kill-server"], capture_output=True, timeout=30)
        subprocess.run([self._adb, "start-server"], capture_output=True, timeout=60)
        subprocess.run([self._adb, "wait-for-device"], capture_output=True, timeout=120)
        if not self._pinned:
            # Autodetected serials are re-detected; only an explicitly pinned one survives.
            self._serial = None
            self._resolved = False
        serial = self.serial
        logger.info(f"[adb] reconnected: serial={serial}")
        return serial

    # -- observation --------------------------------------------------------

    def dump_ui(self, *, timeout: float | None = None) -> str:
        """Return the CURRENT window hierarchy XML (``before_xml`` / ``after_xml``).

        Freshness is structural here, not advisory. The primary path streams the
        dump over stdout (``exec-out uiautomator dump /dev/tty``): nothing is
        written to a shared on-device path, so there is no previous-screen buffer
        that a failed dump could hand back. A benchmark on the target Pixel 6
        measured all three dump variants at the same ~2.33s, so the transfer-free
        path costs nothing and removes the shared-file hazard entirely.

        The stdout stream carries uiautomator's "UI hierarchy dumped to:" banner
        beside the XML; :func:`_extract_hierarchy_xml` removes it by slicing the
        document out structurally, so the banner is a non-issue rather than a
        reason to route through a file.

        The file path survives only as a fallback, and it (a) ``rm -f``s the remote
        path and PROVES it is gone, (b) requires the dump command to report
        success, and (c) requires non-empty, well-formed ``<hierarchy>`` XML.

        Raises:
            UiDumpError: when both paths fail, naming the check each one failed.
                A possibly-stale buffer is never returned -- the previous screen
                silently standing in for this observation is corpus corruption
                that nothing downstream can detect.
        """
        try:
            return self._dump_via_stdout(timeout=timeout)
        except AdbError as primary:
            logger.warning(
                f"[adb] stdout dump failed ({primary}); falling back to {REMOTE_DUMP_PATH}"
            )
            try:
                return self._dump_via_file(timeout=timeout)
            except AdbError as fallback:
                raise UiDumpError(
                    f"both dump paths failed -- /dev/tty: {primary} | "
                    f"{REMOTE_DUMP_PATH}: {fallback}"
                ) from fallback

    def _dump_via_stdout(self, *, timeout: float | None = None) -> str:
        """Primary path: dump straight to stdout, touching no shared file."""
        result = self._run(
            ["exec-out", "uiautomator", "dump", "/dev/tty"], binary=True, timeout=timeout
        )
        raw = cast(bytes, result.stdout).decode("utf-8", errors="replace")
        return _extract_hierarchy_xml(raw, source="/dev/tty")

    def _dump_via_file(self, *, timeout: float | None = None) -> str:
        """Fallback path: dump to ``REMOTE_DUMP_PATH``, with the staleness guards.

        The remote file is live-confirmed to persist between calls, so the order
        below is the whole point: clear it, prove it is gone, and only then dump.
        Any failure of the dump itself then leaves nothing to read back, instead of
        leaving the previous screen sitting there ready to be mistaken for this one.
        """
        self.shell(f"rm -f {REMOTE_DUMP_PATH}")
        probe = self.shell(f"[ -e {REMOTE_DUMP_PATH} ] && echo EXISTS || echo GONE")
        if "GONE" not in probe:
            raise UiDumpError(
                f"stale-dump guard: {REMOTE_DUMP_PATH} still exists after `rm -f` "
                f"(probe={probe.strip()!r}); refusing to read a possibly-stale buffer"
            )
        out = self.shell(f"uiautomator dump {REMOTE_DUMP_PATH}", timeout=timeout)
        if "dumped to" not in out:
            # A POSITIVE gate, and only a positive gate. The old test ("ERROR" in
            # out AND "dumped to" not in out) let every quiet failure through -- a
            # swallowed timeout, a permission blip, a secure-window refusal -- and
            # then `cat` handed back the previous screen. Substring-hunting for
            # "ERROR" is not the mirror fix: it would reject a good dump whose
            # output merely mentions the word, and it is unnecessary here, because
            # freshness no longer rests on parsing this banner at all -- the file
            # was proven absent above, so anything `cat` returns was written by
            # THIS dump, and it still has to survive `_extract_hierarchy_xml`.
            raise UiDumpError(f"`uiautomator dump` did not report success: {out.strip()!r}")
        if "ERROR" in out:
            # Reported, never fatal: the dump said it succeeded, and the checks
            # that matter are structural.
            logger.warning(f"[adb] uiautomator reported success but mentioned an error: {out.strip()!r}")
        raw = cast(
            bytes,
            self._run(["exec-out", "cat", REMOTE_DUMP_PATH], binary=True, timeout=timeout).stdout,
        ).decode("utf-8", errors="replace")
        return _extract_hierarchy_xml(raw, source=REMOTE_DUMP_PATH)

    def screencap(self, *, timeout: float | None = None) -> bytes:
        """Return a PNG screenshot as raw bytes (``exec-out screencap -p``)."""
        png = cast(
            bytes, self._run(["exec-out", "screencap", "-p"], binary=True, timeout=timeout).stdout
        )
        if not png.startswith(b"\x89PNG"):
            raise AdbError(f"screencap returned non-PNG data ({len(png)} bytes)")
        return png

    def wm_size(self) -> ScreenSize:
        """Device logical resolution via ``wm size``. Override size wins if set."""
        out = self.shell("wm size")
        matches = _WM_SIZE_RE.findall(out)
        if not matches:
            raise AdbError(f"could not parse `wm size` output: {out.strip()!r}")
        width, height = matches[-1]  # Override line, when present, comes last
        return ScreenSize(int(width), int(height))

    def current_package(self) -> str | None:
        """Package of the focused window, or None when it cannot be determined."""
        try:
            out = self.shell("dumpsys window displays | grep -E 'mCurrentFocus|mFocusedApp'")
        except AdbError:
            return None
        for line in out.splitlines():
            if "/" not in line:
                continue
            token = line.strip().rstrip("}").split()[-1]
            if "/" in token:
                return token.split("/")[0].lstrip("{")
        return None

    # -- actuation ----------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        """Tap at device pixel coordinates."""
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """Swipe between two device pixel coordinates over *duration_ms*."""
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        """Long-press at (x, y) via a zero-movement swipe -- the ``LongPress`` action."""
        self.swipe(x, y, x, y, duration_ms)

    def input_text(self, value: str) -> None:
        """Type *value* into the focused field (quoted by `escape_text_for_adb`).

        This is the ``InputText`` action's executor. Propagates
        :class:`TextEscapeError` unchanged: text that cannot be typed without
        mutating is a caller decision (split it and send KEYCODE_ENTER /
        KEYCODE_TAB between segments), never something this layer papers over.
        """
        self.shell(f"input text {escape_text_for_adb(value)}")

    def text(self, value: str) -> None:
        """Alias of :meth:`input_text`.

        Kept so both names reach the exact same escaping -- see the module
        docstring's MERGE NOTES on why two subtly different typers is the
        failure mode being avoided here.
        """
        self.input_text(value)

    def clear_text_field(self) -> None:
        """Select all text in the focused field and delete it."""
        self.key("KEYCODE_MOVE_END")
        self.shell("input keycombination 113 29")  # Ctrl+A
        self.key("KEYCODE_DEL")

    def key(self, keycode: str | int) -> None:
        """Send a keyevent, e.g. ``"KEYCODE_BACK"``, ``"KEYCODE_HOME"``, or ``4``."""
        self.shell(f"input keyevent {keycode}")

    def press_back(self) -> None:
        """Press the Android back button -- the ``PressBack`` action."""
        self.key("KEYCODE_BACK")

    def press_home(self) -> None:
        """Press the Android home button -- the ``PressHome`` action."""
        self.key("KEYCODE_HOME")

    def hide_keyboard(self) -> None:
        """Close the soft input without a Back press (KEYCODE_ESCAPE).

        KEYCODE_ESCAPE dismisses the on-screen keyboard but does not pop the
        activity back stack, so it can dismiss a keyboard on a page from which
        a real Back would exit the app to the launcher.
        """
        self.key("KEYCODE_ESCAPE")

    def is_keyboard_shown(self) -> bool:
        """Return whether the soft input (IME) is currently shown.

        Bounded timeout: this is a measurement call on the ESC-dismiss path,
        not a driver of behavior, so a wedged ``adb shell`` must not stall the
        loop. A failure (including a dropped link) is swallowed to ``False``
        rather than propagated -- matching :meth:`current_package`'s fallback
        idiom -- because a keyboard-visibility probe is not where a caller
        wants to discover a lost USB link.
        """
        try:
            output = self.shell("dumpsys input_method", timeout=5)
        except AdbError:
            return False
        return self._parse_keyboard_shown(output)

    @staticmethod
    def _parse_keyboard_shown(output: str) -> bool:
        """Parse IME-shown state from ``dumpsys input_method`` output.

        Field name observed live (Pixel6-2, API 33, google_apis image):
        ``mInputShown=true|false`` on the
        ``mShowRequested=... mInputShown=...`` line. Also accepts
        ``isInputViewShown=true|false``, seen on some OEM/API variants, so the
        match is deliberately permissive across both spellings.
        """
        match = re.search(r'\b(?:mInputShown|isInputViewShown)=(true|false)\b', output)
        if match:
            return match.group(1) == "true"
        return False

    def get_current_activity(self) -> str:
        """Return the full activity component name of the foreground activity.

        Returns a string like ``com.test.app/.MainActivity``, or ``""`` when it
        cannot be determined -- including when the ``grep`` below finds no
        match. ``adb shell`` reports that as a NONZERO exit, which
        :meth:`shell` now turns into :class:`AdbError`; caught here so this
        method keeps its documented "empty string on failure" contract instead
        of raising on what is a common path, not an exotic one.

        Bounded timeout: this runs on the per-frame coverage hot path
        (resolving the real foreground activity when the a11y report is a
        generic View class), so a wedged ``adb shell`` must not become a new
        stall vector.
        """
        try:
            output = self.shell(
                "dumpsys activity activities | grep ResumedActivity", timeout=5
            )
        except AdbError:
            return ""
        return self._parse_current_activity(output)

    @staticmethod
    def _parse_current_activity(output: str) -> str:
        """Extract the foreground activity component from dumpsys output.

        dumpsys prints the resumed activity inside an ``ActivityRecord{...}``
        wrapper, e.g. ``ActivityRecord{c5601b7 u0 com.google.android.calendar/
        .AllInOneCalendarActivity}``. A naive ``\\S+/\\S+`` match swallows the
        trailing ``}`` (and any following token), producing a component name
        like ``...AllInOneCalendarActivity}`` that never matches a declared
        activity and freezes coverage. Restrict the component-name character
        class to activity-name chars so ``}`` and whitespace act as boundaries.
        """
        match = re.search(r'([A-Za-z0-9_$.]+/[A-Za-z0-9_$.]+)', output)
        if match:
            return match.group(1)
        return ""

    def get_declared_activities(self, package: str) -> list[str]:
        """Return all declared Activity component names for *package*.

        Parses the ``Packages:`` section from ``dumpsys package`` to get
        **all** manifest-declared activities.  Falls back to the Activity
        Resolver Table (intent-filter-only) when the Packages section
        yields no results.  Returns sorted list of component strings.
        """
        try:
            output = self.shell(f"dumpsys package {package}", timeout=15)
        except Exception as e:
            logger.warning(f"Failed to get declared activities: {e}")
            return []

        activities = self._parse_package_activities(output, package)
        if not activities:
            activities = self._parse_resolver_activities(output, package)

        return sorted(activities)

    # ------------------------------------------------------------------
    # Private helpers for dumpsys parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_package_activities(output: str, package: str) -> set[str]:
        """Parse activities from the ``Packages:`` section of dumpsys.

        This section lists **all** manifest-declared activities regardless
        of whether they have intent filters.
        """
        activities: set[str] = set()
        activity_pattern = re.compile(rf'({re.escape(package)}/\S+)')

        in_packages = False
        in_target_pkg = False
        in_activities = False
        activities_indent: int | None = None

        for line in output.splitlines():
            stripped = line.strip()

            if not in_packages:
                if stripped == "Packages:":
                    in_packages = True
                continue

            if not in_target_pkg:
                if f"Package [{package}]" in stripped:
                    in_target_pkg = True
                continue

            indent = len(line) - len(line.lstrip())

            if not in_activities:
                if stripped == "activities:":
                    in_activities = True
                    activities_indent = indent
                    continue
                # Another top-level package started — stop searching.
                if stripped.startswith("Package ["):
                    break
                continue

            # Inside activities: subsection.
            # A sibling or parent section at same/lower indent ends it.
            assert activities_indent is not None  # set whenever in_activities is True
            if stripped and indent <= activities_indent:
                break

            match = activity_pattern.search(stripped)
            if match:
                activities.add(match.group(1))

        return activities

    @staticmethod
    def _parse_resolver_activities(output: str, package: str) -> set[str]:
        """Parse activities from the Activity Resolver Table (fallback).

        Only returns activities that have intent filters registered.
        """
        activities: set[str] = set()
        in_activity_section = False
        activity_pattern = re.compile(rf'({re.escape(package)}/\S+)')

        for line in output.splitlines():
            stripped = line.strip()
            if "Activity Resolver Table:" in stripped:
                in_activity_section = True
                continue
            if in_activity_section and stripped.startswith(
                ("Receiver Resolver", "Service Resolver",
                 "Provider Resolver", "Permissions:")
            ):
                break
            if in_activity_section:
                match = activity_pattern.search(stripped)
                if match:
                    activities.add(match.group(1))

        return activities

    def resolve_launcher_activity(self, package: str) -> str | None:
        """The ``package/activity`` component that *package*'s icon opens.

        ``None`` when the package has no launcher activity. Cached per package.
        """
        if package in self._launcher_components:
            return self._launcher_components[package]
        component: str | None = None
        try:
            out = self.shell(
                "cmd package resolve-activity --brief "
                f"-c android.intent.category.LAUNCHER {package}"
            )
        except AdbError as error:
            logger.warning(f"[adb] could not resolve {package}'s launcher: {error}")
        else:
            # The command also prints a `priority=... isDefault=...` line, and a
            # package with no launcher icon resolves to the *resolver* activity,
            # which belongs to another package. Only a component under
            # `package` is ours.
            for line in reversed(out.splitlines()):
                candidate = line.strip()
                if candidate.startswith(f"{package}/"):
                    component = candidate
                    break
        self._launcher_components[package] = component
        return component

    def launch_app(self, package: str, *, clean: bool = False) -> None:
        """Launch *package*'s LAUNCHER activity.

        ``clean=False`` sends the intent the launcher icon sends, via ``monkey``.
        That RESUMES an existing task at whatever sits on top of it rather than
        starting the app fresh -- usually what you want, because a recovery that
        preserves the app's deep state keeps exploring where it left off.

        ``clean=True`` is for when that resumption is the failure. Measured on
        the target device: Settings had been left on
        ``com.google.android.gms/.octarine.ui.OctarineActivity`` in task t69, and
        BOTH monkey and ``am force-stop com.android.settings`` followed by monkey
        landed straight back on it -- force-stopping Settings cannot touch an
        activity running in GMS's process. The collection loop read every
        observation as "outside the app", recovered five times and abandoned the
        session after 10 triples. Force-stop plus a ``CLEAR_TASK`` start cleared
        the task and resumed ``com.android.settings/.Settings``.

        The force-stop is not redundant with the flags: ``CLEAR_TASK`` empties
        the back stack, force-stop kills the process behind it. Only together is
        the start cold.
        """
        if clean:
            self.force_stop(package)
            component = self.resolve_launcher_activity(package)
            if component is None:
                logger.warning(f"[adb] {package} has no launcher activity; using monkey")
            else:
                out = self.shell(
                    "am start -a android.intent.action.MAIN "
                    f"-c android.intent.category.LAUNCHER -n {component} "
                    f"-f {CLEAN_LAUNCH_FLAGS}",
                    timeout=60,
                )
                if "Error" not in out:
                    return
                logger.warning(
                    f"[adb] clean launch of {package} failed ({out.strip()}); using monkey"
                )
        out = self.shell(
            f"monkey -p {package} -c android.intent.category.LAUNCHER 1", timeout=60
        )
        if "No activities found" in out or "Error" in out:
            raise AdbError(f"could not launch {package}: {out.strip()}")

    def force_stop(self, package: str) -> None:
        """Force-stop *package* (used before a clean relaunch)."""
        self.shell(f"am force-stop {package}")

    def list_packages(self) -> list[str]:
        """Installed package ids (``pm list packages``)."""
        out = self.shell("pm list packages")
        return sorted(
            line.split(":", 1)[1].strip()
            for line in out.splitlines()
            if line.startswith("package:")
        )


__all__ = [
    "CLEAN_LAUNCH_FLAGS",
    "DEFAULT_TIMEOUT",
    "REMOTE_DUMP_PATH",
    "AdbClient",
    "AdbError",
    "ScreenSize",
    "TextEscapeError",
    "UiDumpError",
    "escape_text_for_adb",
]
