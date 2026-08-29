"""A scripted stand-in for :class:`~monkey_collector.adb.AdbClient`.

The collection loop is the one component that talks to hardware, so its tests
replace the DEVICE rather than the loop. Nothing here runs ``adb``: the target
is a real, logged-in phone and a test suite must never reach it.

Lives outside ``tests/unit`` so both the loop tests and the CLI tests can import
it without one test module importing another.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from monkey_collector.adb import ScreenSize

PKG = "com.test.app"
ACT = f"{PKG}/.Main"

EMPTY_SCREEN = '<hierarchy rotation="0"/>'


def screen(*, rows: int = 3, package: str = PKG, tag: str = "a") -> str:
    """A dump with *rows* clickable rows, each labelled with *tag*."""
    nodes = "".join(
        f'<node class="android.widget.LinearLayout" package="{package}" '
        f'clickable="true" enabled="true" visible-to-user="true" '
        f'bounds="[0,{i * 100}][1080,{(i + 1) * 100}]">'
        f'<node text="{tag}-row-{i}" package="{package}"/>'
        f"</node>"
        for i in range(rows)
    )
    return f'<hierarchy rotation="0">{nodes}</hierarchy>'


def text_field_screen(*, package: str = PKG) -> str:
    """A dump whose only actionable element takes text."""
    return (
        '<hierarchy rotation="0">'
        f'<node class="android.widget.EditText" package="{package}" enabled="true" '
        'visible-to-user="true" bounds="[0,0][1080,200]" '
        'resource-id="com.test.app:id/title">'
        f'<node text="Title" package="{package}"/>'
        "</node>"
        "</hierarchy>"
    )


@dataclass
class Script:
    """What the fake device shows, and under which activity."""

    xml: str
    activity: str = ACT


class FakeAdb:
    """Replays `scripts`; the last entry repeats once the script runs out.

    Every action advances the cursor, so "the screen after the action" is the
    next scripted entry — which is what makes a triple's before/after differ.
    """

    def __init__(
        self,
        scripts: list[Script],
        *,
        fail_dumps: int = 0,
        size: ScreenSize | None = None,
    ) -> None:
        self.scripts = scripts
        self.actions: list[tuple[str, tuple]] = []
        self.shell_commands: list[str] = []
        self.launches = 0
        self.clean_launches = 0
        self.reconnects = 0
        self.stopped: list[str] = []
        self.typed: list[str] = []
        self.serial = "fake-serial"
        self._size = size or ScreenSize(1080, 2400)
        self._dumps = 0
        self._fail_dumps = fail_dumps
        self._cursor = 0

    # -- observation --------------------------------------------------------

    def screencap(self, **_):
        """A REAL PNG whose content tracks the cursor.

        Stabilization decodes what it is given, so a stub byte string would make
        every observation fail — the wrong failure to be exercising in tests
        about triples and recovery.
        """
        from PIL import Image

        image = Image.new("L", (120, 260), 255)
        image.paste(self._cursor % 200, (0, 0, 120, 20))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def dump_ui(self, **_):
        self._dumps += 1
        if self._dumps <= self._fail_dumps:
            raise RuntimeError("dump failed")
        return self._current.xml

    def get_current_activity(self) -> str:
        return self._current.activity

    def get_declared_activities(self, package: str) -> list[str]:
        return [ACT] if package == PKG else []

    def shell(self, command, **_):
        self.shell_commands.append(command)
        return ""

    def wm_size(self) -> ScreenSize:
        return self._size

    @property
    def _current(self) -> Script:
        return self.scripts[min(self._cursor, len(self.scripts) - 1)]

    def _advance(self) -> None:
        self._cursor += 1

    # -- actuation ----------------------------------------------------------

    def tap(self, x, y):
        self.actions.append(("tap", (x, y)))
        self._advance()

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        self.actions.append(("swipe", (x1, y1, x2, y2)))
        self._advance()

    def long_press(self, x, y, duration_ms=1000):
        self.actions.append(("long_press", (x, y, duration_ms)))
        self._advance()

    def input_text(self, value):
        self.typed.append(value)
        self.actions.append(("text", (value,)))

    def text(self, value):
        self.input_text(value)

    def key(self, keycode):
        self.actions.append(("key", (keycode,)))
        self._advance()

    def press_back(self):
        self.key("KEYCODE_BACK")

    def press_home(self):
        self.key("KEYCODE_HOME")

    def hide_keyboard(self):
        self.actions.append(("key", ("KEYCODE_ESCAPE",)))

    # -- lifecycle ----------------------------------------------------------

    def launch_app(self, package, *, clean=False):
        self.launches += 1
        self.clean_launches += int(clean)
        self.actions.append(("launch", (package, clean)))

    def force_stop(self, package):
        self.stopped.append(package)

    def reconnect(self):
        self.reconnects += 1
        return self.serial


__all__ = [
    "ACT",
    "EMPTY_SCREEN",
    "PKG",
    "FakeAdb",
    "Script",
    "screen",
    "text_field_screen",
]
