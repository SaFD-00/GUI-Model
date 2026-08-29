"""The App Interaction Graph and its ``graph.json`` contract.

ARCHITECTURE §6 is the specification. The schema assertions below are
deliberately explicit about KEY SETS rather than spot-checking values: a field
that quietly disappears from ``to_dict`` breaks a downstream consumer long after
the change that dropped it, and nothing else in the repo would notice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monkey_collector.aig import AIG, AIGEdge, AIGNode, SameFunctionGroup, graph_path
from monkey_collector.domain.actions import Tap
from monkey_collector.explore import (
    ActionType,
    Element,
    elements_of,
    to_domain_action,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pages"

PACKAGE = "com.android.settings"
ROOT_ACTIVITY = "com.android.settings/.Settings"
SUB_ACTIVITY = "com.android.settings/.SubSettings"

#: ARCHITECTURE §6.1, verbatim.
TOP_LEVEL_KEYS = {
    "package",
    "device",
    "semantic_labeling",
    "nodes",
    "edges",
    "same_function_groups",
    "stats",
}
NODE_KEYS = {
    "page_id",
    "activity",
    "package",
    "state_strs",
    "structure_strs",
    "semantic_title",
    "first_observation",
    "visits",
}
EDGE_KEYS = {
    "from_page",
    "to_page",
    "action",
    "element_signature",
    "semantic_element",
    "count",
    "effective",
    "steps",
}
GROUP_KEYS = {"minted_in_state", "page_id", "members", "source"}
STATS_KEYS = {
    "pages",
    "edges",
    "explored_actions",
    "unexplored_actions",
    "llm_calls",
}


def dump(name: str) -> str:
    return (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")


def elem(signature: str, *actions: ActionType, index: int = 0) -> Element:
    return Element(signature, actions or (ActionType.TOUCH,), (0, 0, 10, 10), index)


@pytest.fixture
def elements() -> list[Element]:
    return elements_of(dump("settings_root"))


@pytest.fixture
def graph(elements: list[Element]) -> AIG:
    """A graph with two pages, three traversals over two edges, and a frontier."""
    g = AIG(PACKAGE, device_width=1080, device_height=2400, semantic_labeling=False)
    g.note_page(
        "0",
        activity=ROOT_ACTIVITY,
        package=PACKAGE,
        state_str="a1b2c3",
        structure_str="9f8e7d",
        observation=42,
    )
    g.note_page("0", state_str="d4e5f6")
    g.note_page(
        "1",
        activity=SUB_ACTIVITY,
        package=PACKAGE,
        state_str="beef01",
        structure_str="cafe02",
        observation=58,
    )

    row = elements[0]
    scroller = next(e for e in elements if ActionType.SCROLL in e.allowed_actions)
    for step in (58, 91):
        g.record_transition(
            "0",
            row.signature,
            ActionType.TOUCH,
            "1",
            domain_action=to_domain_action(row, ActionType.TOUCH),
            step=step,
        )
    g.record_transition(
        "1",
        scroller.signature,
        ActionType.SCROLL,
        "1",
        domain_action=to_domain_action(scroller, ActionType.SCROLL, screen_height=2400),
        step=140,
    )
    g.unexplored("0", elements)
    return g


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_a_page_merges_several_state_strs(graph: AIG):
    """§6: nodes are PAGES, so ``state_strs`` is a list, not a scalar."""
    assert graph.nodes["0"].state_strs == ["a1b2c3", "d4e5f6"]
    assert graph.nodes["0"].structure_strs == ["9f8e7d"]


def test_visits_count_observations_not_node_lookups(graph: AIG):
    """``record_transition`` reaches page 1 but must not inflate its visits."""
    assert graph.nodes["0"].visits == 2
    assert graph.nodes["1"].visits == 1


def test_first_observation_is_the_first_one_only(graph: AIG):
    graph.note_page("0", observation=999)
    assert graph.nodes["0"].first_observation == 42


def test_seen_pages_are_in_first_visit_order(graph: AIG):
    assert graph.seen_pages == ["0", "1"]


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_coverage_is_keyed_on_the_page_not_the_screen_instance(elements):
    """An action tried once is known-tried on every later visit to that page —
    which is what lets coverage saturate instead of re-walking the same buttons."""
    g = AIG()
    target = elements[0]
    assert len(g.unexplored("0", elements)) > 0
    g.mark_explored("0", target.signature, ActionType.TOUCH)
    remaining = g.unexplored("0", elements)
    assert (target.signature, ActionType.TOUCH) not in {
        (e.signature, a) for _, e, a in remaining
    }


def test_coverage_does_not_leak_between_pages(elements):
    g = AIG()
    g.mark_explored("0", elements[0].signature, ActionType.TOUCH)
    assert len(g.unexplored("0", elements)) < len(g.unexplored("1", elements))


def test_long_touch_is_withheld_until_touch_has_been_tried():
    """A long press rarely has an independent effect (input_policy3.py:1069), so
    spending a step on it before the tap is waste."""
    g = AIG()
    element = elem("sig", ActionType.TOUCH, ActionType.LONG_TOUCH)
    assert {a for _, _, a in g.unexplored("0", [element])} == {ActionType.TOUCH}

    g.mark_explored("0", "sig", ActionType.TOUCH)
    assert {a for _, _, a in g.unexplored("0", [element])} == {ActionType.LONG_TOUCH}


def test_nav_failure_is_tracked_apart_from_coverage():
    """A routing failure is not evidence the action was tried — it stays
    available to direct exploration on the page itself."""
    g = AIG()
    g.mark_nav_failed("0", "sig", ActionType.TOUCH)
    assert g.is_blocked("0", "sig", ActionType.TOUCH)
    assert ("sig", ActionType.TOUCH) not in g.explored.get("0", set())


def test_shortest_path_finds_the_fewest_hops():
    g = AIG()
    g.record_transition("a", "s1", ActionType.TOUCH, "b")
    g.record_transition("b", "s2", ActionType.TOUCH, "c")
    g.record_transition("a", "s3", ActionType.TOUCH, "c")
    assert g.shortest_path("a", "c") == [("a", "s3", ActionType.TOUCH)]
    assert g.shortest_path("a", "a") == []
    assert g.shortest_path("a", "zzz") == []


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_repeating_an_edge_accumulates_count_and_steps(graph: AIG, elements):
    edge = graph.edge_records[("0", elements[0].signature, ActionType.TOUCH)]
    assert edge.count == 2
    assert edge.steps == [58, 91]


def test_effective_is_whether_the_page_changed(graph: AIG, elements):
    """The page-level analogue of ``from_state.state_str != to_state.state_str``
    (input_policy3.py:789)."""
    scroller = next(e for e in elements if ActionType.SCROLL in e.allowed_actions)
    assert graph.edge_records[("0", elements[0].signature, ActionType.TOUCH)].effective
    assert not graph.edge_records[("1", scroller.signature, ActionType.SCROLL)].effective


def test_an_edge_is_keyed_on_the_signature_so_a_scroll_does_not_fork_it():
    """Same row, new coordinates: one edge, two traversals."""
    g = AIG()
    before = Element("row", (ActionType.TOUCH,), (0, 2067, 1080, 2256), 5)
    after = Element("row", (ActionType.TOUCH,), (0, 879, 1080, 1068), 3)
    for element in (before, after):
        g.record_transition(
            "0",
            element.signature,
            ActionType.TOUCH,
            "1",
            domain_action=to_domain_action(element, ActionType.TOUCH),
        )
    assert len(g.edge_records) == 1
    assert g.edge_records[("0", "row", ActionType.TOUCH)].count == 2
    # Last write wins on the payload, so the recorded tap is the latest one.
    assert g.edge_records[("0", "row", ActionType.TOUCH)].action["y"] == 973


def test_the_stored_action_is_the_domain_serialization_not_the_wire_format(graph, elements):
    """§6: ``action`` is ``domain/actions.py``'s dict — export translates, not this."""
    edge = graph.edge_records[("0", elements[0].signature, ActionType.TOUCH)]
    assert edge.action["action_type"] == "tap"
    assert edge.action["element_index"] == elements[0].index
    assert set(edge.action) == set(Tap().to_dict())


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_report_the_frontier_seen_so_far(graph: AIG, elements):
    stats = graph.stats()
    assert stats["pages"] == 2
    assert stats["edges"] == 2
    assert stats["explored_actions"] == 2
    total_actions = sum(len(e.allowed_actions) for e in elements)
    # Page 0's frontier is everything seen there minus the one tapped row.
    assert stats["unexplored_actions"] == total_actions - 1
    assert stats["llm_calls"] == 0


def test_a_nav_failed_action_is_neither_explored_nor_unexplored(elements):
    g = AIG()
    g.unexplored("0", elements)
    baseline = g.stats()
    g.mark_nav_failed("0", elements[0].signature, ActionType.TOUCH)
    after = g.stats()
    assert after["explored_actions"] == baseline["explored_actions"]
    assert after["unexplored_actions"] == baseline["unexplored_actions"] - 1


# ---------------------------------------------------------------------------
# Serialization — the ARCHITECTURE §6 contract
# ---------------------------------------------------------------------------


def test_to_dict_matches_the_architecture_section_6_schema(graph: AIG):
    """Pin the key sets so the schema cannot drift silently.

    Every key is emitted unconditionally, including the ones M3a never
    populates (``semantic_title``, ``semantic_element``, ``same_function_groups``,
    ``llm_calls``) — omitting a null would make this assertion vacuous.
    """
    graph.same_function_groups.append(
        SameFunctionGroup(minted_in_state="a1b2c3", page_id="0", members=["x", "y"])
    )
    document = graph.to_dict()

    assert set(document) == TOP_LEVEL_KEYS
    assert set(document["device"]) == {"width", "height"}
    assert set(document["stats"]) == STATS_KEYS
    for node in document["nodes"]:
        assert set(node) == NODE_KEYS
    for edge in document["edges"]:
        assert set(edge) == EDGE_KEYS
    for group in document["same_function_groups"]:
        assert set(group) == GROUP_KEYS

    assert document["package"] == PACKAGE
    assert document["device"] == {"width": 1080, "height": 2400}
    assert document["semantic_labeling"] is False
    assert document["nodes"][0]["semantic_title"] is None
    assert document["edges"][0]["semantic_element"] is None
    assert document["same_function_groups"][0]["source"] == "none"


def test_page_ids_are_json_integers(graph: AIG):
    """§6 spells page ids as ints; ``pagematch`` mints them as decimal strings."""
    document = graph.to_dict()
    assert [n["page_id"] for n in document["nodes"]] == [0, 1]
    assert document["edges"][0]["from_page"] == 0
    assert document["edges"][0]["to_page"] == 1


def test_same_function_groups_is_empty_but_present_in_m3a(graph: AIG):
    """M3b fills it. The slot has to exist now so a group can never be minted
    without its audit record (§5.2)."""
    assert graph.to_dict()["same_function_groups"] == []


def test_save_load_round_trip_preserves_nodes_edges_and_frontier(
    graph: AIG, elements, tmp_path: Path
):
    """A resumed session that loses the graph re-explores what it already did.

    Nodes, edges and the frontier must all come back. ``explored`` is not stored
    separately because an edge IS the record that its action was performed.
    """
    destination = graph_path(tmp_path, PACKAGE)
    assert graph.save(destination) == destination
    assert destination.name == "graph.json"
    assert destination.parent == tmp_path / "raw" / PACKAGE

    restored = AIG.load(destination)

    assert restored.to_dict()["nodes"] == graph.to_dict()["nodes"]
    assert restored.to_dict()["edges"] == graph.to_dict()["edges"]
    assert restored.package == graph.package
    assert restored.device_width == graph.device_width
    assert restored.device_height == graph.device_height
    assert restored.semantic_labeling == graph.semantic_labeling
    assert restored.explored == graph.explored
    assert restored.edges == graph.edges
    assert restored.seen_pages == graph.seen_pages
    assert restored.shortest_path("0", "1") == graph.shortest_path("0", "1")

    before = graph.unexplored("0", elements)
    after = restored.unexplored("0", elements)
    assert [(p, e.signature, a) for p, e, a in after] == [
        (p, e.signature, a) for p, e, a in before
    ]
    assert restored.stats() == graph.stats()


def test_load_rebuilds_the_action_type_from_the_stored_domain_action(
    graph: AIG, tmp_path: Path, elements
):
    """§6's edge carries no explore-layer ``action_type``; it is inverted from
    the domain action. Get that wrong and the reloaded key routes nothing."""
    destination = graph.save(tmp_path / "graph.json")
    restored = AIG.load(destination)
    scroller = next(e for e in elements if ActionType.SCROLL in e.allowed_actions)
    assert ("1", scroller.signature, ActionType.SCROLL) in restored.edge_records
    assert restored.edge_records[
        ("1", scroller.signature, ActionType.SCROLL)
    ].action_type is ActionType.SCROLL


def test_an_edge_recorded_without_a_domain_action_still_round_trips(tmp_path: Path):
    """Regression: ``"action": {}`` saved a graph ``load()`` refused to read.

    §6 stores no explorer ``action_type``, so ``load`` inverts the domain one —
    and an empty dict has nothing to invert. Every edge therefore carries at
    least ``action_type``. The coordinate keys are OMITTED rather than zeroed,
    so a consumer reading ``action["x"]`` fails loudly instead of believing in a
    tap on the top-left corner.
    """
    g = AIG(PACKAGE)
    g.record_transition("0", "sig-a", ActionType.TOUCH, "1")
    payload = g.edge_records[("0", "sig-a", ActionType.TOUCH)].action
    assert payload == {"action_type": "tap", "element_index": -1}

    restored = AIG.load(g.save(tmp_path / "graph.json"))
    assert restored.edge_records[("0", "sig-a", ActionType.TOUCH)].action_type is (
        ActionType.TOUCH
    )
    assert restored.to_dict()["edges"] == g.to_dict()["edges"]


def test_save_creates_the_package_directory(tmp_path: Path):
    destination = AIG(PACKAGE).save(graph_path(tmp_path, PACKAGE))
    assert json.loads(destination.read_text(encoding="utf-8"))["package"] == PACKAGE


def test_round_trip_of_a_same_function_group(tmp_path: Path):
    g = AIG(PACKAGE)
    g.note_page("0")
    g.same_function_groups.append(
        SameFunctionGroup("a1b2c3", "0", ["sig-a", "sig-b"], source="llm")
    )
    restored = AIG.load(g.save(tmp_path / "graph.json"))
    assert restored.same_function_groups == g.same_function_groups


def test_dataclass_round_trips_are_symmetric():
    node = AIGNode("3", activity="A", package="p", state_strs=["x"], visits=2)
    assert AIGNode.from_dict(node.to_dict()) == node
    edge = AIGEdge(
        from_page="1",
        to_page="2",
        element_signature="sig",
        action_type=ActionType.TOUCH,
        action=Tap(x=1, y=2, element_index=3).to_dict(),
        count=1,
        effective=True,
        steps=[7],
    )
    assert AIGEdge.from_dict(edge.to_dict()) == edge
