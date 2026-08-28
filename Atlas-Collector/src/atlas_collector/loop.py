"""The host-pull collection loop: the thing that actually produces triples.

Everything else in this package is a component; this is where they meet. One
step is:

    stabilize (screenshots)  ->  dump (once)  ->  page match  ->  coverage
      ->  explorer picks an action  ->  execute over ADB  ->  next observation

and the two observations either side of that action become one triple.

WHY THE LOOP OWNS RECOVERY
==========================

Autonomous exploration leaves the app. It taps a link and lands in a browser, it
hits Home, it opens a share sheet, it crashes something. None of those are
errors — they are the normal weather of driving a real device — but every one of
them produces a screen that must NOT enter the corpus as if it were the app's,
and must not enter the page graph as a routable transition.

So the loop, not the explorer, decides what is on screen and whether it counts.
:meth:`CollectionLoop._recover` returns the run to the app and tells the explorer
to forget its pending action, because the alternative is an edge in the graph
("tapping X leads to the launcher") that only a mis-tap can reproduce and that
the router will then dutifully plan routes over.

WHY A TIME BUDGET RATHER THAN A STEP BUDGET
===========================================

Step cost is not constant: a screen that settles in two polls costs ~3.4 s, one
that never settles costs the full stabilization wait. Budgeting in steps makes
the wall-clock cost of a 48-app run unpredictable; budgeting in time makes it
exact and lets the per-step cost improve without re-planning the schedule.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from atlas_collector.explore import ActionType, Element, Explorer, elements_of
from atlas_collector.pagematch import PageRegistry, ScreenState
from atlas_collector.session import Observation, Session
from atlas_collector.stabilize import wait_for_stable_screen

if TYPE_CHECKING:
    from atlas_collector.adb import AdbClient
    from atlas_collector.coverage import ActivityCoverage

#: Package prefixes that mean "we are no longer in the app". Kept as prefixes
#: because launcher implementations differ across devices and skins.
_LAUNCHER_HINTS = ("com.google.android.apps.nexuslauncher", "com.android.launcher")

#: Consecutive failed observations before the app is relaunched. Low because a
#: failed dump usually means the app is gone, not that the device is busy.
MAX_OBSERVE_FAILURES = 3

#: Consecutive recoveries before the session is abandoned. A run that cannot stay
#: in the app is not collecting anything; spending the remaining budget on it
#: costs the whole schedule.
MAX_CONSECUTIVE_RECOVERIES = 5

#: Consecutive observations under a FOREIGN package tolerated before recovering,
#: from the reference's ``MAX_NUM_STEPS_OUTSIDE`` (input_policy3.py:31).
#:
#: Strict package equality is the obvious rule and it is wrong. An Android app's
#: flow routinely spans packages: measured on the target device, the Settings
#: search screen is `com.google.android.settings.intelligence` in BOTH the view
#: tree and `topResumedActivity`, and a share sheet, account picker or WebView
#: helper behaves the same way. Under strict equality Settings recovered on its
#: very first screen and collected zero triples. Those screens are genuinely
#: part of the app's experience and belong in the corpus; what does not is
#: DRIFT — wandering into a browser and never coming back. Tolerating a few
#: steps separates the two.
MAX_STEPS_OUTSIDE = 3

#: Swipe geometry as a fraction of the screen, for SCROLL actions.
_SCROLL_FROM_Y = 0.75
_SCROLL_TO_Y = 0.35


def is_launcher(package: str | None) -> bool:
    if not package:
        return False
    return any(package.startswith(hint) for hint in _LAUNCHER_HINTS)


@dataclass
class LoopStats:
    """What a session did. Written into metadata.json and logged at the end."""

    steps: int = 0
    observations: int = 0
    triples: int = 0
    unchanged: int = 0
    recoveries: int = 0
    #: Observations collected under a foreign package — a sub-flow, not drift.
    steps_outside: int = 0
    observe_failures: int = 0
    exhausted: int = 0
    not_stabilized: int = 0
    pages: int = 0
    elapsed_sec: float = 0.0
    stop_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "observations": self.observations,
            "triples": self.triples,
            "unchanged_triples": self.unchanged,
            "recoveries": self.recoveries,
            "steps_outside": self.steps_outside,
            "observe_failures": self.observe_failures,
            "exhausted_steps": self.exhausted,
            "not_stabilized": self.not_stabilized,
            "pages": self.pages,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "stop_reason": self.stop_reason,
        }


@dataclass
class _Frame:
    """A settled screen plus everything derived from it, before it is written."""

    png: bytes
    raw_xml: str
    activity: str
    package: str
    stabilized: bool
    state: ScreenState
    elements: list[Element] = field(default_factory=list)


class CollectionLoop:
    """Drives one app to its budget, writing observations and triples."""

    def __init__(
        self,
        adb: AdbClient,
        session: Session,
        *,
        registry: PageRegistry | None = None,
        explorer: Explorer | None = None,
        coverage: ActivityCoverage | None = None,
        text_for: Any = None,
        max_duration_sec: float = 7200.0,
        max_steps: int = 0,
        action_delay_ms: int = 1500,
        stabilize: dict[str, Any] | None = None,
        launch_settle_sec: float = 2.0,
    ) -> None:
        self.adb = adb
        self.session = session
        self.registry = registry or PageRegistry()
        self.explorer = explorer or Explorer()
        self.coverage = coverage
        #: Callable(Element, raw_xml) -> str for SET_TEXT. None uses a constant,
        #: so the loop runs with no API key and no network.
        self.text_for = text_for
        self.max_duration_sec = max_duration_sec
        self.max_steps = max_steps
        self.action_delay_ms = action_delay_ms
        self.stabilize_kwargs = stabilize or {}
        #: How long to wait after a launch or relaunch before the first
        #: observation. An app needs a moment to draw; tests set it to 0.
        self.launch_settle_sec = launch_settle_sec
        self.stats = LoopStats()

        self._width = 1080
        self._height = 2400
        #: The action taken on the PREVIOUS observation, still awaiting the
        #: screen that will become its result.
        self._pending_action: dict[str, Any] | None = None
        self._pending_reason = ""

    # -- observation ---------------------------------------------------------

    def _observe(self) -> _Frame | None:
        """Settle the screen, take the one dump, and derive identity.

        Returns None on a failure the caller should treat as "we may have lost
        the app" rather than as a fatal error.
        """
        try:
            settled = wait_for_stable_screen(self.adb, **self.stabilize_kwargs)
            raw_xml = self.adb.dump_ui()
        except Exception as error:  # noqa: BLE001 - any ADB/parse failure is recoverable
            logger.warning("observation failed: {}", error)
            return None

        activity = self._current_activity()
        try:
            # The package is DERIVED from the dump, never asserted from the
            # session: passing the session package here would make
            # `_left_the_app` structurally unable to fire, since the screen
            # would always claim to be ours no matter what is actually on it.
            state = ScreenState.from_dump(raw_xml, activity)
            elements = elements_of(raw_xml)
        except Exception as error:  # noqa: BLE001 - a malformed dump is recoverable
            logger.warning("could not parse the dump: {}", error)
            return None

        if not settled.settled:
            self.stats.not_stabilized += 1
        return _Frame(
            png=settled.png,
            raw_xml=raw_xml,
            activity=activity,
            package=state.package,
            stabilized=settled.settled,
            state=state,
            elements=elements,
        )

    def _current_activity(self) -> str:
        try:
            out = self.adb.shell(
                "dumpsys activity activities | grep -m1 topResumedActivity"
            )
        except Exception:  # noqa: BLE001 - the activity is a label, never fatal
            return ""
        for token in out.split():
            if "/" in token and not token.endswith(":"):
                return token
        return ""

    def _persist(self, frame: _Frame) -> Observation:
        match = self.registry.classify(frame.state)
        observation = self.session.write_observation(
            png=frame.png,
            raw_xml=frame.raw_xml,
            page_key=match.page_key,
            activity=frame.activity,
            state_str=frame.state.state_str,
            is_new_page=match.is_new,
            match_kind=match.kind.value,
            stabilized=frame.stabilized,
        )
        self.stats.observations += 1
        if self.coverage is not None:
            self.coverage.record(frame.activity, step=self.session.step_count)
        return observation

    # -- action execution ----------------------------------------------------

    def _execute(self, element: Element, action: ActionType) -> dict[str, Any]:
        """Perform the action and return its JSON record for the triple.

        The recorded text is the ORIGINAL string, never the shell-escaped form —
        the escaped form is a transport detail and would be wrong to train on.
        """
        x, y = element.center
        if action is ActionType.TOUCH:
            self.adb.tap(x, y)
            return {"action": "click", "coordinate": [x, y]}
        if action is ActionType.LONG_TOUCH:
            self.adb.shell(f"input swipe {x} {y} {x} {y} 800")
            return {"action": "long_press", "coordinate": [x, y]}
        if action is ActionType.SCROLL:
            x1, y1 = x, int(self._height * _SCROLL_FROM_Y)
            x2, y2 = x, int(self._height * _SCROLL_TO_Y)
            self.adb.swipe(x1, y1, x2, y2)
            return {"action": "swipe", "coordinate1": [x1, y1], "coordinate2": [x2, y2]}
        if action is ActionType.SET_TEXT:
            text = self._text_for(element)
            self.adb.tap(x, y)
            time.sleep(0.3)
            self.adb.text(text)
            # Dismiss the IME so the next observation is the page, not a keyboard
            # covering two thirds of it.
            self.adb.key("KEYCODE_ESCAPE")
            return {"action": "type", "text": text}
        raise ValueError(f"unhandled action {action!r}")

    def _text_for(self, element: Element) -> str:
        if self.text_for is None:
            return "test"
        try:
            return str(self.text_for(element))
        except Exception as error:  # noqa: BLE001 - never fail a run over input text
            logger.warning("input-text generation failed ({}), using a constant", error)
            return "test"

    def _press_back(self) -> dict[str, Any]:
        self.adb.key("KEYCODE_BACK")
        return {"action": "navigate_back"}

    # -- recovery ------------------------------------------------------------

    def _is_outside(self, frame: _Frame) -> bool:
        """Whether this screen belongs to a package other than the target's."""
        package = frame.package
        return bool(package) and package != self.session.package

    def _recover(self, reason: str) -> None:
        """Return to the app and forget the pending action.

        Forgetting matters more than the relaunch: an un-attributed action would
        otherwise be recorded as an edge to wherever recovery landed, and the
        router would plan routes over a transition only a mis-tap can reproduce.
        """
        self.stats.recoveries += 1
        self.explorer.abandon_pending()
        logger.info("recovering ({}) — relaunching {}", reason, self.session.package)
        try:
            self.adb.launch_app(self.session.package)
        except Exception as error:  # noqa: BLE001 - try a reconnect before giving up
            logger.warning("relaunch failed ({}), reconnecting", error)
            self.adb.reconnect()
            try:
                self.adb.launch_app(self.session.package)
            except Exception as second:  # noqa: BLE001
                logger.error("relaunch failed again: {}", second)
        if self.launch_settle_sec:
            time.sleep(self.launch_settle_sec)

    # -- budget --------------------------------------------------------------

    def _budget_left(self) -> str:
        if self.max_steps and self.session.step_count >= self.max_steps:
            return "step budget exhausted"
        if self.session.elapsed_sec >= self.max_duration_sec:
            return "time budget exhausted"
        return ""

    # -- the loop ------------------------------------------------------------

    def run(self) -> LoopStats:
        """Collect until the budget runs out. Always writes metadata."""
        try:
            size = self.adb.wm_size()
            self._width, self._height = size.as_tuple()
        except Exception as error:  # noqa: BLE001 - defaults are already sane
            logger.warning("could not read wm size ({}), assuming {}x{}",
                           error, self._width, self._height)

        self.adb.launch_app(self.session.package)
        if self.launch_settle_sec:
            time.sleep(self.launch_settle_sec)

        previous: Observation | None = None
        failures = 0
        consecutive_recoveries = 0
        steps_outside = 0

        while True:
            reason = self._budget_left()
            if reason:
                self.stats.stop_reason = reason
                break

            frame = self._observe()
            if frame is None:
                failures += 1
                self.stats.observe_failures += 1
                if failures >= MAX_OBSERVE_FAILURES:
                    failures = 0
                    consecutive_recoveries += 1
                    self._recover("observation kept failing")
                    previous = None
                    if consecutive_recoveries >= MAX_CONSECUTIVE_RECOVERIES:
                        self.stats.stop_reason = "could not stay in the app"
                        break
                continue
            failures = 0

            # The launcher is never part of an app's flow, so it recovers at
            # once. A foreign package might be (see MAX_STEPS_OUTSIDE), so it is
            # collected and only recovered from once it persists.
            if is_launcher(frame.package):
                steps_outside = 0
                consecutive_recoveries += 1
                self._recover("drifted to the launcher")
                previous = None
                if consecutive_recoveries >= MAX_CONSECUTIVE_RECOVERIES:
                    self.stats.stop_reason = "could not stay in the app"
                    break
                continue

            if self._is_outside(frame):
                steps_outside += 1
                if steps_outside > MAX_STEPS_OUTSIDE:
                    steps_outside = 0
                    consecutive_recoveries += 1
                    self._recover(f"stuck outside the app (in {frame.package})")
                    previous = None
                    if consecutive_recoveries >= MAX_CONSECUTIVE_RECOVERIES:
                        self.stats.stop_reason = "could not stay in the app"
                        break
                    continue
                self.stats.steps_outside += 1
            else:
                steps_outside = 0
                consecutive_recoveries = 0

            current = self._persist(frame)
            self.explorer.observe(current.page_key)

            # Close the previous step: the screen we just observed IS its result.
            if previous is not None and self._pending_action is not None:
                triple = self.session.write_triple(
                    before=previous,
                    after=current,
                    action=self._pending_action,
                    reason=self._pending_reason,
                )
                self.stats.triples += 1
                self.stats.steps += 1
                if not triple.changed:
                    self.stats.unchanged += 1

            decision = self.explorer.select(current.page_key, frame.elements)
            if decision.is_fallback:
                self.stats.exhausted += 1
                self._pending_action = self._press_back()
                self._pending_reason = decision.reason
            else:
                assert decision.element is not None and decision.action is not None
                try:
                    self._pending_action = self._execute(decision.element, decision.action)
                    self._pending_reason = decision.reason
                except Exception as error:  # noqa: BLE001 - a failed tap is recoverable
                    logger.warning("action failed: {}", error)
                    self.explorer.abandon_pending()
                    self._pending_action = None
                    self._pending_reason = ""

            previous = current
            if self.action_delay_ms:
                time.sleep(self.action_delay_ms / 1000.0)

        self.stats.pages = len(self.registry)
        self.stats.elapsed_sec = self.session.elapsed_sec
        completed = self.stats.stop_reason.endswith("exhausted")
        self.session.write_metadata(completed=completed, extra=self.stats.as_dict())
        logger.info(
            "{}: {} triples over {} observations, {} pages, {} — {}",
            self.session.package,
            self.stats.triples,
            self.stats.observations,
            self.stats.pages,
            f"{self.stats.elapsed_sec:.0f}s",
            self.stats.stop_reason,
        )
        return self.stats


__all__ = [
    "MAX_CONSECUTIVE_RECOVERIES",
    "MAX_OBSERVE_FAILURES",
    "MAX_STEPS_OUTSIDE",
    "CollectionLoop",
    "LoopStats",
    "is_launcher",
]
