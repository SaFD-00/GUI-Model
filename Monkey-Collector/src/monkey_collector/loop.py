"""The host-pull collection loop: the thing that actually produces triples.

Everything else in this package is a component; this is where they meet.
ARCHITECTURE §3 is the specification of one step:

    stabilize (screenshots) -> dump (once) -> page match -> semantic label
      -> explorer picks an action -> execute over ADB -> next observation

and the two observations either side of that action become one triple, plus one
AIG edge (§6). The sibling collector's ``loop.py`` is the structural reference —
the recovery ladder below is its measured behaviour, ported — but exploration,
the graph and the semantic layer are this project's own (AGENTS §0).

WHY THE LOOP OWNS RECOVERY
==========================

Autonomous exploration leaves the app. It taps a link and lands in a browser, it
hits Home, it opens a share sheet, it crashes something. None of those are
errors — they are the normal weather of driving a real device — but every one of
them produces a screen that must NOT enter the corpus as if it were the app's,
and must not enter the AIG as a routable transition.

So the loop, not the explorer, decides what is on screen and whether it counts.
:meth:`CollectionLoop._recover` returns the run to the app and drops the pending
action, because the alternative is an edge ("tapping X leads to the launcher")
that only a mis-tap can reproduce and that :class:`~monkey_collector.explore.Navigator`
would then dutifully plan routes over.

WHAT IS NOT MARKED EXPLORED
===========================

Coverage is written by :meth:`~monkey_collector.aig.AIG.record_transition` alone,
which runs only when the NEXT observation is attributable to the action. An
action that drifted the run out of the app is therefore left untried rather than
recorded as explored — the safe direction (AGENTS §2(f)): the corpus can contain
a retry, but it can never contain an action marked explored that was never
performed.

GRAPH DURABILITY
================

``graph.json`` is written after every observation that changed it, not once at
the end. It is a few kB against a step that costs seconds on real hardware
(`uiautomator dump` alone is 2.34 s on the Pixel 6), so the write is noise in
the budget — and a session killed mid-flight, which is exactly when the graph is
most wanted, still leaves one on disk.

RESUME DOES NOT RESTORE THE GRAPH
=================================

A resumed session starts a FRESH :class:`~monkey_collector.aig.AIG` and replaces
``graph.json``. :meth:`AIG.load` exists, but page ids are minted by
:class:`~monkey_collector.pagematch.PageRegistry`, whose state is not persisted:
a fresh registry mints ``"0"`` for whatever screen it sees first, which is not
in general the loaded node ``"0"``. Loading would therefore attribute this run's
edges and coverage to the previous run's pages, marking untried actions
explored — the one failure mode that is invisible in the collected data. The
observations and ``triples.jsonl`` (the corpus) resume normally; only the
parallel graph restarts.

WHY A TIME BUDGET RATHER THAN A STEP BUDGET
===========================================

Step cost is not constant: a screen that settles in two polls costs ~3.4 s, one
that never settles costs the full stabilization wait. Budgeting in steps makes
the wall-clock cost of a 48-app run unpredictable; budgeting in time makes it
exact and lets the per-step cost improve without re-planning the schedule.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from monkey_collector.aig import AIG
from monkey_collector.domain.actions import PressBack
from monkey_collector.explore import (
    ActionType,
    Element,
    Explorer,
    elements_of,
    signature_label,
    to_domain_action,
)
from monkey_collector.pagematch import PageRegistry, ScreenState
from monkey_collector.session import Observation, Session
from monkey_collector.stabilize import wait_for_stable_screen
from monkey_collector.text_input import SAMPLE_TEXTS, RandomTextGenerator

if TYPE_CHECKING:  # pragma: no cover - types only
    from monkey_collector.adb import AdbClient
    from monkey_collector.domain.activity_coverage import ActivityCoverageTracker
    from monkey_collector.domain.cost_tracker import CostTracker
    from monkey_collector.llm.client import LLMClient
    from monkey_collector.semantic import SemanticLabeler
    from monkey_collector.text_input import TextGenerator

#: Package prefixes that mean "we are no longer in the app". Kept as prefixes
#: because launcher implementations differ across devices and skins.
_LAUNCHER_HINTS = ("com.google.android.apps.nexuslauncher", "com.android.launcher")

#: Consecutive failed observations before the app is relaunched. Low because a
#: failed dump usually means the app is gone, not that the device is busy.
MAX_OBSERVE_FAILURES = 3

#: Consecutive recoveries before the session is abandoned. A run that cannot
#: stay in the app is not collecting anything; spending the remaining budget on
#: it costs the whole schedule.
MAX_CONSECUTIVE_RECOVERIES = 5

#: Consecutive observations under a FOREIGN package tolerated before recovering.
#:
#: Strict package equality is the obvious rule and it is wrong. An Android app's
#: flow routinely spans packages: measured on the target device, the Settings
#: search screen is `com.google.android.settings.intelligence` in BOTH the view
#: tree and the resumed activity, and a share sheet, account picker or WebView
#: helper behaves the same way. Under strict equality Settings recovered on its
#: very first screen and collected zero triples. Those screens are genuinely
#: part of the app's experience and belong in the corpus; what does not is
#: DRIFT — wandering into a browser and never coming back. Tolerating a few
#: steps separates the two.
#:
#: Deliberately the loop's OWN constant rather than
#: ``exploration.max_steps_outside``: that knob is the explorer's (how long
#: before it proposes Back), this one is the corpus's (how long before a screen
#: stops being collected at all).
MAX_STEPS_OUTSIDE = 3

#: The :class:`~monkey_collector.explore.Decision` reasons that mean "the
#: frontier was empty" — every branch of ARCHITECTURE §5.1 that fires because
#: nothing was left to try, rather than because something was.
#:
#: This is the Monkey analogue of the sibling collector's single ``exhausted``
#: reason, and it must be reason-based rather than element-based: that collector
#: falls back to BACK, so a dead step there carries no element, while
#: ``Explorer._fallback`` here re-taps a RANDOM element of the screen. Keying
#: the stall counter on "no element" would therefore never fire on the very
#: screen the counter exists for — a static one that still has buttons.
EXHAUSTED_REASONS = frozenset(
    {"escape_repeated_frame", "return_to_app", "fallback_back", "fallback_random"}
)

#: Consecutive dead steps before the session is abandoned. A step is dead when
#: the explorer had nothing left to try (:data:`EXHAUSTED_REASONS`) AND the
#: screen did not change -- i.e. the run is poking a screen that will not move.
#:
#: Measured on the sibling collector: Markor opens on a 5-page onboarding
#: carousel whose "next" control the explorer marks explored after a handful of
#: taps. BACK does nothing there. It then pressed BACK **647 times in a row**,
#: producing 658 triples of which 2 changed, and would have burnt its entire 2h
#: budget for data that export drops as unchanged. Ending early hands the budget
#: to the other 47 apps.
#:
#: 30 is far above any legitimate run of dead ends: backing out of a deep stack
#: is also an exhausted step, but it CHANGES the screen and so resets the
#: counter. Only a screen that refuses to move counts.
MAX_STALLED_STEPS = 30

#: Pause between tapping a text field and typing into it, so the field has
#: focus before the keystrokes arrive.
_TEXT_FOCUS_DELAY_SEC = 0.3


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
    restarts: int = 0
    #: Observations collected under a foreign package — a sub-flow, not drift.
    steps_outside: int = 0
    observe_failures: int = 0
    exhausted: int = 0
    not_stabilized: int = 0
    pages: int = 0
    edges: int = 0
    #: Distinct structures the semantic layer queried for, NOT steps (§5.3).
    llm_calls: int = 0
    cost_usd: float = 0.0
    elapsed_sec: float = 0.0
    stop_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "observations": self.observations,
            "triples": self.triples,
            "unchanged_triples": self.unchanged,
            "recoveries": self.recoveries,
            "restarts": self.restarts,
            "steps_outside": self.steps_outside,
            "observe_failures": self.observe_failures,
            "exhausted_steps": self.exhausted,
            "not_stabilized": self.not_stabilized,
            "pages": self.pages,
            "edges": self.edges,
            "llm_calls": self.llm_calls,
            "cost_usd": round(self.cost_usd, 6),
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
    """Drives one app to its budget, writing observations, triples and the AIG."""

    def __init__(
        self,
        adb: AdbClient,
        session: Session,
        *,
        registry: PageRegistry | None = None,
        graph: AIG | None = None,
        explorer: Explorer | None = None,
        labeler: SemanticLabeler | None = None,
        coverage: ActivityCoverageTracker | None = None,
        text_generator: TextGenerator | None = None,
        llm_client: LLMClient | None = None,
        cost_tracker: CostTracker | None = None,
        max_duration_sec: float = 7200.0,
        max_steps: int = 0,
        action_delay_ms: int = 1500,
        stabilize: dict[str, Any] | None = None,
        launch_settle_sec: float = 2.0,
        sibling_packages: set[str] | None = None,
        seed: int = 42,
    ) -> None:
        self.adb = adb
        self.session = session
        self.registry = registry or PageRegistry()
        self.graph = graph if graph is not None else AIG(package=session.package)
        self.explorer = explorer or Explorer(self.graph, session.package, seed=seed)
        self.labeler = labeler
        self.coverage = coverage
        #: Input text for SET_TEXT comes from ``text_input.py`` — there is no
        #: constant fallback path, because a corpus of one hardcoded string is
        #: worse than one of canned samples and indistinguishable from a bug.
        self.text_generator = text_generator or RandomTextGenerator(random.Random(seed))
        self._fallback_rng = random.Random(seed)
        #: Only for step attribution in ``cost.csv``; the loop never queries.
        self.llm_client = llm_client
        self.cost_tracker = cost_tracker
        self.max_duration_sec = max_duration_sec
        self.max_steps = max_steps
        self.action_delay_ms = action_delay_ms
        self.stabilize_kwargs = stabilize or {}
        #: How long to wait after a launch or relaunch before the first
        #: observation. An app needs a moment to draw; tests set it to 0.
        self.launch_settle_sec = launch_settle_sec
        #: The OTHER apps in the collection catalog. Their screens are never this
        #: app's, however the run reached them -- see the sibling branch in
        #: `run` for why they are treated harder than any other foreign package.
        self.sibling_packages = (sibling_packages or set()) - {session.package}
        self.stats = LoopStats()

        #: ``{root}/raw/{package}/graph.json`` (§6.1) — the session already
        #: resolves that directory, so the path is derived rather than
        #: re-plumbed through a second root argument.
        self.graph_path = session.root / "graph.json"
        # Whether labelling was ACTUALLY active, not whether it was configured
        # (§5.2): a missing API key with `semantic_labeling: true` must not
        # write `true` into the field that exists to detect exactly that.
        self.graph.semantic_labeling = labeler.active if labeler is not None else False
        self.graph.package = session.package

        self._width = 1080
        self._height = 2400
        #: The action taken on the PREVIOUS observation, still awaiting the
        #: screen that will become its result.
        self._pending_action: dict[str, Any] | None = None
        self._pending_reason = ""
        self._pending_element: Element | None = None
        self._pending_type: ActionType | None = None
        self._pending_page: str | None = None

    # -- observation ---------------------------------------------------------

    def _observe(self) -> _Frame | None:
        """Settle the screen, take the one dump, and derive identity.

        Returns None on a failure the caller should treat as "we may have lost
        the app" rather than as a fatal error.
        """
        try:
            settled = wait_for_stable_screen(self.adb, **self.stabilize_kwargs)
            raw_xml = self.adb.dump_ui()
        except Exception as error:  # noqa: BLE001 - any ADB failure is recoverable
            logger.warning("observation failed: {}", error)
            return None

        activity = self._current_activity()
        try:
            # The package is DERIVED from the dump, never asserted from the
            # session: passing the session package here would make `_is_outside`
            # structurally unable to fire, since the screen would always claim
            # to be ours no matter what is actually on it.
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
        """The resumed activity, or "" — never an exception: it is a label."""
        try:
            return self.adb.get_current_activity()
        except Exception:  # noqa: BLE001 - the activity is a label, never fatal
            return ""

    def _persist(self, frame: _Frame) -> tuple[Observation, bool]:
        """Write the observation and everything derived from it (§3 steps 3-5).

        Returns the observation and whether it raised activity coverage, which
        the explorer needs and only the loop is holding the tracker for.
        """
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
        # Once per observation: `note_page` counts a visit, so it is not an
        # existence check (that is `_ensure_node`'s job, inside record_transition).
        self.graph.note_page(
            match.page_key,
            activity=frame.activity,
            package=frame.package,
            state_str=frame.state.state_str,
            structure_str=frame.state.structure_str,
            observation=observation.index,
        )
        new_activity = self._record_coverage(frame.activity)
        if self.labeler is not None:
            # After note_page: the labeller hangs a title on an EXISTING node
            # and never mints one (AGENTS §2(b)).
            self.labeler.observe(frame.state, match.page_key, frame.elements, self.graph)
        return observation, new_activity

    def _record_coverage(self, activity: str) -> bool:
        """Record the activity; True when it was one we had not seen."""
        if self.coverage is None:
            return False
        before = self.coverage.get_visited_count()
        self.coverage.record(activity, self.session.step_count)
        return self.coverage.get_visited_count() > before

    def _save_graph(self) -> None:
        try:
            self.graph.save(self.graph_path)
        except OSError as error:  # noqa: BLE001 - a graph write must not end a run
            logger.warning("could not write {}: {}", self.graph_path, error)

    # -- action execution ----------------------------------------------------

    def _execute(self, element: Element, action: ActionType, raw_xml: str) -> dict[str, Any]:
        """Perform the action and return its ``domain/actions.py`` record.

        The record is the DOMAIN serialization (ARCHITECTURE §9), not the EXP08
        wire format: export translates names and rescales coordinates at M5, and
        recording the wire form here would bake a frame the collector cannot
        undo. The recorded text is likewise the ORIGINAL string, never the
        shell-escaped one — escaping is transport, and wrong to train on.
        """
        text = self._text_for(element, raw_xml) if action is ActionType.SET_TEXT else ""
        payload = to_domain_action(
            element, action, screen_height=self._height, text=text
        ).to_dict()
        x, y = element.center

        if action is ActionType.TOUCH:
            self.adb.tap(x, y)
        elif action is ActionType.LONG_TOUCH:
            self.adb.long_press(x, y, duration_ms=payload["duration_ms"])
        elif action is ActionType.SCROLL:
            self.adb.swipe(
                payload["x1"],
                payload["y1"],
                payload["x2"],
                payload["y2"],
                duration_ms=payload["duration_ms"],
            )
        elif action is ActionType.SET_TEXT:
            self.adb.tap(x, y)
            if _TEXT_FOCUS_DELAY_SEC:
                time.sleep(_TEXT_FOCUS_DELAY_SEC)
            self.adb.input_text(payload["text"])
            # Dismiss the IME so the next observation is the page, not a
            # keyboard covering two thirds of it. ESCAPE rather than BACK: the
            # latter would pop the activity on a screen where it is the exit.
            self.adb.hide_keyboard()
        else:  # pragma: no cover - ActionType has no other member
            raise ValueError(f"unhandled action {action!r}")
        return payload

    def _text_for(self, element: Element, raw_xml: str) -> str:
        """Input text from ``text_input.py``. Never fails a run."""
        try:
            screen_xml = self._screen_for_prompt(raw_xml)
            return str(
                self.text_generator.generate(
                    resource_id=element.resource_id,
                    content_desc="",
                    current_text=element.text,
                    display_name=signature_label(element.signature),
                    screen_xml=screen_xml,
                )
            )
        except Exception as error:  # noqa: BLE001 - never fail a run over input text
            logger.warning("input-text generation failed ({}), using a sample", error)
            # A canned sample, never one hardcoded constant: a corpus whose
            # every typed string is identical is indistinguishable from a bug.
            return self._fallback_rng.choice(SAMPLE_TEXTS)

    @staticmethod
    def _screen_for_prompt(raw_xml: str) -> str:
        """The dump encoded for a prompt, or "" when it cannot be encoded.

        ``parse_device_xml_absolute``, never ``parse_device_xml``: the latter
        resizes every box into the 840x1876 EXPORT frame, which this call site
        would then have to thread width/height through for, to answer a
        question (what text belongs in this field) that reads no coordinates.
        """
        from monkey_collector.xml import parse_device_xml_absolute

        try:
            return parse_device_xml_absolute(raw_xml)
        except Exception:  # noqa: BLE001 - a prompt without the screen still works
            return ""

    def _press_back(self) -> dict[str, Any]:
        self.adb.press_back()
        return PressBack().to_dict()

    # -- recovery ------------------------------------------------------------

    def _is_sibling(self, frame: _Frame) -> bool:
        """Whether this screen belongs to another app the catalog collects."""
        return frame.package in self.sibling_packages

    def _is_outside(self, frame: _Frame) -> bool:
        """Whether this screen belongs to a package other than the target's."""
        package = frame.package
        return bool(package) and package != self.session.package

    def _abandon_pending(self) -> None:
        """Drop the un-attributed action and any route planned over it.

        Without this, a crash-and-relaunch is recorded as a normal edge from the
        page we left to wherever recovery landed, and the navigator later plans
        routes over a transition that only a mis-tap can reproduce.
        """
        self._pending_action = None
        self._pending_reason = ""
        self._pending_element = None
        self._pending_type = None
        self._pending_page = None
        self.explorer.navigator.clear()

    def _leave_sibling(self, package: str, *, hard: bool) -> None:
        """Get out of another target app, preferring BACK over a restart.

        BACK first because the handoff usually pushed the sibling onto OUR task,
        so popping it leaves this app exactly where exploration had reached.
        Restarting throws that away, and it showed on the sibling collector:
        org.tasks handed off to Joplin 298 times in one session, and
        cold-restarting every time meant the run kept re-exploring its opening
        screens -- its last 100 triples produced 15 changed, down from 65%
        earlier in the same session.

        `hard` is for a sibling that is STILL there on the next observation.
        Measured why that case is needed: Joplin hands off to Markor's
        IntroActivity, which does not honour BACK at all.
        """
        if hard:
            self._recover(f"handed off to {package} again", clean=True)
            return
        self.stats.recoveries += 1
        self._abandon_pending()
        logger.info("recovering (handed off to {}) — pressing back", package)
        self.adb.press_back()

    def _recover(self, reason: str, *, clean: bool = False) -> None:
        """Return to the app and forget the pending action.

        ``clean`` decides between resuming the app's task and restarting it cold
        (see :meth:`AdbClient.launch_app`). The default is to resume, because a
        recovery that keeps the app's deep state keeps exploring where it left
        off; the callers that pass ``clean=True`` are the ones where resuming is
        provably a no-op.
        """
        self.stats.recoveries += 1
        self._abandon_pending()
        logger.info(
            "recovering ({}) — {} {}",
            reason,
            "restarting" if clean else "relaunching",
            self.session.package,
        )
        try:
            self.adb.launch_app(self.session.package, clean=clean)
        except Exception as error:  # noqa: BLE001 - try a reconnect before giving up
            logger.warning("relaunch failed ({}), reconnecting", error)
            self.adb.reconnect()
            try:
                self.adb.launch_app(self.session.package, clean=clean)
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
        """Collect until the budget runs out. Always writes metadata and graph."""
        try:
            size = self.adb.wm_size()
            self._width, self._height = size.as_tuple()
        except Exception as error:  # noqa: BLE001 - defaults are already sane
            logger.warning(
                "could not read wm size ({}), assuming {}x{}", error, self._width, self._height
            )
        # Recorded with the session rather than left for export to guess: a
        # dialog's window is legitimately smaller than the display, so deriving
        # the frame from a dump confuses "partial window" with "wrong
        # resolution" (§8.3 — measured, it dropped 23% of one app's triples).
        self.graph.device_width, self.graph.device_height = self._width, self._height

        # Cold, not resumed. A LAUNCHER intent resumes whatever task the app
        # already had, so a session would otherwise inherit wherever the last
        # run -- or a person -- left the app, and the first screen of the corpus
        # would depend on the device's history rather than on the app.
        self.adb.launch_app(self.session.package, clean=True)
        if self.launch_settle_sec:
            time.sleep(self.launch_settle_sec)

        previous: Observation | None = None
        failures = 0
        consecutive_recoveries = 0
        steps_outside = 0
        stalled = 0
        consecutive_siblings = 0

        while True:
            reason = self._budget_left()
            if reason:
                self.stats.stop_reason = reason
                break

            if self.llm_client is not None:
                self.llm_client.set_step(self.session.step_count)

            frame = self._observe()
            if frame is None:
                failures += 1
                self.stats.observe_failures += 1
                if failures >= MAX_OBSERVE_FAILURES:
                    failures = 0
                    consecutive_recoveries += 1
                    self._recover(
                        "observation kept failing", clean=consecutive_recoveries > 1
                    )
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

            # Another TARGET app is not a helper this app hands off to; it is a
            # whole other app, and its screens must never be attributed to this
            # one. Measured on the sibling collector: Joplin reached Markor 84
            # times through a file-open handoff, and 95 of its 136 changed
            # triples had a Markor screen on one side. Because export holds OOD
            # out BY APP and an exported record carries no package, those would
            # be an undetectable leak of a held-out app into train.
            #
            # So this recovers at once AND skips the persist, which the
            # tolerated branch below deliberately does not: at MAX_STEPS_OUTSIDE
            # the first foreign frame is still written before the count is checked.
            if self._is_sibling(frame):
                steps_outside = 0
                consecutive_siblings += 1
                consecutive_recoveries += 1
                self._leave_sibling(frame.package, hard=consecutive_siblings > 1)
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
                    # Cold at once, not after a wasted soft attempt: when the
                    # foreign activity sits in OUR task (measured: GMS's
                    # OctarineActivity inside the Settings task), a LAUNCHER
                    # intent resumes that task and lands right back on it.
                    self._recover(
                        f"stuck outside the app (in {frame.package})", clean=True
                    )
                    previous = None
                    if consecutive_recoveries >= MAX_CONSECUTIVE_RECOVERIES:
                        self.stats.stop_reason = "could not stay in the app"
                        break
                    continue
                self.stats.steps_outside += 1
            else:
                steps_outside = 0
                consecutive_recoveries = 0
                consecutive_siblings = 0

            current, new_activity = self._persist(frame)

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
                if (
                    self._pending_element is not None
                    and self._pending_type is not None
                    and self._pending_page is not None
                ):
                    self.graph.record_transition(
                        self._pending_page,
                        self._pending_element.signature,
                        self._pending_type,
                        current.page_key,
                        domain_action=self._pending_action,
                        step=triple.step,
                        semantic_element=signature_label(self._pending_element.signature)
                        or None,
                    )
                # A dead step: nothing left to try, against a screen that did
                # not move. Backing out of a deep stack is also exhausted, but
                # it CHANGES the screen and so resets the counter.
                if not triple.changed and self._pending_reason in EXHAUSTED_REASONS:
                    stalled += 1
                else:
                    stalled = 0
                if stalled >= MAX_STALLED_STEPS:
                    self.stats.stop_reason = "made no progress"
                    self._save_graph()
                    break

            self._save_graph()

            decision = self.explorer.select(
                current.page_key,
                frame.state,
                frame.elements,
                groups_by_page=self.labeler.groups_by_page if self.labeler else None,
                new_activity=new_activity,
            )
            # FIRST, before is_fallback: a restart decision carries no element
            # either, so the naive order would silently degrade it to a Back.
            if decision.restart:
                self.stats.restarts += 1
                self._recover(decision.reason, clean=True)
                previous = None
                continue
            if decision.is_fallback:
                self.stats.exhausted += 1
                self._pending_action = self._press_back()
                self._pending_reason = decision.reason
                self._pending_element = None
                self._pending_type = None
                self._pending_page = None
            else:
                assert decision.element is not None and decision.action is not None
                try:
                    self._pending_action = self._execute(
                        decision.element, decision.action, frame.raw_xml
                    )
                    self._pending_reason = decision.reason
                    self._pending_element = decision.element
                    self._pending_type = decision.action
                    self._pending_page = current.page_key
                except Exception as error:  # noqa: BLE001 - a failed tap is recoverable
                    logger.warning("action failed: {}", error)
                    self._abandon_pending()

            previous = current
            if self.action_delay_ms:
                time.sleep(self.action_delay_ms / 1000.0)

        self.stats.pages = len(self.registry)
        self.stats.edges = len(self.graph.edge_records)
        self.stats.llm_calls = self.graph.llm_calls
        if self.cost_tracker is not None:
            self.stats.cost_usd = self.cost_tracker.get_total_cost()
        self.stats.elapsed_sec = self.session.elapsed_sec
        self._save_graph()
        completed = self.stats.stop_reason.endswith("exhausted")
        self.session.write_metadata(
            completed=completed,
            extra={
                **self.stats.as_dict(),
                "device_width": self._width,
                "device_height": self._height,
                # Whether the model actually labelled, not whether it was asked
                # to (§5.2). Two runs of one app diverge without it and nothing
                # else records why.
                "semantic_labeling": self.graph.semantic_labeling,
            },
        )
        logger.info(
            "{}: {} triples over {} observations, {} pages, {} edges, {} llm calls, {} — {}",
            self.session.package,
            self.stats.triples,
            self.stats.observations,
            self.stats.pages,
            self.stats.edges,
            self.stats.llm_calls,
            f"{self.stats.elapsed_sec:.0f}s",
            self.stats.stop_reason,
        )
        return self.stats


__all__ = [
    "EXHAUSTED_REASONS",
    "MAX_CONSECUTIVE_RECOVERIES",
    "MAX_OBSERVE_FAILURES",
    "MAX_STALLED_STEPS",
    "MAX_STEPS_OUTSIDE",
    "CollectionLoop",
    "LoopStats",
    "is_launcher",
]
