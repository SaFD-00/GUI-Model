"""Semantic labelling and same-function element grouping.

This is the LLM half of exploration (ARCHITECTURE §5.2), ported from the
reference's ``_gen_state_semantic_info`` (input_policy3.py:381-498). It answers
two questions about a screen — *what is this page called* and *which of its
elements do the same thing* — and it is the ONLY place in this project where a
model can change what gets explored.

WHAT THIS MODULE MUST NEVER DO
==============================

It must never touch PAGE IDENTITY. ``pagematch.py`` alone decides which screen
is which (AGENTS §2(b)); the label produced here is a human-readable name hung
on a node that already exists. This is a REPRODUCIBILITY contract, not a
performance one: if the same screen could land on different pages depending on
an API's mood, the AIG and every coverage number computed from it become
meaningless. Collection with no ``OPENROUTER_API_KEY`` must produce the same
page ids as collection with one, and :func:`test_page_identity_is_unchanged...`
pins exactly that.

THE CALL-COUNT GUARD IS LOAD-BEARING
====================================

``_gen_state_semantic_info`` runs per STATE, not per page, and states vastly
outnumber pages — the sibling collector's ``org.tasks`` session was 1,047
observations / 597 steps / **30 pages**. Unguarded, that is a four-figure bill
for one app.

The reference's defence is to compare the new state's ``text_representation_frame``
(its structure-only rendering) against every known state's, and on an exact
match reuse that state's description and groups without querying at all
(input_policy3.py:388-404).

Here the reuse key is :attr:`~monkey_collector.pagematch.ScreenState.structure_str`.
It is md5(activity + the set of CONTENT-FREE view signatures) — the same
"structure with the content removed" the reference's frame expresses, already
computed for page matching, and 6 characters instead of a whole document to
compare. Two screens with equal ``structure_str`` have, by construction,
identical activity and identical content-free element structure, which is the
condition the reference is testing for.

There is a real asymmetry in that choice, and it must not be "fixed" without
reading this paragraph. The cache key is CONTENT-FREE, while a group's members
are :func:`~monkey_collector.explore.element_signature` values, which include
subtree text and so are CONTENT-AWARE. A list whose rows changed label keeps its
``structure_str``, hits the cache, and is handed groups whose signatures match
nothing on screen — so pruning quietly does nothing. That is the SAFE direction:
a signature matches only a genuinely identical element, so this can under-prune
and can never prune an action the model never grouped. The two obvious repairs
both trade it for the unsafe direction — keying the cache on ``state_str`` would
restore a per-state bill, and re-indexing groups POSITIONALLY as the reference
does (it stores element ids, not identities) would let a reordered list apply
yesterday's grouping to today's rows, marking untried actions covered. AGENTS
§2(f) mandates the single signature identity; this is the price of it, not a bug.

Consequently :attr:`SemanticLabeler.llm_calls` counts DISTINCT STRUCTURES, not
steps. Hundreds of calls for one app means the guard is broken, not that the app
is complex; the number is written to ``graph.json``'s ``stats.llm_calls`` so the
first real session reports it in a log rather than an invoice.

MIN_SIZE_SAME_FUNCTION_ELEMENT_GROUP IS NOT A GROUP SIZE
========================================================

The reference names a constant ``MIN_SIZE_SAME_FUNCTION_ELEMENT_GROUP = 5`` and
then writes ``if len(elements) < 5: continue`` (input_policy3.py:478), where
``elements`` is the WHOLE SCREEN's element list and not the group. So it means
"do not apply grouping on screens with fewer than 5 elements", which is not what
the name says. That behaviour is ported verbatim and exposed as
``exploration.min_elements_for_grouping``. **Do not "fix" the mismatch**: it is
the reference's semantics, and quietly turning it into a group-size floor would
change which actions get pruned with no visible symptom.

Two deliberate departures, both recorded here so they are not rediscovered as
bugs:

* ``len(elements)`` here counts ACTIONABLE elements
  (:func:`~monkey_collector.explore.elements_of`) rather than every rendered
  view, because those are the elements the prompt enumerates and the indices the
  model answers with. The count is therefore systematically smaller than the
  reference's and grouping applies on strictly fewer screens — the safe
  direction, since grouping only ever removes work.
* Groups of fewer than two members are dropped. A one-element group prunes
  nothing (the rule skips an action when a *different* member already ran it),
  so keeping them would only pad the audit log.

EVERY LABELLED STATE LEAVES AN AUDIT ROW
========================================

§5.2 requires that grouping be auditable, because a wrong group is INVISIBLE in
the collected data: the pruned action was never attempted, so nothing in the
corpus records that it was skipped rather than tried. So every state that
reaches the labeller appends to ``graph.same_function_groups``:

    * one row per real group, ``source="llm"``; or
    * exactly one row with ``members: []`` when no group was produced.

**A row with empty ``members`` is a no-pruning-here MARKER, not a group.** Do not
read ``len(same_function_groups)`` as a group count. The marker is what makes a
per-state failure visible: a run that labelled 30 structures and had one
malformed response shows 29 rows with ``source="llm"`` and one with
``source="none"``, which the top-level ``semantic_labeling`` flag alone could
never tell you.

DEGRADATION IS A NORMAL PATH
============================

No API key, ``llm.semantic_labeling: false``, a transport error, a non-JSON
reply, a reply of the wrong shape — all land on the same place: label =
``structure_str``, groups = empty, ``source="none"``. Exploration continues
unchanged; it simply loses the pruning. Nothing here raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from monkey_collector.aig import AIG, SameFunctionGroup
from monkey_collector.explore import Element, signature_label

if TYPE_CHECKING:  # pragma: no cover - types only
    from monkey_collector.llm.client import LLMClient
    from monkey_collector.pagematch import ScreenState

#: Reference default (``MIN_SIZE_SAME_FUNCTION_ELEMENT_GROUP``, input_policy3.py:39).
#: See the module docstring: this is a SCREEN size floor, not a group size floor.
MIN_ELEMENTS_FOR_GROUPING = 5

#: Cost-attribution label for ``cost.csv`` rows raised by this module.
AGENT = "semantic"

#: Bound the reply so a runaway generation cannot dominate the bill.
MAX_TOKENS = 800

#: Cap on the element list handed to the model. Long screens are truncated
#: rather than dropped: the indices stay aligned with ``elements`` because only
#: the tail is removed.
MAX_PROMPT_ELEMENTS = 80

_PROMPT_TEMPLATE = (
    'Now suppose you are analyzing an app named "{app}", '
    "the current GUI page shows following elements:\n{elements}\n"
    "Please think step by step and respond in the following format:\n"
    " Page description: <short (less than 20 words) description of the function "
    "of current page>\n"
    " Element description: <short (less than 20 words) summary of main control "
    "elements in current page, comma separated>\n"
    " Same-function elements: <groups of element ids, each group contains multiple "
    "elements that lead to the same function or share common characteristics>. "
    "The elements with different layouts and redirect targets are less likely to "
    "have the same function.\n"
    "**If there are various file categories, group them together. If there are "
    "checkboxes with similar functionalities, group them together. If there are "
    "redundant date labels, group them together.**\n"
    "You should respond with JSON only, in the following format:\n"
    "{{\n"
    '    "Page description": "",\n'
    '    "Element description": "",\n'
    '    "Same-function elements": [\n'
    "        {{\n"
    '            "elements": [],\n'
    '            "function": ""\n'
    "        }}\n"
    "    ]\n"
    "}}"
)


@dataclass(frozen=True)
class SemanticInfo:
    """What the semantic layer knows about one screen.

    ``source`` is ``"llm"`` when a model produced this and ``"none"`` when the
    degraded path did. It is copied verbatim into the ``graph.json`` audit rows,
    which is how a reader tells pruning-was-off from pruning-found-nothing (§6).
    """

    #: Human-readable page name. Falls back to ``structure_str`` — never used
    #: for page IDENTITY, which is ``pagematch.py``'s alone (AGENTS §2(b)).
    page_label: str
    elements_description: str = ""
    #: Sets of :func:`~monkey_collector.explore.element_signature` values. The
    #: model answers with INDICES into the screen's element list; they are
    #: converted here so that one identity function is used everywhere (§5.4).
    groups: tuple[frozenset[str], ...] = ()
    source: str = "none"

    @property
    def from_llm(self) -> bool:
        return self.source == "llm"


def describe_elements(elements: list[Element]) -> str:
    """Numbered element listing for the prompt. Indices index *elements*."""
    lines: list[str] = []
    for index, element in enumerate(elements[:MAX_PROMPT_ELEMENTS]):
        parts = [f"class={element.class_name or '?'}"]
        if element.resource_id:
            parts.append(f"id={element.resource_id.rsplit('/', 1)[-1]}")
        label = signature_label(element.signature) or element.text
        if label:
            parts.append(f'label="{label}"')
        parts.append("actions=" + ",".join(str(a) for a in element.allowed_actions))
        lines.append(f"{index}: <{' '.join(parts)}>")
    return "\n".join(lines)


def _loads(text: str) -> Any:
    """Parse *text* as JSON, tolerating code fences and surrounding prose.

    Returns ``None`` rather than raising: a malformed reply is a degradation,
    not an error, and nothing in this module may propagate an exception into the
    exploration loop.
    """
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        if body.lstrip().lower().startswith("json"):
            body = body.lstrip()[4:]
        body = body.strip()
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        pass
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(body[start : end + 1])
        except (ValueError, TypeError):
            return None
    return None


def _group_indices(entry: Any) -> list[int]:
    """The element ids of one ``Same-function elements`` entry, junk removed."""
    ids = entry.get("elements") if isinstance(entry, dict) else entry
    if not isinstance(ids, list):
        return []
    # bool is an int subclass; `True` is not an element id.
    return [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]


@dataclass
class SemanticLabeler:
    """Labels screens and mints same-function groups, once per structure.

    One instance per app session. It owns the reuse cache (so the call count is
    per-app), the running :attr:`llm_calls` total, and
    :attr:`groups_by_page` — the map the explorer prunes with, which must cover
    every page and not just the current one, or a candidate reached by
    navigation would escape pruning that the same candidate would receive when
    picked directly.
    """

    client: LLMClient | None = None
    app_name: str = ""
    #: ``llm.semantic_labeling``. False and ``client is None`` are the same
    #: outcome, kept separate so a config choice and a missing key are
    #: distinguishable in a log line.
    enabled: bool = True
    min_elements_for_grouping: int = MIN_ELEMENTS_FOR_GROUPING
    llm_calls: int = 0
    #: page_id -> its groups, for :meth:`monkey_collector.explore.Explorer.select`.
    groups_by_page: dict[str, tuple[frozenset[str], ...]] = field(default_factory=dict)
    #: structure_str -> the info minted for it. THE call-count guard.
    _cache: dict[str, SemanticInfo] = field(default_factory=dict, repr=False)

    @property
    def active(self) -> bool:
        """Whether a query will actually be issued for a new structure."""
        return self.enabled and self.client is not None

    def observe(
        self,
        state: ScreenState,
        page_id: str,
        elements: list[Element],
        graph: AIG,
    ) -> SemanticInfo:
        """Label *state*, querying at most once per distinct ``structure_str``.

        Writes the audit rows and ``stats.llm_calls`` into *graph* as a side
        effect. That is deliberate coupling: an audit record that a caller can
        forget to write is an audit record that will be missing exactly when it
        matters (§5.2).
        """
        cached = self._cache.get(state.structure_str)
        if cached is not None:
            self.groups_by_page[page_id] = cached.groups
            self._title(graph, page_id, cached)
            return cached

        before = self.llm_calls
        info = self._label(state, elements)
        self._cache[state.structure_str] = info
        self.groups_by_page[page_id] = info.groups
        self._title(graph, page_id, info)
        # INCREMENT, never assign: AIG.load restores ``llm_calls`` from
        # ``stats`` while a resumed session gets a fresh labeller starting at 0.
        # Assigning would rewrite a session's whole billing history as "1".
        graph.llm_calls += self.llm_calls - before
        self._audit(graph, state, page_id, info)
        return info

    # -- internals -----------------------------------------------------------

    def _title(self, graph: AIG, page_id: str, info: SemanticInfo) -> None:
        """Hang the label on an EXISTING node; never mint one.

        Minting here would let the semantic layer invent pages, which is
        precisely what AGENTS §2(b) forbids. The loop notes the page first
        (ARCHITECTURE §3 step 4 before step 5), so the node is already there.
        """
        node = graph.nodes.get(page_id)
        if node is not None:
            node.semantic_title = info.page_label

    def _audit(
        self, graph: AIG, state: ScreenState, page_id: str, info: SemanticInfo
    ) -> None:
        if info.groups:
            for group in info.groups:
                graph.same_function_groups.append(
                    SameFunctionGroup(
                        minted_in_state=state.state_str,
                        page_id=page_id,
                        members=sorted(group),
                        source=info.source,
                    )
                )
            return
        # No group: still leave the marker row, so a per-state degradation is
        # visible afterwards. Empty ``members`` means "no pruning here".
        graph.same_function_groups.append(
            SameFunctionGroup(
                minted_in_state=state.state_str,
                page_id=page_id,
                members=[],
                source=info.source,
            )
        )

    def _degraded(self, state: ScreenState) -> SemanticInfo:
        return SemanticInfo(page_label=state.structure_str, source="none")

    def _label(self, state: ScreenState, elements: list[Element]) -> SemanticInfo:
        client = self.client
        if not self.enabled or client is None:
            return self._degraded(state)

        prompt = _PROMPT_TEMPLATE.format(
            app=self.app_name or state.package or "this app",
            elements=describe_elements(elements),
        )
        self.llm_calls += 1
        try:
            reply = client.chat(
                prompt,
                max_tokens=MAX_TOKENS,
                response_format={"type": "json_object"},
                agent=AGENT,
            )
        except Exception as exc:  # noqa: BLE001 - degradation is the contract
            logger.warning(f"semantic labelling failed for {state.structure_str}: {exc}")
            return self._degraded(state)

        info = self._parse(reply, state, elements)
        if info is None:
            logger.warning(
                f"semantic labelling got an unusable reply for {state.structure_str}"
            )
            return self._degraded(state)
        return info

    def _parse(
        self, reply: str, state: ScreenState, elements: list[Element]
    ) -> SemanticInfo | None:
        """Reply -> :class:`SemanticInfo`, or None when the shape is wrong."""
        data = _loads(reply)
        if not isinstance(data, dict):
            return None
        raw_groups = data.get("Same-function elements") or []
        if not isinstance(raw_groups, list):
            return None

        groups: list[frozenset[str]] = []
        seen: set[frozenset[str]] = set()
        for entry in raw_groups:
            members = {
                elements[i].signature
                for i in _group_indices(entry)
                if 0 <= i < len(elements)
            }
            # A one-member group prunes nothing: the rule fires only when a
            # DIFFERENT member has already run the action.
            if len(members) < 2:
                continue
            frozen = frozenset(members)
            if frozen in seen:
                continue
            seen.add(frozen)
            groups.append(frozen)

        # The reference's screen-size floor, ported verbatim. See the module
        # docstring for why the constant's name disagrees with its behaviour.
        if len(elements) < self.min_elements_for_grouping:
            groups = []

        label = str(data.get("Page description") or "").strip() or state.structure_str
        return SemanticInfo(
            page_label=label,
            elements_description=str(data.get("Element description") or "").strip(),
            groups=tuple(groups),
            source="llm",
        )


__all__ = [
    "AGENT",
    "MAX_PROMPT_ELEMENTS",
    "MAX_TOKENS",
    "MIN_ELEMENTS_FOR_GROUPING",
    "SemanticInfo",
    "SemanticLabeler",
    "describe_elements",
]
