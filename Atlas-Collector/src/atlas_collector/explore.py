"""Coverage-guided exploration, ported from LLM-Explorer.

Each step answers one question: which (page, element, action) has never been
tried, and how do we get there? The order is the reference's
``generate_event_based_on_utg`` (input_policy3.py:1199), reduced to the four
branches that carry the algorithm:

    1. continue an in-flight navigation plan
    2. take an unexplored action on the CURRENT page
    3. plan a shortest path to an unexplored action on ANOTHER page, and start it
    4. fall back — Back, or a tap when Back would leave the app

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
:func:`~atlas_collector.pagematch.view_signature` maps 12 clickable elements onto
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

WHAT IS NOT PORTED
==================

The reference also carries ``same_function_element_groups`` (LLM-derived) and a
pandas ``action_effects`` table that skips actions whose siblings always did the
same thing. Both are omitted: the first needs the LLM on the identity path,
which this project forbids, and the second was measured in the sibling project
to be attacking a bottleneck that was not there.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from xml.etree import ElementTree as ET

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


class ActionType(StrEnum):
    """The action vocabulary. Mapped to ADB calls by the collection loop."""

    TOUCH = "touch"
    LONG_TOUCH = "long_touch"
    SET_TEXT = "set_text"
    SCROLL = "scroll"


#: Ranking within the unexplored pool. A tap opens the most new pages; a scroll
#: reveals off-screen frontier; typing rarely leaves the page at all. Used only
#: to break ties deterministically — it never overrides "unexplored first".
_ACTION_RANK = {
    ActionType.TOUCH: 3,
    ActionType.SCROLL: 2,
    ActionType.SET_TEXT: 1,
    ActionType.LONG_TOUCH: 0,
}


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

    Distinct from :func:`~atlas_collector.pagematch.view_signature`, which keys
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
    #: Raw-dump document order. Only for logging — NEVER for identity, since it
    #: shifts whenever the tree changes.
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


# ---------------------------------------------------------------------------
# Memory: what has been explored, and how the pages connect
# ---------------------------------------------------------------------------

#: One frontier candidate.
Candidate = tuple[str, "Element", ActionType]


@dataclass
class Memory:
    """Coverage and the transition graph, keyed on ``page_key``.

    The coverage unit is ``(page_key, element_signature, action_type)``. Keying
    on the PAGE rather than the screen instance is the whole point: an action
    tried once on a page is known-tried on every later visit, which is what lets
    coverage saturate instead of the explorer re-walking the same buttons.
    """

    #: page_key -> {(signature, action)} actually performed.
    explored: dict[str, set[tuple[str, ActionType]]] = field(default_factory=dict)
    #: page_key -> {(signature, action)} that navigation could not re-locate.
    #: Kept apart from `explored` because these are ROUTING failures, not
    #: coverage: the action may still be perfectly reachable directly.
    nav_failed: dict[str, set[tuple[str, ActionType]]] = field(default_factory=dict)
    #: from_page -> action -> to_page, the graph navigation routes over.
    edges: dict[str, dict[tuple[str, ActionType], str]] = field(default_factory=dict)
    #: Every page seen, in first-visit order.
    seen_pages: list[str] = field(default_factory=list)

    def note_page(self, page_key: str) -> None:
        if page_key not in self.seen_pages:
            self.seen_pages.append(page_key)

    def mark_explored(self, page_key: str, signature: str, action: ActionType) -> None:
        self.explored.setdefault(page_key, set()).add((signature, action))

    def mark_nav_failed(self, page_key: str, signature: str, action: ActionType) -> None:
        self.nav_failed.setdefault(page_key, set()).add((signature, action))

    def record_transition(
        self, from_page: str, signature: str, action: ActionType, to_page: str
    ) -> None:
        """Mark the action explored and remember where it led."""
        self.mark_explored(from_page, signature, action)
        self.edges.setdefault(from_page, {})[(signature, action)] = to_page
        self.note_page(to_page)

    def is_blocked(self, page_key: str, signature: str, action: ActionType) -> bool:
        pair = (signature, action)
        return pair in self.explored.get(page_key, ()) or pair in self.nav_failed.get(
            page_key, ()
        )

    def unexplored(self, page_key: str, elements: list[Element]) -> list[Candidate]:
        """Frontier of *page_key* given the elements currently on screen.

        ``long_touch`` is withheld until the element's ``touch`` has been tried,
        as in the reference (input_policy3.py:1069): a long press rarely has an
        independent effect, so spending a step on it before the tap is waste.
        """
        candidates: list[Candidate] = []
        for element in elements:
            touched = (element.signature, ActionType.TOUCH) in self.explored.get(
                page_key, ()
            )
            for action in element.allowed_actions:
                if self.is_blocked(page_key, element.signature, action):
                    continue
                if action is ActionType.LONG_TOUCH and not touched:
                    continue
                candidates.append((page_key, element, action))
        return candidates

    def shortest_path(self, start: str, goal: str) -> list[tuple[str, str, ActionType]]:
        """BFS route from *start* to *goal* as ``(page, signature, action)`` steps.

        Empty when already there; empty ALSO when unreachable — the caller must
        distinguish those two with ``start == goal``, and :class:`Navigator` does.
        """
        if start == goal:
            return []
        queue: deque[tuple[str, list[tuple[str, str, ActionType]]]] = deque([(start, [])])
        visited = {start}
        while queue:
            page, path = queue.popleft()
            for (signature, action), destination in self.edges.get(page, {}).items():
                if destination in visited:
                    continue
                step = [*path, (page, signature, action)]
                if destination == goal:
                    return step
                visited.add(destination)
                queue.append((destination, step))
        return []


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
    """

    memory: Memory
    _queue: list[tuple[str, str, ActionType]] = field(default_factory=list)
    _steps: int = 0

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
            route = self.memory.shortest_path(current_page, page_key)
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
        """
        if not self._queue:
            return None
        if self._steps >= MAX_NAVIGATE_STEPS:
            self.clear()
            return None

        page_key, signature, action = self._queue[0]
        if current_page != page_key:
            self.clear()
            return None

        match = next((e for e in elements if e.signature == signature), None)
        if match is None or action not in match.allowed_actions:
            self.memory.mark_nav_failed(page_key, signature, action)
            self.clear()
            return None

        self._queue.pop(0)
        self._steps += 1
        return match, action


# ---------------------------------------------------------------------------
# The explorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """What to do this step, and why — the ``reason`` is logged for auditing."""

    element: Element | None
    action: ActionType | None
    reason: str

    @property
    def is_fallback(self) -> bool:
        return self.element is None


class Explorer:
    """Selects one action per step. Pure decision-making: it touches no device.

    Keeping ADB out of this class is what makes the algorithm testable without
    hardware — every test below drives it with fixture XML.
    """

    def __init__(self, memory: Memory | None = None) -> None:
        self.memory = memory or Memory()
        self.navigator = Navigator(self.memory)
        self._pending: tuple[str, str, ActionType] | None = None

    def observe(self, page_key: str) -> None:
        """Attribute the previous action now that its result is visible.

        Attribution is deferred to the NEXT observation on purpose: the outcome
        of an action is the page it produced, which is not knowable when the
        action is chosen.
        """
        self.memory.note_page(page_key)
        if self._pending is not None:
            from_page, signature, action = self._pending
            self.memory.record_transition(from_page, signature, action, page_key)
            self._pending = None

    def abandon_pending(self) -> None:
        """Drop the un-attributed action after an excursion (relaunch, recovery).

        Without this, a crash-and-relaunch is recorded as a normal edge from the
        page we left to the app's launch page, and the router later plans routes
        over a transition that only a crash can reproduce.
        """
        self._pending = None

    def select(self, page_key: str, elements: list[Element]) -> Decision:
        """Choose this step's action. See the module docstring for the order."""
        if self.navigator.is_navigating:
            step = self.navigator.next_action(page_key, elements)
            if step is not None:
                return self._commit(page_key, *step, reason="navigate")

        local = self.memory.unexplored(page_key, elements)
        if local:
            element, action = self._best(local)
            return self._commit(page_key, element, action, reason="explore")

        if self.navigator.plan(page_key, self._global_frontier()):
            step = self.navigator.next_action(page_key, elements)
            if step is not None:
                return self._commit(page_key, *step, reason="navigate")

        self._pending = None
        return Decision(None, None, reason="exhausted")

    def _best(self, candidates: list[Candidate]) -> tuple[Element, ActionType]:
        """Highest-ranked candidate; document order breaks ties.

        Deterministic rather than random (the reference uses ``random.choice``)
        so a session replays identically from its event log — which is what makes
        a collected corpus auditable after the fact.
        """
        _, element, action = max(
            candidates,
            key=lambda c: (_ACTION_RANK.get(c[2], 0), -c[1].index),
        )
        return element, action

    def _global_frontier(self) -> list[Candidate]:
        """Unexplored actions on OTHER pages, reconstructed from the graph.

        Only actions whose element was seen on an edge are recoverable, because
        the elements of a page we are not standing on are not in hand. That is a
        real limit inherited from the reference and it is why navigation targets
        are re-matched on arrival rather than trusted.
        """
        frontier: list[Candidate] = []
        for page_key, transitions in self.memory.edges.items():
            for signature, action in transitions:
                if self.memory.is_blocked(page_key, signature, action):
                    continue
                frontier.append(
                    (page_key, Element(signature, (action,), (0, 0, 0, 0), -1), action)
                )
        return frontier

    def _commit(
        self, page_key: str, element: Element, action: ActionType, *, reason: str
    ) -> Decision:
        self._pending = (page_key, element.signature, action)
        return Decision(element, action, reason=reason)


__all__ = [
    "MAX_NAVIGATE_STEPS",
    "MAX_SUBTREE_TEXT",
    "ActionType",
    "Candidate",
    "Decision",
    "Element",
    "Explorer",
    "Memory",
    "Navigator",
    "element_signature",
    "elements_of",
    "parse_bounds",
    "subtree_text",
]
