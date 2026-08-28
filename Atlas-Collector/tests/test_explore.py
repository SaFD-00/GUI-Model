"""Coverage-guided exploration.

Fixtures are real ``uiautomator`` dumps from the target Pixel 6, so the numbers
pinned here are measurements. The Explorer touches no device, so every test
drives it directly.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from atlas_collector.explore import (
    MAX_NAVIGATE_STEPS,
    MAX_SUBTREE_TEXT,
    ActionType,
    Element,
    Explorer,
    Memory,
    Navigator,
    element_signature,
    elements_of,
    parse_bounds,
    subtree_text,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pages"
PAGE = "0"
OTHER = "1"


def dump(name: str) -> str:
    return (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")


def node(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def elem(signature: str, *actions: ActionType, index: int = 0) -> Element:
    return Element(signature, actions or (ActionType.TOUCH,), (0, 0, 10, 10), index)


# ---------------------------------------------------------------------------
# parse_bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[0,0][1080,2400]", (0, 0, 1080, 2400)),
        ("[42,218][1038,2343]", (42, 218, 1038, 2343)),
        ("[-5,0][10,10]", (-5, 0, 10, 10)),
        ("garbage", (0, 0, 0, 0)),
        ("", (0, 0, 0, 0)),
        ("[1,2][3]", (0, 0, 0, 0)),
    ],
)
def test_parse_bounds(raw, expected):
    assert parse_bounds(raw) == expected


# ---------------------------------------------------------------------------
# Element identity — the collapse this design exists to prevent
# ---------------------------------------------------------------------------


def test_node_attributes_alone_collapse_android_list_rows():
    """The trap that motivates subtree text.

    Settings rows are clickable LinearLayouts with no id and no text of their
    own. Keyed on node attributes, ten different entries become one signature
    and the explorer abandons the screen after three steps.
    """
    from atlas_collector.pagematch import view_signature

    root = node(dump("settings_root"))
    clickable = [n for n in root.iter("node") if n.get("clickable") == "true"]
    node_level = Counter(view_signature(n) for n in clickable)
    assert max(node_level.values()) >= 10, "the collapse should be reproducible"
    assert len(node_level) < len(clickable) / 3


def test_subtree_text_recovers_every_row():
    elements = elements_of(dump("settings_root"))
    signatures = {e.signature for e in elements}
    assert len(signatures) == len(elements) == 12, "no element shares an identity"


@pytest.mark.parametrize(
    "name", ["settings_root", "subsettings_a", "subsettings_a_scrolled", "files_list"]
)
def test_no_signature_collisions_on_any_real_screen(name):
    elements = elements_of(dump(name))
    assert len({e.signature for e in elements}) == len(elements)


def test_signatures_survive_scrolling():
    """The property bound_box matching cannot give: identity travels with the
    row, so coverage learned before a scroll is still valid after it."""
    before = {e.signature for e in elements_of(dump("subsettings_a"))}
    after = {e.signature for e in elements_of(dump("subsettings_a_scrolled"))}
    assert before & after, "rows still on screen must keep their identity"


def test_subtree_text_reads_children_and_content_desc():
    xml = '<node class="LinearLayout"><node text="Wi-Fi"/><node content-desc="on"/></node>'
    assert subtree_text(node(xml)) == "Wi-Fi|on"


def test_subtree_text_is_truncated_so_prose_is_not_identity():
    xml = f'<node class="X"><node text="{"a" * 500}"/></node>'
    assert len(subtree_text(node(xml))) == MAX_SUBTREE_TEXT


def test_signature_ignores_position_but_not_label():
    a = '<node class="X" bounds="[0,0][9,9]"><node text="One"/></node>'
    b = '<node class="X" bounds="[500,900][999,999]"><node text="One"/></node>'
    c = '<node class="X" bounds="[0,0][9,9]"><node text="Two"/></node>'
    assert element_signature(node(a)) == element_signature(node(b))
    assert element_signature(node(a)) != element_signature(node(c))


# ---------------------------------------------------------------------------
# elements_of
# ---------------------------------------------------------------------------


def test_only_actionable_nodes_are_returned():
    for element in elements_of(dump("settings_root")):
        assert element.allowed_actions


def test_disabled_and_zero_area_nodes_never_enter_the_frontier():
    """Once in the frontier they are indistinguishable from untried actions, so
    the explorer would return to them forever."""
    xml = (
        "<hierarchy>"
        '<node class="A" clickable="true" enabled="false" bounds="[0,0][9,9]"/>'
        '<node class="B" clickable="true" bounds="[5,5][5,5]"/>'
        '<node class="C" clickable="true" bounds="[0,0][9,9]"/>'
        "</hierarchy>"
    )
    assert [e.class_name for e in elements_of(xml)] == ["C"]


def test_editable_fields_offer_set_text():
    xml = '<hierarchy><node class="android.widget.EditText" bounds="[0,0][9,9]"/></hierarchy>'
    assert elements_of(xml)[0].allowed_actions == (ActionType.SET_TEXT,)


def test_scrollable_containers_are_actionable():
    kinds = {a for e in elements_of(dump("settings_root")) for a in e.allowed_actions}
    assert ActionType.SCROLL in kinds


def test_center_is_derived_from_bounds():
    assert Element("s", (ActionType.TOUCH,), (0, 0, 10, 20), 0).center == (5, 10)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def test_coverage_is_keyed_on_the_page_not_the_screen_instance():
    """An action tried once is known-tried on every later visit to that page —
    which is what lets coverage saturate instead of re-walking the same buttons."""
    memory = Memory()
    elements = elements_of(dump("settings_root"))
    target = elements[0]
    assert len(memory.unexplored(PAGE, elements)) > 0
    memory.mark_explored(PAGE, target.signature, ActionType.TOUCH)
    remaining = memory.unexplored(PAGE, elements)
    assert (target.signature, ActionType.TOUCH) not in {
        (e.signature, a) for _, e, a in remaining
    }


def test_coverage_does_not_leak_between_pages():
    memory = Memory()
    elements = elements_of(dump("settings_root"))
    memory.mark_explored(PAGE, elements[0].signature, ActionType.TOUCH)
    assert len(memory.unexplored(OTHER, elements)) == len(
        memory.unexplored(OTHER, elements)
    )
    assert len(memory.unexplored(PAGE, elements)) < len(
        memory.unexplored(OTHER, elements)
    )


def test_long_touch_is_withheld_until_touch_has_been_tried():
    """A long press rarely has an independent effect (input_policy3.py:1069), so
    spending a step on it before the tap is waste."""
    memory = Memory()
    element = elem("sig", ActionType.TOUCH, ActionType.LONG_TOUCH)
    actions = {a for _, _, a in memory.unexplored(PAGE, [element])}
    assert actions == {ActionType.TOUCH}

    memory.mark_explored(PAGE, "sig", ActionType.TOUCH)
    actions = {a for _, _, a in memory.unexplored(PAGE, [element])}
    assert actions == {ActionType.LONG_TOUCH}


def test_nav_failure_is_tracked_apart_from_coverage():
    """A routing failure is not evidence the action was tried — it stays
    available to direct exploration on the page itself."""
    memory = Memory()
    memory.mark_nav_failed(PAGE, "sig", ActionType.TOUCH)
    assert memory.is_blocked(PAGE, "sig", ActionType.TOUCH)
    assert (("sig", ActionType.TOUCH)) not in memory.explored.get(PAGE, set())


def test_shortest_path_finds_the_fewest_hops():
    memory = Memory()
    memory.record_transition("a", "s1", ActionType.TOUCH, "b")
    memory.record_transition("b", "s2", ActionType.TOUCH, "c")
    memory.record_transition("a", "s3", ActionType.TOUCH, "c")
    assert memory.shortest_path("a", "c") == [("a", "s3", ActionType.TOUCH)]
    assert memory.shortest_path("a", "a") == []
    assert memory.shortest_path("a", "zzz") == []


# ---------------------------------------------------------------------------
# Navigator
# ---------------------------------------------------------------------------


def test_navigation_re_matches_by_signature_not_coordinate():
    """A route survives the layout shifting between visits."""
    memory = Memory()
    memory.record_transition("a", "sig-a", ActionType.TOUCH, "b")
    navigator = Navigator(memory)
    target = elem("sig-b", ActionType.TOUCH)
    assert navigator.plan("a", [("b", target, ActionType.TOUCH)])

    moved = Element("sig-a", (ActionType.TOUCH,), (900, 1800, 1000, 1900), 7)
    step = navigator.next_action("a", [moved])
    assert step == (moved, ActionType.TOUCH)


def test_landing_off_course_drops_the_plan_instead_of_patching_it():
    memory = Memory()
    memory.record_transition("a", "sig-a", ActionType.TOUCH, "b")
    navigator = Navigator(memory)
    navigator.plan("a", [("b", elem("sig-b"), ActionType.TOUCH)])
    assert navigator.next_action("unexpected-page", []) is None
    assert not navigator.is_navigating


def test_an_unreachable_planned_element_is_marked_nav_failed():
    memory = Memory()
    memory.record_transition("a", "sig-a", ActionType.TOUCH, "b")
    navigator = Navigator(memory)
    navigator.plan("a", [("b", elem("sig-b"), ActionType.TOUCH)])
    assert navigator.next_action("a", []) is None
    assert memory.is_blocked("a", "sig-a", ActionType.TOUCH)
    assert not navigator.is_navigating


def test_navigation_gives_up_after_the_step_budget():
    memory = Memory()
    chain = [f"p{i}" for i in range(MAX_NAVIGATE_STEPS + 4)]
    for src, dst in zip(chain, chain[1:], strict=False):
        memory.record_transition(src, f"sig-{src}", ActionType.TOUCH, dst)
    navigator = Navigator(memory)
    assert navigator.plan(chain[0], [(chain[-1], elem("goal"), ActionType.TOUCH)])

    taken = 0
    for page in chain:
        step = navigator.next_action(page, [elem(f"sig-{page}", ActionType.TOUCH)])
        if step is None:
            break
        taken += 1
    assert taken == MAX_NAVIGATE_STEPS


def test_planning_ignores_targets_on_the_current_page():
    memory = Memory()
    navigator = Navigator(memory)
    assert not navigator.plan("a", [("a", elem("s"), ActionType.TOUCH)])


# ---------------------------------------------------------------------------
# Explorer
# ---------------------------------------------------------------------------


def test_unexplored_actions_come_before_anything_else():
    explorer = Explorer()
    elements = elements_of(dump("settings_root"))
    explorer.observe(PAGE)
    decision = explorer.select(PAGE, elements)
    assert decision.reason == "explore"
    assert decision.action in {a for e in elements for a in e.allowed_actions}


def test_the_frontier_saturates_and_then_reports_exhausted():
    explorer = Explorer()
    elements = elements_of(dump("files_list"))
    seen = set()
    for _ in range(20):
        explorer.observe(PAGE)
        decision = explorer.select(PAGE, elements)
        if decision.is_fallback:
            assert decision.reason == "exhausted"
            break
        seen.add((decision.element.signature, decision.action))
    else:
        pytest.fail("the frontier never saturated")
    assert seen, "actions were taken before exhaustion"


def test_selection_is_deterministic_so_a_session_replays():
    """The reference uses random.choice; determinism is what makes a collected
    corpus auditable against its own event log."""
    elements = elements_of(dump("settings_root"))

    def run() -> list[str]:
        explorer = Explorer()
        out = []
        for _ in range(6):
            explorer.observe(PAGE)
            decision = explorer.select(PAGE, elements)
            if decision.is_fallback:
                break
            out.append(f"{decision.element.signature}:{decision.action}")
        return out

    assert run() == run()


def test_an_action_is_attributed_to_the_page_it_produced():
    """Attribution is deferred to the next observation because the outcome of an
    action is the page it produced, unknowable when it is chosen."""
    explorer = Explorer()
    element = elem("sig-a", ActionType.TOUCH)
    explorer.observe(PAGE)
    decision = explorer.select(PAGE, [element])
    assert decision.element is element

    explorer.observe(OTHER)
    assert explorer.memory.edges[PAGE][("sig-a", ActionType.TOUCH)] == OTHER


def test_an_excursion_is_never_recorded_as_a_transition():
    """Otherwise a crash-and-relaunch becomes an edge the router plans over, and
    only a crash can reproduce it."""
    explorer = Explorer()
    explorer.observe(PAGE)
    explorer.select(PAGE, [elem("sig-a", ActionType.TOUCH)])
    explorer.abandon_pending()
    explorer.observe(OTHER)
    assert PAGE not in explorer.memory.edges


def test_navigation_starts_when_the_current_page_is_exhausted():
    explorer = Explorer()
    here = elem("sig-here", ActionType.TOUCH)
    explorer.memory.record_transition(PAGE, "sig-here", ActionType.TOUCH, OTHER)
    explorer.memory.record_transition(OTHER, "sig-there", ActionType.TOUCH, "2")
    explorer.memory.nav_failed.clear()
    # PAGE's only action is explored; OTHER still has an unexplored edge action.
    explorer.memory.explored[OTHER] = set()

    explorer.observe(PAGE)
    decision = explorer.select(PAGE, [here])
    assert decision.reason == "navigate"
    assert decision.element is here


def test_a_page_with_no_actionable_elements_is_exhausted_immediately():
    explorer = Explorer()
    explorer.observe(PAGE)
    decision = explorer.select(PAGE, [])
    assert decision.is_fallback
    assert decision.reason == "exhausted"
