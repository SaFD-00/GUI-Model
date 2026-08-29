"""Page identity, ported from LLM-Explorer.

Two screens are "the same page" when exploration should treat an action tried on
one as already tried on the other. Get this wrong in one direction and the page
graph fragments (every list scroll mints a new page, coverage never saturates);
wrong in the other and distinct screens collapse (actions are marked explored
that were never tried).

WHAT THE REFERENCE ACTUALLY DOES
================================

Source: ``.claude/references/LLM-Explorer/droidbot/{device_state,input_policy3}.py``.

Identity is built from THREE hashes over the raw uiautomator view list, all
scoped by the foreground activity:

    state_str      md5("{activity}{" + ",".join(sorted(signature)) + "}")
    structure_str  the same with the CONTENT-FREE signature
    frame          the tag skeleton, all content stripped

    signature               [class]C[resource_id]R[visible]V[text]T[en,ch,se]
    content_free_signature  [class]C[resource_id]R[visible]V

``text`` longer than 50 chars is nulled in ``signature`` (device_state.py:272) —
a long body of text is content, not identity.

``_classify_state`` (input_policy3.py:506) then walks the known pages in
insertion order and merges on the FIRST of:

    1. equal semantic title      (LLM-free: the title reduces to structure_str)
    2. equal text_representation (content-aware equality)
    3. structure_str already registered for that page

after which two FILTERS narrow the remaining candidates — same activity, and
content-free signature symmetric difference <= 2
(``MAX_NUM_DIFF_ELEMENTS_IN_SIMILAR_STATES``) — and an LLM is asked to pick one.

THE DEAD BRANCH, AND WHAT WE DO ABOUT IT
========================================

That LLM selection is DEAD CODE in the published snapshot. ``_classify_state``
assigns ``state_id = None`` unconditionally and then tests
``if state_id is not None`` (input_policy3.py:557-559), so the chooser can never
fire and the function returns "no match" — a NEW page — for every candidate the
filters let through.

Two consequences worth being explicit about, because both are easy to get wrong:

* **The reference's page matching is heuristic whether or not the LLM is on.**
  The LLM supplies the page's *title string*, never the merge decision. So
  porting this algorithm does not compromise the project's LLM-free page-identity
  rule — there is nothing to give up.

* **The symmetric-difference filter never actually merges anything upstream.**
  It computes candidates for a chooser that cannot run. Ported literally, the
  effective behaviour is structure-hash matching alone, which fragments badly on
  exactly the screens the constant was written for: a list whose rows differ by a
  handful of nodes mints a fresh page per scroll position.

:data:`MergePolicy` makes the choice explicit rather than burying it.
``STRUCTURE_ONLY`` reproduces the reference's effective behaviour byte for byte.
``SIMILAR_ELEMENTS`` (the default) fills the dead chooser's slot with the
smallest-symmetric-difference candidate, which is what the filters were plainly
built to feed.

MEASURED ON THE TARGET DEVICE
=============================

The default is not a guess. Over the five real Pixel 6 dumps in
``tests/fixtures/pages/``, the symmetric difference separates cleanly:

    subsettings_a  vs  subsettings_a_scrolled       2     <- same page, scrolled
    every other pair                            18..30     <- genuinely different

The pair at 2 is Settings > Notifications before and after a scroll: eight rows
of text change completely, and the only content-free signatures that differ are
an off-screen ``Switch`` and its ``widget_frame`` wrapper. ``STRUCTURE_ONLY``
mints two pages for it; ``SIMILAR_ELEMENTS`` merges it and leaves a 16-unit
margin before the nearest genuinely-distinct screen. That margin is why the
budget of 2 is safe here rather than merely inherited.

Note that ``structure_str`` alone already merges many scrolls — a ``RecyclerView``
recycles rows, so scrolling often leaves the content-free signature SET
untouched. The symmetric-difference path is for the residue: scrolls that reveal
or hide a node type, as this one does.

Neither number is a law of nature. Re-measure before trusting them on a device
with a different form factor or Android version, and prefer
``STRUCTURE_ONLY`` — which under-merges — over widening the budget, because
over-merging marks actions explored that were never tried, and that damage is
silent and unrecoverable from the collected data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from xml.etree import ElementTree as ET

#: Symmetric-difference budget over content-free signatures, from
#: ``MAX_NUM_DIFF_ELEMENTS_IN_SIMILAR_STATES`` (input_policy3.py:38).
DEFAULT_MAX_DIFF_ELEMENTS = 2

#: ``text`` at or above this length is dropped from the content-aware signature
#: (device_state.py:272). Long text is content, not identity.
MAX_SIGNATURE_TEXT_LEN = 50

#: Hash prefix length. The reference truncates to 6 (device_state.py:29-30).
HASH_PREFIX = 6

#: Chrome that must not participate in identity — present on every screen.
_IGNORED_RESOURCE_IDS = frozenset(
    {"android:id/navigationBarBackground", "android:id/statusBarBackground"}
)

_TRUE = "true"


class MergePolicy(str, Enum):
    """How far to go when no hash matches. See the module docstring.

    Python 3.10 has no ``enum.StrEnum`` (the reference used 3.11+), so this is a
    plain ``str``/``Enum`` mixin instead. ``==``, ``.value`` and JSON encoding
    behave identically either way, but ``str(member)`` does not: a bare mixin
    returns ``"MergePolicy.SIMILAR_ELEMENTS"`` where ``StrEnum`` returns
    ``"similar_elements"``. The explicit ``__str__`` below closes that gap so a
    caller that does ``str(policy)`` or ``f"{policy}"`` for a log line or a CSV
    cell gets the same value under either Python version.
    """

    #: Only the reference's live paths: content-aware equality and structure hash.
    STRUCTURE_ONLY = "structure_only"
    #: Additionally merge into the closest same-activity page within the
    #: symmetric-difference budget — the slot the dead LLM chooser left open.
    SIMILAR_ELEMENTS = "similar_elements"

    def __str__(self) -> str:
        return self.value


class MatchKind(str, Enum):
    """Why a screen was assigned to its page. Recorded for auditability.

    See :class:`MergePolicy` for why ``__str__`` is overridden here too.
    """

    NEW = "new"
    STATE_EXACT = "state_exact"
    STRUCTURE = "structure"
    SIMILAR_ELEMENTS = "similar_elements"

    def __str__(self) -> str:
        return self.value


def _attr(node: ET.Element, name: str, default: str = "None") -> str:
    value = node.get(name)
    return default if value is None or value == "" else value


def _visible(node: ET.Element) -> str:
    """uiautomator spells the reference's ``visible`` as ``visible-to-user``."""
    raw = node.get("visible-to-user")
    if raw is None:
        raw = node.get("visible", "false")
    return _TRUE if raw == _TRUE else "False"


def _flag(node: ET.Element, name: str) -> str:
    """The reference's ``__key_if_true``: the flag name, or empty when false."""
    return name if node.get(name) == _TRUE else ""


def view_signature(node: ET.Element) -> str:
    """Content-AWARE signature: identity including text and interaction state."""
    text = node.get("text") or "None"
    if len(text) > MAX_SIGNATURE_TEXT_LEN:
        text = "None"
    return "[class]{}[resource_id]{}[visible]{}[text]{}[{},{},{}]".format(
        _attr(node, "class"),
        _attr(node, "resource-id"),
        _visible(node),
        text,
        _flag(node, "enabled"),
        _flag(node, "checked"),
        _flag(node, "selected"),
    )


def content_free_signature(node: ET.Element) -> str:
    """Content-FREE signature: layout identity only, no text or state."""
    return "[class]{}[resource_id]{}[visible]{}".format(
        _attr(node, "class"),
        _attr(node, "resource-id"),
        _visible(node),
    )


def _iter_views(raw_xml: str) -> list[ET.Element]:
    """Every ``<node>`` of a uiautomator dump, system chrome excluded."""
    root = ET.fromstring(raw_xml)
    return [
        node
        for node in root.iter("node")
        if node.get("resource-id") not in _IGNORED_RESOURCE_IDS
    ]


def _digest(activity: str, signatures: set[str]) -> str:
    payload = "{}{{{}}}".format(activity, ",".join(sorted(signatures)))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:HASH_PREFIX]


@dataclass(frozen=True)
class ScreenState:
    """One observed screen, reduced to the identities page matching needs."""

    activity: str
    package: str
    state_str: str
    structure_str: str
    #: Content-free signatures, for the symmetric-difference comparison.
    element_sigs: frozenset[str]

    @classmethod
    def from_dump(cls, raw_xml: str, activity: str, package: str = "") -> ScreenState:
        views = _iter_views(raw_xml)
        aware = {view_signature(v) for v in views}
        free = {content_free_signature(v) for v in views}
        return cls(
            activity=activity or "",
            package=package or _dominant_package(views),
            state_str=_digest(activity or "", aware),
            structure_str=_digest(activity or "", free),
            element_sigs=frozenset(free),
        )

    def is_in_app(self, package: str) -> bool:
        """Whether this screen belongs to *package* — the reference's
        ``ONLY_EXPLORE_IN_APP`` guard, which keeps pages of other apps out of
        the graph the explorer routes over."""
        return bool(package) and (
            self.package == package or self.activity.startswith(package)
        )


def dominant_package(raw_xml: str) -> str:
    """The package most of *raw_xml*'s views belong to, or "" if it has none.

    The same rule :meth:`ScreenState.from_dump` uses, exposed because export must
    ask the question of a dump on disk without rebuilding a whole ScreenState.
    """
    return _dominant_package(_iter_views(raw_xml))


def _dominant_package(views: list[ET.Element]) -> str:
    counts: dict[str, int] = {}
    for view in views:
        pkg = view.get("package")
        if pkg:
            counts[pkg] = counts.get(pkg, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else ""


@dataclass
class Page:
    """An abstract page: the merged identities of every screen assigned to it."""

    key: str
    activity: str
    #: Every ``structure_str`` this page has been seen under.
    structures: set[str] = field(default_factory=set)
    #: Every content-aware ``state_str`` seen.
    states: set[str] = field(default_factory=set)
    #: Content-free signatures of the FIRST screen — the comparison anchor.
    element_sigs: frozenset[str] = frozenset()
    observations: int = 0


@dataclass(frozen=True)
class PageMatch:
    """The page a screen was assigned to, and why."""

    page_key: str
    kind: MatchKind
    #: Symmetric difference against the matched page; None unless the match came
    #: from the similar-elements path.
    diff: int | None = None

    @property
    def is_new(self) -> bool:
        return self.kind is MatchKind.NEW


class PageRegistry:
    """Assigns screens to abstract pages, LLM-free.

    Pages are walked in insertion order and the FIRST match wins, exactly as the
    reference does — so an earlier page absorbs a screen a later one would also
    have accepted. That ordering dependence is inherited on purpose: it is what
    makes a session's page numbering stable and replayable from its event log.
    """

    def __init__(
        self,
        *,
        policy: MergePolicy = MergePolicy.SIMILAR_ELEMENTS,
        max_diff_elements: int = DEFAULT_MAX_DIFF_ELEMENTS,
        same_activity_only: bool = True,
    ) -> None:
        if max_diff_elements < 0:
            raise ValueError(f"max_diff_elements must be >= 0, got {max_diff_elements!r}")
        self.policy = MergePolicy(policy)
        self.max_diff_elements = max_diff_elements
        self.same_activity_only = same_activity_only
        self._pages: dict[str, Page] = {}
        self._next_key = 0

    @property
    def pages(self) -> dict[str, Page]:
        return self._pages

    def __len__(self) -> int:
        return len(self._pages)

    def classify(self, state: ScreenState) -> PageMatch:
        """Assign *state* to a page, minting one if nothing matches."""
        match = self._match(state)
        page = self._pages[match.page_key]
        page.structures.add(state.structure_str)
        page.states.add(state.state_str)
        page.observations += 1
        return match

    def _match(self, state: ScreenState) -> PageMatch:
        best_key: str | None = None
        best_diff: int | None = None

        for key, page in self._pages.items():
            # 1-2. Content-aware equality, then the structure hash. Both are
            #      early returns in the reference and both ignore the activity
            #      filter, because an identical hash IS the identity.
            if state.state_str in page.states:
                return PageMatch(key, MatchKind.STATE_EXACT)
            if state.structure_str in page.structures:
                return PageMatch(key, MatchKind.STRUCTURE)

            if self.policy is MergePolicy.STRUCTURE_ONLY:
                continue
            if self.same_activity_only and state.activity != page.activity:
                continue
            diff = len(state.element_sigs.symmetric_difference(page.element_sigs))
            if diff > self.max_diff_elements:
                continue
            # Closest candidate wins. The reference collects every survivor and
            # hands them to a chooser that never runs; "fewest differing
            # elements" is the obvious LLM-free stand-in, and ties keep
            # insertion order because the comparison is strict.
            if best_diff is None or diff < best_diff:
                best_key, best_diff = key, diff

        if best_key is not None:
            return PageMatch(best_key, MatchKind.SIMILAR_ELEMENTS, diff=best_diff)
        return PageMatch(self._mint(state), MatchKind.NEW)

    def _mint(self, state: ScreenState) -> str:
        key = str(self._next_key)
        self._next_key += 1
        self._pages[key] = Page(
            key=key,
            activity=state.activity,
            element_sigs=state.element_sigs,
        )
        return key


__all__ = [
    "DEFAULT_MAX_DIFF_ELEMENTS",
    "HASH_PREFIX",
    "MAX_SIGNATURE_TEXT_LEN",
    "MatchKind",
    "MergePolicy",
    "Page",
    "PageMatch",
    "PageRegistry",
    "ScreenState",
    "content_free_signature",
    "dominant_package",
    "view_signature",
]
