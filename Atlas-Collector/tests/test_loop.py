"""The collection loop, driven by a fake device.

The loop is the one component that talks to hardware, so these tests replace the
device rather than the loop: a FakeAdb replays scripted screens and records the
actions performed against it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pytest
from PIL import Image

from atlas_collector.adb import ScreenSize
from atlas_collector.coverage import ActivityCoverage
from atlas_collector.loop import (
    MAX_CONSECUTIVE_RECOVERIES,
    MAX_STEPS_OUTSIDE,
    CollectionLoop,
    is_launcher,
)
from atlas_collector.session import Session

PKG = "com.test.app"
ACT = f"{PKG}/.Main"


def screen(*, rows: int = 3, package: str = PKG, tag: str = "a") -> str:
    nodes = "".join(
        f'<node class="android.widget.LinearLayout" package="{package}" '
        f'clickable="true" enabled="true" visible-to-user="true" '
        f'bounds="[0,{i * 100}][1080,{(i + 1) * 100}]">'
        f'<node text="{tag}-row-{i}" package="{package}"/>'
        f"</node>"
        for i in range(rows)
    )
    return f'<hierarchy rotation="0">{nodes}</hierarchy>'


EMPTY = '<hierarchy rotation="0"/>'


@dataclass
class Script:
    """What the fake device shows, and for how long."""

    xml: str
    activity: str = ACT


class FakeAdb:
    """Replays `scripts`; the last entry repeats once the script runs out."""

    def __init__(self, scripts: list[Script], *, fail_dumps: int = 0) -> None:
        self.scripts = scripts
        self.actions: list[tuple[str, tuple]] = []
        self.launches = 0
        self.clean_launches = 0
        self.reconnects = 0
        self._dumps = 0
        self._fail_dumps = fail_dumps
        self._cursor = 0

    # -- observation
    def screencap(self, **_):
        """A REAL PNG whose content tracks the cursor.

        Stabilization decodes what it is given, so a stub byte string makes
        every observation fail — which is exactly the wrong failure to be
        exercising in tests about triples and recovery.
        """
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

    def shell(self, command, **_):
        if "topResumedActivity" in command:
            return f"  topResumedActivity=ActivityRecord{{x u0 {self._current.activity} t1}}"
        self.actions.append(("shell", (command,)))
        return ""

    @property
    def _current(self) -> Script:
        return self.scripts[min(self._cursor, len(self.scripts) - 1)]

    def _advance(self) -> None:
        self._cursor += 1

    # -- actions
    def tap(self, x, y):
        self.actions.append(("tap", (x, y)))
        self._advance()

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        self.actions.append(("swipe", (x1, y1, x2, y2)))
        self._advance()

    def text(self, value):
        self.actions.append(("text", (value,)))

    def key(self, keycode):
        self.actions.append(("key", (keycode,)))
        self._advance()

    def launch_app(self, package, *, clean=False):
        self.launches += 1
        self.clean_launches += int(clean)
        self.actions.append(("launch", (package, clean)))

    def reconnect(self):
        self.reconnects += 1
        return "serial"

    def wm_size(self):
        return ScreenSize(1080, 2400)


def build(tmp_path, adb, **kwargs) -> CollectionLoop:
    session = Session(PKG, tmp_path / "data", tmp_path / "runtime")
    session.open(resume=False)
    kwargs.setdefault("max_steps", 6)
    kwargs.setdefault("action_delay_ms", 0)
    kwargs.setdefault("stabilize", {"max_wait_sec": 0.2, "poll_ms": 0})
    kwargs.setdefault("launch_settle_sec", 0.0)
    return CollectionLoop(adb, session, **kwargs)


# ---------------------------------------------------------------------------
# is_launcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ("com.google.android.apps.nexuslauncher", True),
        ("com.android.launcher3", True),
        (PKG, False),
        ("", False),
        (None, False),
    ],
)
def test_is_launcher(package, expected):
    assert is_launcher(package) is expected


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_run_produces_one_triple_per_action(tmp_path):
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcdefgh"])
    loop = build(tmp_path, adb, max_steps=4)
    stats = loop.run()

    assert stats.triples == 4
    assert stats.stop_reason == "step budget exhausted"
    triples = loop.session.read_triples()
    assert len(triples) == 4
    assert [t.step for t in triples] == [0, 1, 2, 3]


def test_consecutive_triples_share_an_observation(tmp_path):
    """The chain is what proves screens are stored once, not twice."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcdef"])
    loop = build(tmp_path, adb, max_steps=3)
    loop.run()
    triples = loop.session.read_triples()
    for earlier, later in zip(triples, triples[1:], strict=False):
        assert earlier.after == later.before


def test_the_action_record_is_exp08_shaped(tmp_path):
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcd"])
    loop = build(tmp_path, adb, max_steps=2)
    loop.run()
    for triple in loop.session.read_triples():
        assert "action" in triple.action
        if triple.action["action"] == "click":
            x, y = triple.action["coordinate"]
            assert 0 <= x <= 1080 and 0 <= y <= 2400


def test_a_no_op_action_is_recorded_rather_than_dropped(tmp_path):
    """A world model learning that an action does nothing is learning something
    real. What to train on is export's decision, not the loop's."""
    adb = FakeAdb([Script(screen(tag="same"))])
    loop = build(tmp_path, adb, max_steps=3)
    stats = loop.run()
    assert stats.triples == 3
    assert stats.unchanged == 3
    assert all(not t.changed for t in loop.session.read_triples())


def test_screen_size_is_read_from_the_device(tmp_path):
    adb = FakeAdb([Script(screen())])
    loop = build(tmp_path, adb, max_steps=1)
    loop.run()
    assert (loop._width, loop._height) == (1080, 2400)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def test_the_time_budget_stops_the_run(tmp_path):
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcdefghij"])
    loop = build(tmp_path, adb, max_steps=0, max_duration_sec=0.4)
    stats = loop.run()
    assert stats.stop_reason == "time budget exhausted"
    assert stats.triples >= 1


def test_a_completed_budget_marks_the_session_complete(tmp_path):
    """`completed_at` is the skip signal, so it may only be set on a clean end."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcd"])
    loop = build(tmp_path, adb, max_steps=2)
    loop.run()
    assert loop.session.is_complete


# ---------------------------------------------------------------------------
# Recovery — the loop's real job
# ---------------------------------------------------------------------------


def test_a_brief_excursion_into_another_package_is_collected_not_recovered():
    """An app's flow routinely spans packages.

    Measured on the device: the Settings search screen is
    `com.google.android.settings.intelligence` in both the view tree and
    topResumedActivity. Under strict package equality Settings recovered on its
    first screen and collected zero triples, which is why tolerance exists.
    """
    assert MAX_STEPS_OUTSIDE >= 1


def test_a_short_foreign_sub_flow_does_not_trigger_recovery(tmp_path):
    scripts = [Script(screen())]
    scripts += [
        Script(screen(package="com.other.helper", tag=str(i)), "com.other.helper/.Sub")
        for i in range(MAX_STEPS_OUTSIDE)
    ]
    adb = FakeAdb(scripts)
    loop = build(tmp_path, adb, max_steps=MAX_STEPS_OUTSIDE)
    stats = loop.run()
    assert stats.recoveries == 0, "a sub-flow is part of the app's experience"
    assert stats.steps_outside >= 1


def test_persistent_drift_into_another_package_does_recover(tmp_path):
    """What tolerance must NOT do is let the run wander off forever."""
    adb = FakeAdb(
        [
            Script(screen()),
            *[
                Script(screen(package="com.android.chrome", tag=str(i)), "com.android.chrome/.Main")
                for i in range(MAX_STEPS_OUTSIDE + 3)
            ],
        ]
    )
    loop = build(tmp_path, adb, max_steps=MAX_STEPS_OUTSIDE + 3)
    stats = loop.run()
    assert stats.recoveries >= 1
    assert adb.launches >= 2, "launched at start, then relaunched"


def test_a_launcher_excursion_is_not_recorded_as_a_graph_edge(tmp_path):
    """Otherwise the router plans routes over a transition only a mis-tap
    reproduces."""
    adb = FakeAdb(
        [
            Script(screen()),
            Script(
                screen(package="com.google.android.apps.nexuslauncher"),
                "com.google.android.apps.nexuslauncher/.Home",
            ),
            Script(screen(tag="back")),
        ]
    )
    loop = build(tmp_path, adb, max_steps=3)
    loop.run()

    # The launcher screen is never persisted, so it has no page — the thing to
    # prove is that the action taken just before the drift did not acquire an
    # edge to wherever recovery happened to land.
    assert loop.stats.recoveries >= 1
    recorded = {
        destination
        for transitions in loop.explorer.memory.edges.values()
        for destination in transitions.values()
    }
    assert recorded <= set(loop.registry.pages), (
        "every recorded edge must point at a page that was actually observed"
    )
    assert len(loop.registry) == 1, "only the app's own screen became a page"


def test_the_launcher_counts_as_leaving_the_app(tmp_path):
    adb = FakeAdb(
        [
            Script(screen()),
            Script(
                screen(package="com.google.android.apps.nexuslauncher"),
                "com.google.android.apps.nexuslauncher/.Home",
            ),
        ]
    )
    loop = build(tmp_path, adb, max_steps=2)
    assert loop.run().recoveries >= 1


# ---------------------------------------------------------------------------
# Cold vs resumed launches
# ---------------------------------------------------------------------------


def test_a_session_starts_the_app_cold(tmp_path):
    """A LAUNCHER intent resumes the app's existing task. Measured consequence:
    a session inherited Settings sitting on GMS's OctarineActivity, read every
    observation as "outside the app", and collected 10 triples before giving up.
    """
    adb = FakeAdb([Script(screen())])
    build(tmp_path, adb, max_steps=1).run()
    assert adb.actions[0] == ("launch", (PKG, True))


def test_recovering_from_persistent_drift_restarts_cold(tmp_path):
    """Soft is provably a no-op here: the foreign activity can be sitting on top
    of OUR task, and a LAUNCHER intent then resumes it straight back."""
    adb = FakeAdb(
        [
            Script(screen()),
            *[
                Script(screen(package="com.android.chrome", tag=str(i)), "com.android.chrome/.Main")
                for i in range(MAX_STEPS_OUTSIDE + 3)
            ],
        ]
    )
    stats = build(tmp_path, adb, max_steps=MAX_STEPS_OUTSIDE + 3).run()
    assert stats.recoveries >= 1
    assert adb.clean_launches >= 2, "the start plus every drift recovery"
    assert ("launch", (PKG, True)) in adb.actions[1:]


def test_recovering_from_the_launcher_resumes_rather_than_restarts(tmp_path):
    """The app's task is intact in the background, so resuming keeps the deep
    state exploration reached. Restarting would throw it away for nothing."""
    adb = FakeAdb(
        [
            Script(screen()),
            Script(
                screen(package="com.google.android.apps.nexuslauncher"),
                "com.google.android.apps.nexuslauncher/.Home",
            ),
            Script(screen(tag="back")),
        ]
    )
    stats = build(tmp_path, adb, max_steps=3).run()
    assert stats.recoveries >= 1
    assert ("launch", (PKG, False)) in adb.actions[1:], "the launcher recovery is soft"
    assert adb.clean_launches == 1, "only the session start was cold"


def test_repeated_dump_failures_relaunch_rather_than_crash(tmp_path):
    adb = FakeAdb([Script(screen())], fail_dumps=3)
    loop = build(tmp_path, adb, max_steps=2)
    stats = loop.run()
    assert stats.observe_failures == 3
    assert stats.recoveries >= 1


def test_a_run_that_cannot_stay_in_the_app_gives_up(tmp_path):
    """Spending the rest of a 2h budget on an app that will not stay open costs
    the whole schedule."""
    adb = FakeAdb(
        [
            Script(
                screen(package="com.google.android.apps.nexuslauncher"),
                "com.google.android.apps.nexuslauncher/.Home",
            )
        ]
    )
    loop = build(tmp_path, adb, max_steps=0, max_duration_sec=30)
    stats = loop.run()
    assert stats.stop_reason == "could not stay in the app"
    assert stats.recoveries == MAX_CONSECUTIVE_RECOVERIES
    assert not loop.session.is_complete, "giving up is not completion"


def test_an_empty_screen_still_advances_instead_of_spinning(tmp_path):
    adb = FakeAdb([Script(EMPTY)])
    loop = build(tmp_path, adb, max_steps=2)
    stats = loop.run()
    assert stats.exhausted >= 1
    assert ("key", ("KEYCODE_BACK",)) in adb.actions


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_coverage_is_recorded_when_a_tracker_is_supplied(tmp_path):
    tracker = ActivityCoverage(package=PKG, declared={ACT})
    adb = FakeAdb([Script(screen(tag=t)) for t in "abc"])
    loop = build(tmp_path, adb, max_steps=2, coverage=tracker)
    loop.run()
    assert tracker.unique_visited == 1
    assert tracker.coverage == 1.0


def test_input_text_failure_never_fails_the_run(tmp_path):
    def explode(_element):
        raise RuntimeError("no API key")

    xml = (
        '<hierarchy rotation="0">'
        f'<node class="android.widget.EditText" package="{PKG}" enabled="true" '
        'visible-to-user="true" bounds="[0,0][100,100]"><node text="field"/></node>'
        "</hierarchy>"
    )
    adb = FakeAdb([Script(xml)])
    loop = build(tmp_path, adb, max_steps=2, text_for=explode)
    loop.run()
    assert any(kind == "text" for kind, _ in adb.actions)


def test_stats_land_in_metadata(tmp_path):
    import json

    adb = FakeAdb([Script(screen(tag=t)) for t in "abcd"])
    loop = build(tmp_path, adb, max_steps=2)
    stats = loop.run()
    meta = json.loads(loop.session.metadata_path.read_text())
    assert meta["triples"] == stats.triples
    assert meta["stop_reason"] == stats.stop_reason
    assert meta["pages"] >= 1
