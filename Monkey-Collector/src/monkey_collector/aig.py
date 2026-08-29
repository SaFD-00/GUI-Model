"""AIG — the App Interaction Graph, and the coverage it carries.

ARCHITECTURE §6 is the specification; :meth:`AIG.to_dict` is the contract.
``{root}/raw/{package}/graph.json`` is a first-class deliverable of this
collector (the sibling project has no equivalent), so the schema is pinned by
test rather than left to drift.

NODES ARE PAGES, NOT SCREENS
===========================

A node is a page as ``pagematch.py`` decides it (§4), so several ``state_str``
values collapse into one node — hence the LIST. Keying coverage on the page
rather than the screen instance is the whole point: an action tried once on a
page is known-tried on every later visit, which is what lets coverage saturate
instead of the explorer re-walking the same buttons after every scroll.

EDGES ARE KEYED ON THE ELEMENT SIGNATURE, NOT COORDINATES
=========================================================

The key is ``(from_page, element_signature, action_type)`` (§5.4). A row that
scrolled is the same row, so it is the same edge. The stored ``action`` is the
``domain/actions.py`` serialization — coordinates included, because the triple
has to say where the tap actually landed — but the coordinates are payload, not
identity.

Note that the explorer's :class:`~monkey_collector.explore.ActionType` does not
appear in the JSON: §6's edge carries the DOMAIN action, whose ``action_type``
determines it. :func:`~monkey_collector.explore.action_type_from_domain` inverts
the map on load. Adding a redundant field would be a schema change.

WHAT SURVIVES save() -> load(), AND WHAT DOES NOT
================================================

§6 stores edges, not coverage sets. Restored from ``graph.json``:

    nodes, edges, same_function_groups, device, package, llm_calls, and
    ``explored`` — which is exactly the set of edge keys, because
    :meth:`record_transition` is the only thing that writes it.

NOT restored: ``nav_failed`` (routing failures), the UNTRIED part of ``known``,
and ``known_elements`` — the actionable elements of a page are only knowable
while standing on it, so the frontier's denominator has to be re-observed. The
consequence of the last one is concrete: after :meth:`load`, the explorer's
navigate-to-a-remote-target branch proposes nothing until pages are revisited,
because there is no remembered element to route toward. All three losses push
the SAFE way: a resumed session may re-try a route it had given up on, explore
only what is in front of it for a while, or under-report
``unexplored_actions``. None of them can mark an untried action explored, which
is the failure mode that would corrupt the corpus silently.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from monkey_collector.domain.actions import Action
from monkey_collector.explore import (
    ActionType,
    Candidate,
    Element,
    action_type_from_domain,
    domain_action_type,
)
from monkey_collector.paths import raw_app_dir

#: An edge key as it is stored per source page: what was done, on what.
EdgeKey = tuple[str, ActionType]


def graph_path(root: str | Path, package: str) -> Path:
    """``{root}/raw/{package}/graph.json`` — one graph per app per session."""
    return raw_app_dir(root, package) / "graph.json"


@dataclass
class AIGNode:
    """One page. Serializes as an ARCHITECTURE §6 ``nodes[]`` entry."""

    page_id: str
    activity: str = ""
    package: str = ""
    #: Every content-aware ``state_str`` merged into this page, in first-seen
    #: order. A list, not a set, because §6 says so and order is reproducible.
    state_strs: list[str] = field(default_factory=list)
    structure_strs: list[str] = field(default_factory=list)
    #: Human-readable name from ``semantic.py``; None until a state on this page
    #: has been labelled, and the structure hash when labelling is degraded.
    #: NEVER part of page identity (AGENTS §2(b)).
    semantic_title: str | None = None
    first_observation: int | None = None
    visits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": int(self.page_id),
            "activity": self.activity,
            "package": self.package,
            "state_strs": list(self.state_strs),
            "structure_strs": list(self.structure_strs),
            "semantic_title": self.semantic_title,
            "first_observation": self.first_observation,
            "visits": self.visits,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AIGNode:
        return cls(
            page_id=str(raw["page_id"]),
            activity=raw.get("activity", ""),
            package=raw.get("package", ""),
            state_strs=list(raw.get("state_strs") or []),
            structure_strs=list(raw.get("structure_strs") or []),
            semantic_title=raw.get("semantic_title"),
            first_observation=raw.get("first_observation"),
            visits=raw.get("visits", 0),
        )


@dataclass
class AIGEdge:
    """One ``(from_page, element_signature, action_type) -> to_page`` transition."""

    from_page: str
    to_page: str
    element_signature: str
    action_type: ActionType
    #: ``domain/actions.py`` serialization — NOT the EXP08 wire format (§8).
    #: Always carries at least ``action_type``, because that is what ``load``
    #: inverts to rebuild :attr:`action_type`.
    action: dict[str, Any] = field(default_factory=dict)
    semantic_element: str | None = None
    count: int = 0
    #: Did the action change the page? The page-level analogue of the
    #: reference's ``from_state.state_str != to_state.state_str``
    #: (input_policy3.py:789).
    effective: bool = False
    #: Session step numbers at which this edge was traversed.
    steps: list[int] = field(default_factory=list)

    @property
    def key(self) -> EdgeKey:
        return (self.element_signature, self.action_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_page": int(self.from_page),
            "to_page": int(self.to_page),
            "action": dict(self.action),
            "element_signature": self.element_signature,
            "semantic_element": self.semantic_element,
            "count": self.count,
            "effective": self.effective,
            "steps": list(self.steps),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AIGEdge:
        action = dict(raw.get("action") or {})
        return cls(
            from_page=str(raw["from_page"]),
            to_page=str(raw["to_page"]),
            element_signature=raw["element_signature"],
            action_type=action_type_from_domain(action.get("action_type", "")),
            action=action,
            semantic_element=raw.get("semantic_element"),
            count=raw.get("count", 0),
            effective=raw.get("effective", False),
            steps=list(raw.get("steps") or []),
        )


@dataclass
class SameFunctionGroup:
    """Audit record for the one place an LLM prunes exploration (§5.2).

    Written by :class:`~monkey_collector.semantic.SemanticLabeler`. §5.2 requires
    every group to be recorded WITH the state that minted it — a wrong grouping
    is invisible in the collected data (the action is simply never attempted),
    so if it is not written down when it is made it can never be found
    afterwards. ``source`` distinguishes ``"llm"`` from ``"none"``, which is how
    a reader tells pruning-was-off from pruning-found-nothing.

    A row with EMPTY ``members`` is a no-pruning-here marker rather than a
    group, emitted once for any labelled state that produced none. Do not read
    ``len(same_function_groups)`` as a group count.
    """

    minted_in_state: str
    page_id: str
    members: list[str] = field(default_factory=list)
    source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "minted_in_state": self.minted_in_state,
            "page_id": int(self.page_id),
            "members": list(self.members),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SameFunctionGroup:
        return cls(
            minted_in_state=raw.get("minted_in_state", ""),
            page_id=str(raw["page_id"]),
            members=list(raw.get("members") or []),
            source=raw.get("source", "none"),
        )


class AIG:
    """The page graph plus the coverage the explorer routes over.

    Page ids are the ``str`` keys ``pagematch.PageRegistry`` mints, and stay
    ``str`` all the way through the API so :class:`~monkey_collector.explore.Navigator`
    and :meth:`shortest_path` are literal ports. §6 spells them as JSON
    integers, so the conversion happens at the serialization boundary and
    nowhere else. It is unconditional ``int()`` on purpose: ``_mint`` is the only
    producer of page ids and always mints decimal strings, so anything else is a
    bug that should fail loudly rather than emit a differently-typed JSON.
    """

    def __init__(
        self,
        package: str = "",
        *,
        device_width: int = 0,
        device_height: int = 0,
        semantic_labeling: bool = False,
    ) -> None:
        self.package = package
        self.device_width = device_width
        self.device_height = device_height
        #: Whether semantic labelling was actually ACTIVE for this session (§5.2):
        #: two runs of the same app diverge without it and nothing else records why.
        self.semantic_labeling = semantic_labeling
        self.llm_calls = 0

        self.nodes: dict[str, AIGNode] = {}
        #: from_page -> key -> to_page. Deliberately the same shape the sibling
        #: collector's ``Memory`` uses, so :meth:`shortest_path` is unchanged.
        self.edges: dict[str, dict[EdgeKey, str]] = {}
        #: (from_page, signature, action) -> the §6 edge record.
        self.edge_records: dict[tuple[str, str, ActionType], AIGEdge] = {}
        #: page_id -> {(signature, action)} actually performed.
        self.explored: dict[str, set[EdgeKey]] = {}
        #: page_id -> {(signature, action)} navigation could not re-locate. Kept
        #: apart from ``explored`` because these are ROUTING failures, not
        #: coverage: the action may still be perfectly reachable directly.
        self.nav_failed: dict[str, set[EdgeKey]] = {}
        #: page_id -> {(signature, action)} ever SEEN on that page. Only the
        #: denominator for ``stats.unexplored_actions``; never routed over.
        self.known: dict[str, set[EdgeKey]] = {}
        #: page_id -> signature -> the Element as FIRST seen there. This is how
        #: the frontier of a page you are not standing on can be enumerated at
        #: all (``unexplored_elsewhere``), and it is a dict rather than the
        #: ``known`` set on purpose: set iteration order varies with
        #: PYTHONHASHSEED, so building a shuffled candidate list from a set
        #: makes a seeded run reproducible only WITHIN one process.
        #:
        #: The remembered ``bounds``/``index`` are from that first visit and are
        #: stale. Nothing may act on them — a remote candidate is a routing
        #: target only, and the element that finally executes comes from
        #: ``Navigator.next_action`` re-matching the LIVE screen by signature.
        self.known_elements: dict[str, dict[str, Element]] = {}
        self.same_function_groups: list[SameFunctionGroup] = []

    # -- pages ---------------------------------------------------------------

    @property
    def seen_pages(self) -> list[str]:
        """Every page seen, in first-visit order."""
        return list(self.nodes)

    def _ensure_node(self, page_id: str) -> AIGNode:
        """The node for *page_id*, minted empty if new. Does NOT count a visit."""
        node = self.nodes.get(page_id)
        if node is None:
            node = AIGNode(page_id=page_id)
            self.nodes[page_id] = node
        return node

    def note_page(
        self,
        page_id: str,
        *,
        activity: str | None = None,
        package: str | None = None,
        state_str: str | None = None,
        structure_str: str | None = None,
        observation: int | None = None,
        semantic_title: str | None = None,
    ) -> AIGNode:
        """Record one OBSERVATION of *page_id*, minting the node when new.

        Counts a visit, so call it once per observation and not as a
        node-existence check — :meth:`record_transition` uses ``_ensure_node``
        for exactly that reason, or arriving at a page would count twice.
        """
        node = self._ensure_node(page_id)
        node.visits += 1
        if activity:
            node.activity = activity
        if package:
            node.package = package
        if state_str and state_str not in node.state_strs:
            node.state_strs.append(state_str)
        if structure_str and structure_str not in node.structure_strs:
            node.structure_strs.append(structure_str)
        if semantic_title:
            node.semantic_title = semantic_title
        if observation is not None and node.first_observation is None:
            node.first_observation = observation
        return node

    # -- coverage ------------------------------------------------------------

    def mark_explored(self, page_id: str, signature: str, action: ActionType) -> None:
        self.explored.setdefault(page_id, set()).add((signature, action))
        self.known.setdefault(page_id, set()).add((signature, action))

    def mark_nav_failed(self, page_id: str, signature: str, action: ActionType) -> None:
        self.nav_failed.setdefault(page_id, set()).add((signature, action))

    def is_explored(self, page_id: str, signature: str, action: ActionType) -> bool:
        """Was this action actually PERFORMED on this page?

        Narrower than :meth:`is_blocked` and deliberately so: same-function
        pruning must fire on evidence of what an action DOES, and a navigation
        failure is evidence only that we could not reach it.
        """
        return (signature, action) in self.explored.get(page_id, ())

    def is_blocked(self, page_id: str, signature: str, action: ActionType) -> bool:
        pair = (signature, action)
        return pair in self.explored.get(page_id, ()) or pair in self.nav_failed.get(
            page_id, ()
        )

    def note_elements(self, page_id: str, elements: list[Element]) -> None:
        """Register what is actionable on *page_id*, for ``unexplored_actions``.

        The frontier of a page is only knowable while standing on it — the
        elements of a page we are not on are not in hand. So the denominator has
        to be accumulated as pages are visited, which is what this does and why
        :meth:`unexplored` calls it.
        """
        seen = self.known.setdefault(page_id, set())
        remembered = self.known_elements.setdefault(page_id, {})
        for element in elements:
            # setdefault, not assignment: keeping the first sighting keeps the
            # dict's iteration order stable across visits, which is what makes a
            # seeded shuffle over this frontier reproducible.
            remembered.setdefault(element.signature, element)
            for action in element.allowed_actions:
                seen.add((element.signature, action))

    def unexplored(self, page_id: str, elements: list[Element]) -> list[Candidate]:
        """Frontier of *page_id* given the elements currently on screen.

        ``long_touch`` is withheld until the element's ``touch`` has been tried,
        as in the reference (input_policy3.py:1069): a long press rarely has an
        independent effect, so spending a step on it before the tap is waste.
        """
        self.note_elements(page_id, elements)
        candidates: list[Candidate] = []
        for element in elements:
            touched = (element.signature, ActionType.TOUCH) in self.explored.get(
                page_id, ()
            )
            for action in element.allowed_actions:
                if self.is_blocked(page_id, element.signature, action):
                    continue
                if action is ActionType.LONG_TOUCH and not touched:
                    continue
                candidates.append((page_id, element, action))
        return candidates

    def unexplored_elsewhere(
        self, current_page: str, *, package: str = ""
    ) -> list[Candidate]:
        """Frontier of every page EXCEPT *current_page*, from memory.

        The caller already holds the live elements of the page it is standing
        on, so excluding it here keeps this method's stale-coordinate elements
        (see :attr:`known_elements`) out of the one place they could do harm.

        *package* is the reference's ``ONLY_EXPLORE_IN_APP`` guard: pages that
        are known to belong to another app are not routed to. A node whose
        package was never recorded is kept, since dropping it would silently
        shrink the frontier.

        The same ``long_touch`` deferral :meth:`unexplored` applies is applied
        here too, so a page's frontier does not depend on whether you are
        looking at it from on it or from across the graph.
        """
        candidates: list[Candidate] = []
        for page_id, remembered in self.known_elements.items():
            if page_id == current_page:
                continue
            node = self.nodes.get(page_id)
            if package and node is not None and node.package and node.package != package:
                continue
            explored = self.explored.get(page_id, set())
            for signature, element in remembered.items():
                touched = (signature, ActionType.TOUCH) in explored
                for action in element.allowed_actions:
                    if self.is_blocked(page_id, signature, action):
                        continue
                    if action is ActionType.LONG_TOUCH and not touched:
                        continue
                    candidates.append((page_id, element, action))
        return candidates

    # -- transitions ---------------------------------------------------------

    def record_transition(
        self,
        from_page: str,
        signature: str,
        action: ActionType,
        to_page: str,
        *,
        domain_action: Action | dict[str, Any] | None = None,
        step: int | None = None,
        semantic_element: str | None = None,
    ) -> AIGEdge:
        """Mark the action explored and remember where it led.

        ``effective`` is recomputed from ``to_page`` in the same statement that
        writes it, so the two can never disagree after a last-write-wins update.
        """
        self.mark_explored(from_page, signature, action)
        self.edges.setdefault(from_page, {})[(signature, action)] = to_page
        self._ensure_node(from_page)
        self._ensure_node(to_page)

        if isinstance(domain_action, Action):
            payload = domain_action.to_dict()
        else:
            payload = dict(domain_action or {})
        if not payload:
            # An edge with an empty ``action`` cannot be loaded back: §6 stores
            # no explorer action_type, so load() inverts the DOMAIN one and an
            # empty dict has nothing to invert. Synthesize the minimum that
            # round-trips. Deliberately the BASE Action rather than the concrete
            # subclass with its zero defaults: omitting the coordinate keys says
            # "none was recorded" and makes a consumer fail loudly, where
            # ``x=0, y=0`` would read as a real tap on the top-left corner.
            payload = Action(action_type=domain_action_type(action)).to_dict()

        record_key = (from_page, signature, action)
        edge = self.edge_records.get(record_key)
        if edge is None:
            edge = AIGEdge(
                from_page=from_page,
                to_page=to_page,
                element_signature=signature,
                action_type=action,
            )
            self.edge_records[record_key] = edge
        edge.to_page = to_page
        edge.effective = from_page != to_page
        edge.count += 1
        if payload:
            edge.action = payload
        if semantic_element:
            edge.semantic_element = semantic_element
        if step is not None:
            edge.steps.append(step)
        return edge

    def shortest_path(self, start: str, goal: str) -> list[tuple[str, str, ActionType]]:
        """BFS route from *start* to *goal* as ``(page, signature, action)`` steps.

        Empty when already there; empty ALSO when unreachable — the caller must
        distinguish those two with ``start == goal``, and
        :class:`~monkey_collector.explore.Navigator` does.
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

    # -- serialization -------------------------------------------------------

    def stats(self) -> dict[str, int]:
        explored = sum(len(pairs) for pairs in self.explored.values())
        unexplored = 0
        for page_id, pairs in self.known.items():
            unexplored += sum(
                1 for signature, action in pairs if not self.is_blocked(page_id, signature, action)
            )
        return {
            "pages": len(self.nodes),
            "edges": len(self.edge_records),
            "explored_actions": explored,
            "unexplored_actions": unexplored,
            "llm_calls": self.llm_calls,
        }

    def to_dict(self) -> dict[str, Any]:
        """The ARCHITECTURE §6 document. Every key is emitted unconditionally."""
        return {
            "package": self.package,
            "device": {"width": self.device_width, "height": self.device_height},
            "semantic_labeling": self.semantic_labeling,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edge_records.values()],
            "same_function_groups": [g.to_dict() for g in self.same_function_groups],
            "stats": self.stats(),
        }

    def save(self, path: str | Path) -> Path:
        """Write ``graph.json`` to *path*, creating its directory if needed."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return destination

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AIG:
        device = raw.get("device") or {}
        graph = cls(
            package=raw.get("package", ""),
            device_width=device.get("width", 0),
            device_height=device.get("height", 0),
            semantic_labeling=bool(raw.get("semantic_labeling", False)),
        )
        graph.llm_calls = (raw.get("stats") or {}).get("llm_calls", 0)
        for node_raw in raw.get("nodes") or []:
            node = AIGNode.from_dict(node_raw)
            graph.nodes[node.page_id] = node
        for edge_raw in raw.get("edges") or []:
            edge = AIGEdge.from_dict(edge_raw)
            graph._ensure_node(edge.from_page)
            graph._ensure_node(edge.to_page)
            graph.edges.setdefault(edge.from_page, {})[edge.key] = edge.to_page
            graph.edge_records[(edge.from_page, *edge.key)] = edge
            # Coverage is not stored separately: an edge IS the record that its
            # action was performed, and record_transition is the only writer of
            # `explored`, so the two are the same set.
            graph.mark_explored(edge.from_page, edge.element_signature, edge.action_type)
        graph.same_function_groups = [
            SameFunctionGroup.from_dict(g) for g in raw.get("same_function_groups") or []
        ]
        return graph

    @classmethod
    def load(cls, path: str | Path) -> AIG:
        """Rebuild a graph from ``graph.json``. See the module docstring for what
        does not survive the round trip, and why every loss is the safe one."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


__all__ = [
    "AIG",
    "AIGEdge",
    "AIGNode",
    "EdgeKey",
    "SameFunctionGroup",
    "graph_path",
]
