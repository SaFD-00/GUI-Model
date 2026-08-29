"""Element layer and navigation for the LLM-Explorer policy.

This module is the DETERMINISTIC half of exploration: what is actionable on the
current screen, how an element is identified across visits, and how a planned
route is walked one step at a time. The policy that decides *which* frontier
entry to take next — and the semantic layer that prunes it — is M3b and lives
elsewhere; nothing here calls an LLM or touches a device.

WHICH XML DRIVES WHAT
=====================

Exploration reads the RAW ``uiautomator`` dump, never the html-like encoding.
The two serve different masters and conflating them causes quiet damage:

    raw uiautomator   -> exploration and page identity. Carries ``clickable``,
                         ``long-clickable``, ``scrollable``, ``bounds``,
                         ``resource-id`` natively — the facts needed to DRIVE.
    html-like         -> the export contract only (``data-bbox`` in the
                         840x1876 frame). It is a lossy view built for a model
                         to read, and its wrapper-collapsing and pruning are
                         wrong to route over.

ELEMENT IDENTITY MUST INCLUDE SUBTREE TEXT
==========================================

The reference identifies an element by ``(tag, bound_box)`` (``_classify_element``,
input_policy3.py:570). That is coordinate-DEPENDENT: the same row acquires a new
identity the moment the list scrolls, and the coverage learned before the scroll
is thrown away.

Keying on the node's OWN attributes instead is the obvious fix and it is a trap.
Measured on ``tests/fixtures/pages/settings_root.xml``, the node-level
:func:`~monkey_collector.pagematch.view_signature` maps 12 clickable elements onto
**3** distinct signatures — 10 of them collapse into a single
``[class]LinearLayout[resource_id]None[text]None``. Android list rows are
clickable CONTAINERS: the row has no id and no text of its own, and the label
lives in a child ``TextView``. Ten different Settings entries become one, and
exploration abandons the screen after three steps believing it is done.

So :func:`element_signature` adds the element's SUBTREE text — its own text and
content-desc plus its descendants'. That is what a user reads as the identity of
a row, it distinguishes siblings, and it survives scrolling because it travels
with the row rather than with its position. On the same fixture it recovers all
12.

The residual ambiguity is genuinely rare rather than merely asserted: two rows
identical in class, id AND all visible text. Those are indistinguishable to a
user too, so collapsing them costs a duplicate of the same control.

ARCHITECTURE §5.4 makes this ONE function the identity used in all three places
that need it: the AIG edge key, the unexplored-frontier dedup, and navigation
re-resolution. The reference's ``view['desc']`` string matching
(input_policy3.py:1475-1495) is deliberately NOT ported — a second identity
function that disagrees with this one makes re-resolution fail silently, and a
failed re-resolution is written to the nav-failed set, where it skips that
action forever while collection keeps looking healthy.

THE ACTION VOCABULARY IS NOT THE ACTION SPACE
=============================================

:class:`ActionType` is the explorer's internal vocabulary.
:func:`to_domain_action` translates it into the FIXED seven-type action space of
``domain/actions.py`` (ARCHITECTURE §9), which export then translates again into
EXP08 wire names. Three layers, translated at the boundaries, never merged.

The reference's ``select`` / ``unselect`` events execute as ``input tap``,
byte for byte the same ADB call as ``touch``. They therefore fold into
:attr:`ActionType.TOUCH` instead of earning types of their own — the action
space is a fixed contract and nothing may be added to it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from monkey_collector.config import ExplorationConfig
from monkey_collector.domain.actions import (
    Action,
    InputText,
    LongPress,
    PressBack,
    Swipe,
    Tap,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from collections.abc import Mapping, Sequence

    from monkey_collector.aig import AIG
    from monkey_collector.pagematch import ScreenState

#: Cap on consecutive navigation steps before a plan is abandoned, from
#: ``MAX_NAVIGATE_NUM_AT_ONE_TIME`` (input_policy3.py:44).
MAX_NAVIGATE_STEPS = 10

_TRUE = "true"

#: Classes whose taps open the IME rather than navigating. Treated as text
#: fields so ``set_text`` is offered instead of a bare tap.
_EDITABLE_HINTS = ("EditText", "AutoCompleteTextView", "SearchView")

#: Cap on the subtree text folded into an element signature. Long prose is
#: content, not identity — the same guard the reference applies to node text
#: (device_state.py:272), applied to the subtree.
MAX_SUBTREE_TEXT = 120

#: A scroll is a swipe over the SCREEN, not over the scrollable element's own
#: box: dragging inside a short container barely moves the list. Fractions of
#: the device height, as the sibling collector uses.
SCROLL_FROM_Y = 0.75
SCROLL_TO_Y = 0.35


class ActionType(str, Enum):
    """The explorer's action vocabulary, translated by :func:`to_domain_action`.

    Python 3.10 has no ``enum.StrEnum`` (the reference used 3.11+), so this is a
    plain ``str``/``Enum`` mixin instead — the same substitution
    :class:`~monkey_collector.pagematch.MergePolicy` makes, for the same reason.
    ``==``, ``.value`` and JSON encoding behave identically either way, but
    ``str(member)`` does not: a bare mixin returns ``"ActionType.TOUCH"`` where
    ``StrEnum`` returns ``"touch"``. The explicit ``__str__`` below closes that
    gap so a log line or a CSV cell reads the same under either Python version.
    """

    TOUCH = "touch"
    LONG_TOUCH = "long_touch"
    SET_TEXT = "set_text"
    SCROLL = "scroll"

    def __str__(self) -> str:
        return self.value


#: Explorer vocabulary -> ``domain/actions.py`` ``action_type`` (ARCHITECTURE §9).
_DOMAIN_TYPE_OF: dict[ActionType, str] = {
    ActionType.TOUCH: "tap",
    ActionType.LONG_TOUCH: "long_press",
    ActionType.SET_TEXT: "input_text",
    ActionType.SCROLL: "swipe",
}

#: The inverse. Needed because the AIG stores the DOMAIN action in its edges
#: (ARCHITECTURE §6) while keying them on the explorer's :class:`ActionType`, so
#: loading a saved graph has to invert the map to rebuild the key.
_ACTION_TYPE_OF: dict[str, ActionType] = {v: k for k, v in _DOMAIN_TYPE_OF.items()}


def domain_action_type(action: ActionType) -> str:
    """``ActionType`` -> the ``domain/actions.py`` ``action_type`` string."""
    return _DOMAIN_TYPE_OF[action]


def action_type_from_domain(domain_type: str) -> ActionType:
    """``domain/actions.py`` ``action_type`` -> :class:`ActionType`.

    Raises for the domain types no explorer action maps onto (``press_back``,
    ``press_home``, ``open_app``). Those are real actions the loop can take, but
    they carry no element and so cannot be an AIG edge key.
    """
    try:
        return _ACTION_TYPE_OF[domain_type]
    except KeyError:
        raise ValueError(f"no ActionType maps onto domain action {domain_type!r}") from None


def subtree_text(node: ET.Element) -> str:
    """The element's own and its descendants' text and content-desc, in order.

    This is the label a user reads for a row, and it is what makes sibling rows
    distinguishable — see the module docstring for the measured collapse it
    prevents. Truncated at :data:`MAX_SUBTREE_TEXT` so a long body of prose does
    not become identity.
    """
    parts: list[str] = []
    for descendant in node.iter():
        for attribute in ("text", "content-desc"):
            value = (descendant.get(attribute) or "").strip()
            if value and value not in parts:
                parts.append(value)
    joined = "|".join(parts)
    return joined[:MAX_SUBTREE_TEXT]


def element_signature(node: ET.Element) -> str:
    """Coordinate-free identity of an actionable element.

    Distinct from :func:`~monkey_collector.pagematch.view_signature`, which keys
    PAGE identity on single nodes. Element identity needs the subtree because an
    Android list row is a clickable container whose label lives in a child.
    """
    return "[class]{}[resource_id]{}[label]{}[{},{}]".format(
        node.get("class") or "None",
        node.get("resource-id") or "None",
        subtree_text(node) or "None",
        "checked" if node.get("checked") == _TRUE else "",
        "selected" if node.get("selected") == _TRUE else "",
    )


def signature_label(signature: str) -> str:
    """The human-readable label inside an :func:`element_signature`.

    COSMETIC ONLY — this exists so the semantic layer can put a readable name in
    a prompt without a second identity function or a new field on
    :class:`Element`. It lives here rather than in the consumer so that the
    signature's layout stays knowledge of exactly one module. Never route,
    dedup, or key anything on the result; the signature itself is the identity.
    """
    head = signature.find("[label]")
    if head < 0:
        return ""
    body = signature[head + len("[label]") :]
    tail = body.rfind("[")
    if tail >= 0:
        body = body[:tail]
    return "" if body == "None" else body


def parse_bounds(raw: str) -> tuple[int, int, int, int]:
    """``[l,t][r,b]`` -> ``(l, t, r, b)``; ``(0, 0, 0, 0)`` when unparsable."""
    digits: list[int] = []
    number = ""
    for char in raw or "":
        if char.isdigit() or (char == "-" and not number):
            number += char
        elif number:
            digits.append(int(number))
            number = ""
    if number:
        digits.append(int(number))
    if len(digits) != 4:
        return (0, 0, 0, 0)
    return (digits[0], digits[1], digits[2], digits[3])


@dataclass(frozen=True)
class Element:
    """One actionable element of the current screen."""

    signature: str
    allowed_actions: tuple[ActionType, ...]
    bounds: tuple[int, int, int, int]
    #: Raw-dump document order. Only for logging and the recorded
    #: ``element_index`` — NEVER for identity, since it shifts whenever the tree
    #: changes.
    index: int
    text: str = ""
    resource_id: str = ""
    class_name: str = ""

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)

    @property
    def is_degenerate(self) -> bool:
        """Zero-area: on screen in the tree, but nothing to tap."""
        left, top, right, bottom = self.bounds
        return right <= left or bottom <= top


def _allowed_actions(node: ET.Element) -> tuple[ActionType, ...]:
    actions: list[ActionType] = []
    class_name = node.get("class", "")
    if any(hint in class_name for hint in _EDITABLE_HINTS):
        actions.append(ActionType.SET_TEXT)
    if node.get("clickable") == _TRUE:
        actions.append(ActionType.TOUCH)
    if node.get("long-clickable") == _TRUE:
        actions.append(ActionType.LONG_TOUCH)
    if node.get("scrollable") == _TRUE:
        actions.append(ActionType.SCROLL)
    return tuple(actions)


def elements_of(raw_xml: str) -> list[Element]:
    """Actionable elements of a raw uiautomator dump, in document order.

    Disabled and zero-area nodes are dropped here rather than at selection time:
    an element that cannot be acted on should never enter the frontier, because
    once there it is indistinguishable from one that simply has not been tried,
    and the explorer would return to it forever.
    """
    root = ET.fromstring(raw_xml)
    elements: list[Element] = []
    for index, node in enumerate(root.iter("node")):
        if node.get("enabled") == "false":
            continue
        actions = _allowed_actions(node)
        if not actions:
            continue
        element = Element(
            signature=element_signature(node),
            allowed_actions=actions,
            bounds=parse_bounds(node.get("bounds", "")),
            index=index,
            text=node.get("text", ""),
            resource_id=node.get("resource-id", ""),
            class_name=node.get("class", ""),
        )
        if element.is_degenerate:
            continue
        elements.append(element)
    return elements


#: One frontier candidate: the page it belongs to, the element, the action.
Candidate = tuple[str, "Element", ActionType]


# ---------------------------------------------------------------------------
# Vocabulary -> action space
# ---------------------------------------------------------------------------


def to_domain_action(
    element: Element | None,
    action: ActionType | None,
    *,
    screen_height: int = 0,
    text: str = "",
) -> Action:
    """Translate an explorer decision into the fixed action space (§9).

    ``element is None`` — the exhausted/fallback decision — becomes
    :class:`~monkey_collector.domain.actions.PressBack`, which is the only action
    that needs no element. Note that Back therefore has no ``element_signature``
    and so cannot be an AIG edge (ARCHITECTURE §6 keys edges on one).

    *text* is injected by the caller rather than generated here: input-text
    generation is ``text_input.py``'s job and may call the LLM, which this module
    never does.
    """
    if element is None or action is None:
        return PressBack()
    x, y = element.center
    if action is ActionType.TOUCH:
        return Tap(x=x, y=y, element_index=element.index)
    if action is ActionType.LONG_TOUCH:
        return LongPress(x=x, y=y, element_index=element.index)
    if action is ActionType.SET_TEXT:
        return InputText(text=text, x=x, y=y, element_index=element.index)
    if action is ActionType.SCROLL:
        if screen_height <= 0:
            raise ValueError("scroll needs a positive screen_height")
        return Swipe(
            x1=x,
            y1=int(screen_height * SCROLL_FROM_Y),
            x2=x,
            y2=int(screen_height * SCROLL_TO_Y),
            element_index=element.index,
        )
    raise ValueError(f"unhandled action {action!r}")


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


@dataclass
class Navigator:
    """Drives a shortest-path route one action per step.

    The plan is a queue of ``(page, signature, action)``. Each step is re-matched
    against the LIVE screen by signature rather than replayed by coordinate, so
    a route survives the layout shifting between visits. When the route drifts
    off course — we land on a page the plan did not expect — the plan is dropped
    and the engine replans, which is cheaper and safer than trying to patch it.

    The routing graph is the :class:`~monkey_collector.aig.AIG`: it is the one
    place that knows how pages connect, so it is also the one place that answers
    ``shortest_path`` and remembers a re-resolution failure.
    """

    graph: AIG
    _queue: list[tuple[str, str, ActionType]] = field(default_factory=list)
    _steps: int = 0
    #: Per-plan step budget (``exploration.max_navigate_steps``). Declared LAST
    #: so no positional construction shifts, and defaulted to the module
    #: constant so M3a's behaviour is unchanged when nobody passes it.
    max_steps: int = MAX_NAVIGATE_STEPS

    @property
    def is_navigating(self) -> bool:
        return bool(self._queue)

    def clear(self) -> None:
        self._queue = []
        self._steps = 0

    def plan(self, current_page: str, targets: list[Candidate]) -> bool:
        """Load the shortest route to any page in *targets*. True when planned."""
        best: list[tuple[str, str, ActionType]] | None = None
        best_target: Candidate | None = None
        for target in targets:
            page_key, element, action = target
            if page_key == current_page:
                continue
            route = self.graph.shortest_path(current_page, page_key)
            if not route:
                continue  # unreachable over the recorded graph
            plan = [*route, (page_key, element.signature, action)]
            if best is None or len(plan) < len(best):
                best, best_target = plan, target
        if best is None or best_target is None:
            return False
        self._queue = best
        self._steps = 0
        return True

    def next_action(
        self, current_page: str, elements: list[Element]
    ) -> tuple[Element, ActionType] | None:
        """Next step of the plan, re-matched on the live screen.

        Returns None — and abandons the plan — when the budget is spent, the
        route drifted, or the planned element is not on screen. In the last case
        the action is marked nav-failed so the router stops proposing a step it
        has proven it cannot take; it stays available to direct exploration,
        which is why nav-failure is tracked apart from coverage.

        Matching is by :func:`element_signature` alone, which is coordinate-free:
        a row that scrolled is still the same row, so the plan survives. That is
        the whole reason §5.4 forbids a second identity function.
        """
        if not self._queue:
            return None
        if self._steps >= self.max_steps:
            self.clear()
            return None

        page_key, signature, action = self._queue[0]
        if current_page != page_key:
            self.clear()
            return None

        match = next((e for e in elements if e.signature == signature), None)
        if match is None or action not in match.allowed_actions:
            self.graph.mark_nav_failed(page_key, signature, action)
            self.clear()
            return None

        self._queue.pop(0)
        self._steps += 1
        return match, action


@dataclass(frozen=True)
class Decision:
    """What to do this step, and why — the ``reason`` is logged for auditing."""

    element: Element | None
    action: ActionType | None
    reason: str
    #: Ask the caller to restart the app instead of acting on this screen.
    #: The policy never DRIVES the device — that is the collection loop's job —
    #: so a restart is expressed as a request and nothing here executes it.
    #: A restart decision carries no element, so a consumer that ignores this
    #: flag silently degrades it into a Back press; check it FIRST.
    restart: bool = False

    @property
    def is_fallback(self) -> bool:
        return self.element is None


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


class Explorer:
    """ARCHITECTURE §5.1's six branches, in order, one action per step.

    Nothing here calls an LLM or touches a device. The semantic layer's only
    entry point is *groups_by_page*, a plain mapping of page id to sets of
    element signatures, passed into :meth:`select`; that keeps the pruning rule
    testable without a model and keeps this module free of an import cycle.

    WHY ``long_touch`` IS SPLIT ACROSS TWO BRANCHES
    ===============================================

    Branch 4 (act on the current screen) drops ``long_touch`` outright, exactly
    as the reference's ``pick_target`` does (input_policy3.py:1409-1422): a long
    press seldom does anything a tap has not already done, so spending the
    cheapest slot on it is waste.

    That deferral is only a deferral because branch 5 considers the CURRENT page
    alongside the remote ones. The reference gets this by calling
    ``get_unexplored_actions(find_in_states=all_states(...))``, which includes
    the state it is standing on, and then appending the target action to a
    zero-hop route. A branch 5 restricted to other pages would turn "defer" into
    "never": branch 4 refuses it here, and every other page becomes "here" the
    moment you arrive. So a current-page candidate is executed immediately —
    a zero-hop route is trivially the shortest — and only if there is none does
    the navigator plan toward another page.

    STALE COORDINATES NEVER ESCAPE
    ==============================

    Remote candidates are built from the elements the graph REMEMBERS seeing on
    those pages, so their ``bounds`` and ``index`` are from an earlier visit and
    are meaningless now. They are safe because such a candidate is only ever a
    routing target: the element that reaches a :class:`Decision` always comes
    back from :meth:`Navigator.next_action`, which re-matches by signature
    against the live screen. Nothing may shortcut that — ``to_domain_action``
    reads ``element.center``, so one leak taps a coordinate from a screen that
    is no longer there and the corpus is wrong without any error.
    """

    def __init__(
        self,
        graph: AIG,
        package: str = "",
        *,
        config: ExplorationConfig | None = None,
        seed: int = 42,
    ) -> None:
        self.graph = graph
        self.package = package
        self.config = config or ExplorationConfig()
        #: Seeded from ``collection.seed`` so a run is reproducible. Every
        #: random choice in this class draws from here and nowhere else.
        self.rng = random.Random(seed)
        self.navigator = Navigator(graph, max_steps=self.config.max_navigate_steps)
        self._steps_outside = 0
        self._stagnation = 0
        self._frame: str | None = None
        self._frame_streak = 0

    # -- the six branches ----------------------------------------------------

    def select(
        self,
        page_id: str,
        state: ScreenState,
        elements: list[Element],
        *,
        groups_by_page: Mapping[str, Sequence[frozenset[str]]] | None = None,
        new_activity: bool = False,
    ) -> Decision:
        """Decide this step's action. First matching branch wins and returns.

        *new_activity* is whether this observation raised activity coverage; the
        collection loop owns the tracker, so it reports the delta rather than
        this module reaching for it.
        """
        groups = groups_by_page or {}

        # 0. Activity coverage has stalled: ask for a restart. The reference
        #    checks this before everything else (input_policy3.py:1220).
        if new_activity:
            self._stagnation = 0
        else:
            self._stagnation += 1
        if self._stagnation > self.config.max_activity_stagnation:
            self._stagnation = 0
            self.navigator.clear()
            return Decision(None, None, "restart_activity_stagnation", restart=True)

        # 1. Continue an in-flight route. next_action returns None when the plan
        #    drifted or died, having already cleared itself, so falling through
        #    replans on this same step rather than wasting one.
        if self.navigator.is_navigating:
            step = self.navigator.next_action(page_id, elements)
            if step is not None:
                return Decision(step[0], step[1], "navigate")

        # 2. Outside the app. Below the threshold we keep exploring what is on
        #    screen, as the reference does: a transient system surface often
        #    resolves itself, and Back on the first frame off-app throws away a
        #    step. Only a sustained absence is worth a Back.
        if not state.is_in_app(self.package):
            self._steps_outside += 1
            if self._steps_outside > self.config.max_steps_outside:
                self.navigator.clear()
                return Decision(None, None, "return_to_app")
        else:
            self._steps_outside = 0

        # 3. The same structure frame over and over: Back out.
        if state.structure_str == self._frame:
            self._frame_streak += 1
        else:
            self._frame = state.structure_str
            self._frame_streak = 1
        if self._frame_streak > self.config.max_explore_current_state:
            # DEVIATION from the reference, which never resets this counter and
            # therefore presses Back every step forever on a screen where Back
            # does nothing — burning the whole budget on one action. Resetting
            # buys the screen another full window before escaping again.
            self._frame_streak = 0
            self.navigator.clear()
            return Decision(None, None, "escape_repeated_frame")

        # Register the frontier of this page whatever branch wins below, so the
        # denominator of ``stats.unexplored_actions`` does not depend on which
        # one did.
        self.graph.note_elements(page_id, elements)

        if self.rng.random() > self.config.random_explore_prob:
            here = self._prune(self.graph.unexplored(page_id, elements), groups)

            # 4. Something untried right here, long_touch excepted.
            immediate = [c for c in here if c[2] is not ActionType.LONG_TOUCH]
            if immediate:
                _, element, action = self.rng.choice(immediate)
                return Decision(element, action, "explore_current")

            # 5. The whole known frontier, nearest first. Shuffling first is
            #    what makes ties random: Navigator.plan keeps a route only when
            #    it is STRICTLY shorter, so among equals the first wins.
            targets = [*here, *self._remote(page_id, groups)]
            self.rng.shuffle(targets)
            local = next((t for t in targets if t[0] == page_id), None)
            if local is not None:
                # Zero hops: nothing can be shorter. This is where a deferred
                # long_touch finally executes.
                return Decision(local[1], local[2], "explore_deferred")
            if targets and self.navigator.plan(page_id, targets):
                step = self.navigator.next_action(page_id, elements)
                if step is not None:
                    return Decision(step[0], step[1], "navigate_to_target")
                self.navigator.clear()

        # 6. Anything executable on this screen; Back when there is nothing.
        return self._fallback(elements)

    # -- helpers -------------------------------------------------------------

    def _prune(
        self,
        candidates: list[Candidate],
        groups_by_page: Mapping[str, Sequence[frozenset[str]]],
    ) -> list[Candidate]:
        """Drop candidates a same-function sibling has already covered (§5.2).

        Per ACTION TYPE, as the reference does (input_policy3.py:1049-1070):
        having tapped one row of a list says nothing about long-pressing
        another, so a group member's ``touch`` never suppresses a peer's
        ``long_touch``.

        Only ``explored`` counts, never ``nav_failed``: a routing failure means
        we could not GET there, which is no evidence about what the action does.
        """
        if not self.config.skip_similar_elements or not groups_by_page:
            return list(candidates)
        kept: list[Candidate] = []
        for page_id, element, action in candidates:
            group = next(
                (g for g in groups_by_page.get(page_id) or () if element.signature in g),
                None,
            )
            if group is not None and any(
                sibling != element.signature
                and self.graph.is_explored(page_id, sibling, action)
                for sibling in group
            ):
                continue
            kept.append((page_id, element, action))
        return kept

    def _remote(
        self,
        page_id: str,
        groups_by_page: Mapping[str, Sequence[frozenset[str]]],
    ) -> list[Candidate]:
        return self._prune(
            self.graph.unexplored_elsewhere(page_id, package=self.package),
            groups_by_page,
        )

    def _fallback(self, elements: list[Element]) -> Decision:
        """The reference's ``get_executable_action`` (input_policy3.py:1126).

        A random element, and a random one of its actions with ``long_touch``
        removed unless it is the only thing on offer.
        """
        if not elements:
            return Decision(None, None, "fallback_back")
        element = self.rng.choice(elements)
        actions = list(element.allowed_actions)
        if len(actions) > 1 and ActionType.LONG_TOUCH in actions:
            actions.remove(ActionType.LONG_TOUCH)
        if not actions:
            return Decision(None, None, "fallback_back")
        return Decision(element, self.rng.choice(actions), "fallback_random")


__all__ = [
    "MAX_NAVIGATE_STEPS",
    "MAX_SUBTREE_TEXT",
    "SCROLL_FROM_Y",
    "SCROLL_TO_Y",
    "ActionType",
    "Candidate",
    "Decision",
    "Element",
    "Explorer",
    "Navigator",
    "action_type_from_domain",
    "domain_action_type",
    "element_signature",
    "elements_of",
    "parse_bounds",
    "signature_label",
    "subtree_text",
    "to_domain_action",
]
