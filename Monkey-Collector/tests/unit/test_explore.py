"""Element layer, action mapping and navigation.

Fixtures are real ``uiautomator`` dumps from the target Pixel 6, so the numbers
pinned here are measurements. Nothing in ``explore.py`` touches a device or an
LLM, so every test drives it directly.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from monkey_collector.aig import AIG
from monkey_collector.domain.actions import (
    InputText,
    LongPress,
    PressBack,
    Swipe,
    Tap,
)
from monkey_collector.explore import (
    MAX_NAVIGATE_STEPS,
    MAX_SUBTREE_TEXT,
    SCROLL_FROM_Y,
    SCROLL_TO_Y,
    ActionType,
    Decision,
    Element,
    Navigator,
    action_type_from_domain,
    domain_action_type,
    element_signature,
    elements_of,
    parse_bounds,
    subtree_text,
    to_domain_action,
)
from monkey_collector.pagematch import PageRegistry, ScreenState

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pages"
PAGE = "0"
OTHER = "1"

#: The activity each dump was captured under, as in ``test_pagematch.py``.
ACTIVITIES = {
    "settings_root": "com.android.settings/.Settings",
    "subsettings_a": "com.android.settings/.SubSettings",
    "subsettings_a_scrolled": "com.android.settings/.SubSettings",
    "files_list": "me.zhanghai.android.files/.filelist.FileListActivity",
    "markor_intro": "net.gsantner.markor/.activity.IntroActivity",
}


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
    from monkey_collector.pagematch import view_signature

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


def test_action_type_str_is_the_value_under_python_310():
    """``str, Enum`` without ``__str__`` would render ``ActionType.TOUCH``."""
    assert str(ActionType.TOUCH) == "touch"
    assert f"{ActionType.SET_TEXT}" == "set_text"


# ---------------------------------------------------------------------------
# Vocabulary -> the fixed action space (ARCHITECTURE §9)
# ---------------------------------------------------------------------------


def test_every_action_type_maps_onto_the_fixed_action_space():
    element = Element("s", (ActionType.TOUCH,), (100, 200, 300, 400), index=23)
    assert to_domain_action(element, ActionType.TOUCH) == Tap(
        x=200, y=300, element_index=23
    )
    assert to_domain_action(element, ActionType.LONG_TOUCH) == LongPress(
        x=200, y=300, element_index=23
    )
    assert to_domain_action(element, ActionType.SET_TEXT, text="hi") == InputText(
        text="hi", x=200, y=300, element_index=23
    )
    assert to_domain_action(element, ActionType.SCROLL, screen_height=2400) == Swipe(
        x1=200,
        y1=int(2400 * SCROLL_FROM_Y),
        x2=200,
        y2=int(2400 * SCROLL_TO_Y),
        element_index=23,
    )


def test_the_element_less_fallback_is_press_back():
    assert to_domain_action(None, None) == PressBack()
    assert to_domain_action(None, ActionType.TOUCH) == PressBack()


def test_the_recorded_element_index_comes_from_the_dump_order():
    element = Element("s", (ActionType.TOUCH,), (0, 0, 10, 10), index=7)
    assert to_domain_action(element, ActionType.TOUCH).to_dict()["element_index"] == 7


def test_a_scroll_without_a_screen_height_fails_loudly():
    """A silent (x, 0) -> (x, 0) swipe would look like a taken step and do nothing."""
    element = Element("s", (ActionType.SCROLL,), (0, 0, 10, 10), 0)
    with pytest.raises(ValueError, match="screen_height"):
        to_domain_action(element, ActionType.SCROLL)


@pytest.mark.parametrize("action", list(ActionType))
def test_the_domain_mapping_is_invertible(action):
    """``load()`` rebuilds the edge key from the stored DOMAIN action, so a
    one-way map would silently mint a graph nothing can route over."""
    assert action_type_from_domain(domain_action_type(action)) is action


@pytest.mark.parametrize("domain_type", ["press_back", "press_home", "open_app", ""])
def test_element_less_domain_actions_have_no_action_type(domain_type):
    with pytest.raises(ValueError, match="no ActionType"):
        action_type_from_domain(domain_type)


def test_select_and_unselect_are_not_separate_types():
    """The reference's ``select``/``unselect`` execute as ``input tap``, exactly
    like ``touch``, so they fold into TOUCH rather than growing the fixed space."""
    assert {a.value for a in ActionType} == {
        "touch",
        "long_touch",
        "set_text",
        "scroll",
    }


# ---------------------------------------------------------------------------
# Navigator
# ---------------------------------------------------------------------------


def test_navigation_re_matches_by_signature_not_coordinate():
    """A route survives the layout shifting between visits."""
    graph = AIG()
    graph.record_transition("a", "sig-a", ActionType.TOUCH, "b")
    navigator = Navigator(graph)
    target = elem("sig-b", ActionType.TOUCH)
    assert navigator.plan("a", [("b", target, ActionType.TOUCH)])

    moved = Element("sig-a", (ActionType.TOUCH,), (900, 1800, 1000, 1900), 7)
    step = navigator.next_action("a", [moved])
    assert step == (moved, ActionType.TOUCH)


def test_navigation_re_resolves_a_row_that_scrolled():
    """The required end-to-end proof, on a real before/after scroll pair.

    ``subsettings_a`` and ``subsettings_a_scrolled`` are the SAME Settings page
    dumped before and after a scroll — the test asserts that first, via
    ``pagematch``, so "same page" is established rather than assumed.

    A navigation step planned from the pre-scroll dump is then re-resolved
    against the post-scroll dump, where the row has moved by over a thousand
    pixels. What is under test is the NEGATIVE: ``mark_nav_failed`` must not
    fire. If element identity ever acquired a coordinate — or a second identity
    function such as the reference's ``view['desc']`` — this step would fail to
    resolve, the action would go onto the nav-failed set, and it would be
    skipped for the rest of the session while collection kept looking healthy.
    """
    registry = PageRegistry()
    before_page = registry.classify(
        ScreenState.from_dump(dump("subsettings_a"), ACTIVITIES["subsettings_a"])
    )
    after_page = registry.classify(
        ScreenState.from_dump(
            dump("subsettings_a_scrolled"), ACTIVITIES["subsettings_a_scrolled"]
        )
    )
    assert after_page.page_key == before_page.page_key, "the fixtures must be one page"
    target_page = before_page.page_key

    before = {e.signature: e for e in elements_of(dump("subsettings_a"))}
    after_elements = elements_of(dump("subsettings_a_scrolled"))
    after = {e.signature: e for e in after_elements}
    moved = sorted(s for s in before.keys() & after.keys() if before[s].bounds != after[s].bounds)
    assert moved, "the fixture pair must contain a row that survived the scroll AND moved"
    signature = moved[0]
    planned_element = before[signature]

    # Coordinate replay would not have found this row: nothing in the scrolled
    # dump sits at its old box.
    assert signature not in {e.signature for e in after_elements if e.bounds == planned_element.bounds}

    graph = AIG()
    graph.record_transition("entry", "sig-entry", ActionType.TOUCH, target_page)
    navigator = Navigator(graph)
    assert navigator.plan(
        "entry", [(target_page, planned_element, ActionType.TOUCH)]
    ), "a route to the page holding the target must exist"

    hop = navigator.next_action("entry", [elem("sig-entry", ActionType.TOUCH)])
    assert hop is not None, "the routing hop off the entry page"

    step = navigator.next_action(target_page, after_elements)
    assert step is not None, "the planned row must re-resolve after the scroll"
    resolved, action = step
    assert resolved.signature == signature
    assert action is ActionType.TOUCH
    assert resolved.bounds != planned_element.bounds, "the row really did move"
    assert not graph.is_blocked(target_page, signature, ActionType.TOUCH), (
        "mark_nav_failed must NOT have fired — that is the permanent skip this "
        "test exists to prevent"
    )
    assert not navigator.is_navigating, "the plan is complete"


def test_landing_off_course_drops_the_plan_instead_of_patching_it():
    graph = AIG()
    graph.record_transition("a", "sig-a", ActionType.TOUCH, "b")
    navigator = Navigator(graph)
    navigator.plan("a", [("b", elem("sig-b"), ActionType.TOUCH)])
    assert navigator.next_action("unexpected-page", []) is None
    assert not navigator.is_navigating


def test_an_unreachable_planned_element_is_marked_nav_failed():
    graph = AIG()
    graph.record_transition("a", "sig-a", ActionType.TOUCH, "b")
    navigator = Navigator(graph)
    navigator.plan("a", [("b", elem("sig-b"), ActionType.TOUCH)])
    assert navigator.next_action("a", []) is None
    assert graph.is_blocked("a", "sig-a", ActionType.TOUCH)
    assert not navigator.is_navigating


def test_navigation_gives_up_after_the_step_budget():
    graph = AIG()
    chain = [f"p{i}" for i in range(MAX_NAVIGATE_STEPS + 4)]
    for src, dst in zip(chain, chain[1:], strict=False):
        graph.record_transition(src, f"sig-{src}", ActionType.TOUCH, dst)
    navigator = Navigator(graph)
    assert navigator.plan(chain[0], [(chain[-1], elem("goal"), ActionType.TOUCH)])

    taken = 0
    for page in chain:
        step = navigator.next_action(page, [elem(f"sig-{page}", ActionType.TOUCH)])
        if step is None:
            break
        taken += 1
    assert taken == MAX_NAVIGATE_STEPS


def test_planning_ignores_targets_on_the_current_page():
    graph = AIG()
    navigator = Navigator(graph)
    assert not navigator.plan("a", [("a", elem("s"), ActionType.TOUCH)])


def test_planning_prefers_the_shortest_route():
    graph = AIG()
    graph.record_transition("a", "s1", ActionType.TOUCH, "b")
    graph.record_transition("b", "s2", ActionType.TOUCH, "c")
    graph.record_transition("a", "s3", ActionType.TOUCH, "c")
    navigator = Navigator(graph)
    assert navigator.plan(
        "a",
        [("b", elem("far"), ActionType.TOUCH), ("c", elem("near"), ActionType.TOUCH)],
    )
    step = navigator.next_action("a", [elem("s1", ActionType.TOUCH)])
    assert step is not None and step[0].signature == "s1", "b is one hop away"


def test_an_unreachable_target_is_not_planned():
    graph = AIG()
    navigator = Navigator(graph)
    assert not navigator.plan("a", [("unknown", elem("s"), ActionType.TOUCH)])


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def test_a_decision_without_an_element_is_the_fallback():
    assert Decision(None, None, reason="exhausted").is_fallback
    assert not Decision(elem("s"), ActionType.TOUCH, reason="explore").is_fallback
