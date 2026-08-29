"""Semantic labelling, same-function grouping, and degradation.

No test here reaches an API. The client is a stub whose replies are written by
the test, which is the only way to pin the failure modes that matter: a reply
that is not JSON, a reply of the wrong shape, and a transport error.

The fixtures are real ``uiautomator`` dumps from the target Pixel 6, so the
element counts and page ids these tests compare are measurements of the same
screens the collector will see.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from monkey_collector.aig import AIG
from monkey_collector.config import ExplorationConfig, load_run_config
from monkey_collector.explore import ActionType, Element, Explorer, elements_of
from monkey_collector.pagematch import PageRegistry, ScreenState
from monkey_collector.semantic import (
    MIN_ELEMENTS_FOR_GROUPING,
    SemanticInfo,
    SemanticLabeler,
    describe_elements,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pages"
PACKAGE = "com.android.settings"
PAGE = "0"

#: The activity each dump was captured under, as in ``test_pagematch.py``.
ACTIVITIES = {
    "settings_root": "com.android.settings/.Settings",
    "subsettings_a": "com.android.settings/.SubSettings",
    "subsettings_a_scrolled": "com.android.settings/.SubSettings",
}


@pytest.fixture(autouse=True)
def _no_mc_env(monkeypatch):
    """Strip ``MC_*`` overrides, as ``test_config.py`` does.

    ``load_run_config`` reads the live environment, so a stray
    ``MC_EXPLORATION_*`` in the developer's shell would make the
    defaults assertion below flaky.
    """
    for key in list(os.environ):
        if key.startswith("MC_"):
            monkeypatch.delenv(key, raising=False)


def dump(name: str) -> str:
    return (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")


def state_of(name: str) -> ScreenState:
    return ScreenState.from_dump(dump(name), ACTIVITIES[name], PACKAGE)


def elem(signature: str, *actions: ActionType, index: int = 0) -> Element:
    return Element(signature, actions or (ActionType.TOUCH,), (0, 0, 10, 10), index)


def five() -> list[Element]:
    """Five elements, so the ``min_elements_for_grouping`` screen floor is met."""
    return [elem(c, index=i) for i, c in enumerate("abcde")]


def screen(structure: str = "st-0", state_str: str = "sa-0") -> ScreenState:
    return ScreenState(
        activity=f"{PACKAGE}/.MainActivity",
        package=PACKAGE,
        state_str=state_str,
        structure_str=structure,
        element_sigs=frozenset(),
    )


class StubClient:
    """Stands in for :class:`~monkey_collector.llm.client.LLMClient`.

    Records every prompt so the call-count guard can be measured rather than
    asserted about. A reply that is an ``Exception`` is raised instead of
    returned, which is how the transport-failure path is exercised.
    """

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies) or [json.dumps({"Page description": "A page"})]
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    def chat(self, messages, **kwargs) -> str:
        self.prompts.append(messages)
        self.kwargs.append(kwargs)
        reply = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return str(reply)

    @property
    def calls(self) -> int:
        return len(self.prompts)


def reply(*groups: list[int], page: str = "Settings list", elements: str = "rows") -> str:
    return json.dumps(
        {
            "Page description": page,
            "Element description": elements,
            "Same-function elements": [
                {"elements": list(g), "function": "f"} for g in groups
            ],
        }
    )


def labeller(client=None, **kwargs) -> SemanticLabeler:
    return SemanticLabeler(client=client, app_name="Settings", **kwargs)


# ---------------------------------------------------------------------------
# The call-count guard — ARCHITECTURE §5.3
# ---------------------------------------------------------------------------


def test_the_same_structure_is_labelled_once_however_often_it_is_seen():
    """The guard that keeps a session from being a four-figure bill.

    ``_gen_state_semantic_info`` runs per state, and the sibling collector's
    ``org.tasks`` session was 1,047 observations over 30 pages. The reuse key
    here is ``structure_str``, so the query count is the number of DISTINCT
    STRUCTURES, not of steps.
    """
    client = StubClient(reply([0, 1]))
    labeler = labeller(client)
    graph = AIG(PACKAGE, semantic_labeling=True)
    graph.note_page(PAGE)
    elements = [elem("a", index=0), elem("b", index=1)]

    infos = [
        labeler.observe(screen(state_str=f"sa-{i}"), PAGE, elements, graph)
        for i in range(25)
    ]

    assert client.calls == 1
    assert labeler.llm_calls == 1
    assert graph.stats()["llm_calls"] == 1
    assert len({id(i) for i in infos}) == 1, "every later state reuses the one answer"


def test_a_new_structure_costs_a_new_call():
    client = StubClient(reply(), reply())
    labeler = labeller(client)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    labeler.observe(screen("st-0"), PAGE, [elem("a")], graph)
    labeler.observe(screen("st-1"), PAGE, [elem("a")], graph)
    labeler.observe(screen("st-0"), PAGE, [elem("a")], graph)
    assert client.calls == 2


def test_two_real_dumps_of_one_scrolled_page_are_two_structures():
    """Honest accounting of what the guard does NOT collapse.

    ``pagematch`` merges these two dumps into one page via the
    symmetric-difference rule, but their ``structure_str`` values differ, so the
    labeller pays for both. The reference has exactly the same property (its key
    is an exact frame match); recording it here so the measured call count is
    never mistaken for one-per-page.
    """
    before, after = state_of("subsettings_a"), state_of("subsettings_a_scrolled")
    assert before.structure_str != after.structure_str
    registry = PageRegistry()
    assert (
        registry.classify(before).page_key == registry.classify(after).page_key
    ), "one page, two structures"

    client = StubClient(reply(), reply())
    labeler = labeller(client)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    labeler.observe(before, PAGE, elements_of(dump("subsettings_a")), graph)
    labeler.observe(after, PAGE, elements_of(dump("subsettings_a_scrolled")), graph)
    assert client.calls == 2


def test_a_disabled_labeller_never_calls_out():
    client = StubClient(reply([0, 1]))
    labeler = labeller(client, enabled=False)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, [elem("a"), elem("b")], graph)
    assert client.calls == 0
    assert info.source == "none"
    assert info.groups == ()


# ---------------------------------------------------------------------------
# Groups: indices in, signatures out
# ---------------------------------------------------------------------------


def test_group_ids_become_element_signatures():
    """The model answers with indices; ONE identity function is stored (§5.4)."""
    client = StubClient(reply([0, 2]))
    labeler = labeller(client)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, five(), graph)
    assert info.groups == (frozenset({"a", "c"}),)
    assert info.source == "llm"
    assert labeler.groups_by_page[PAGE] == info.groups


def test_out_of_range_ids_are_dropped_not_fatal():
    client = StubClient(reply([0, 1, 99]))
    labeler = labeller(client)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, five(), graph)
    assert info.groups == (frozenset({"a", "b"}),)


def test_a_group_of_one_is_dropped_because_it_prunes_nothing():
    """The rule skips an action when a DIFFERENT member has run it, so a
    single-member group can never fire. Keeping it would only pad the audit."""
    client = StubClient(reply([0], [1, 2]))
    labeler = labeller(client)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, five(), graph)
    assert info.groups == (frozenset({"b", "c"}),)


def test_min_elements_for_grouping_counts_the_screen_not_the_group():
    """The reference's ``MIN_SIZE_SAME_FUNCTION_ELEMENT_GROUP`` compares
    ``len(elements)`` — the whole screen — despite its name
    (input_policy3.py:478). Ported verbatim; do not "fix" it.

    A 3-element screen under a floor of 5 yields NO groups even though the model
    returned a perfectly good 3-member one. Lower the floor and the same reply
    produces the group.
    """
    elements = [elem("a", index=0), elem("b", index=1), elem("c", index=2)]
    assert len(elements) < MIN_ELEMENTS_FOR_GROUPING

    strict = labeller(StubClient(reply([0, 1, 2])), min_elements_for_grouping=5)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    assert strict.observe(screen(), PAGE, elements, graph).groups == ()

    lax = labeller(StubClient(reply([0, 1, 2])), min_elements_for_grouping=2)
    graph2 = AIG(PACKAGE)
    graph2.note_page(PAGE)
    assert lax.observe(screen(), PAGE, elements, graph2).groups == (
        frozenset({"a", "b", "c"}),
    )


def test_the_floor_still_yields_source_llm():
    """Grouping was suppressed by the screen size, not by a failure — the audit
    must not claim the model was absent."""
    labeler = labeller(StubClient(reply([0, 1])), min_elements_for_grouping=5)
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, [elem("a", index=0), elem("b", index=1)], graph)
    assert info.groups == ()
    assert info.source == "llm"
    assert graph.same_function_groups[0].source == "llm"


# ---------------------------------------------------------------------------
# Degradation — nothing here may raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "I'm sorry, I can't help with that.",
        "",
        "[1, 2, 3]",
        json.dumps({"Page description": "p", "Same-function elements": "not a list"}),
        "{{{{",
    ],
    ids=["prose", "empty", "not-an-object", "wrong-type", "broken"],
)
def test_a_broken_reply_degrades_instead_of_raising(bad):
    labeler = labeller(StubClient(bad))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    state = screen()
    info = labeler.observe(state, PAGE, [elem("a"), elem("b")], graph)
    assert info.source == "none"
    assert info.groups == ()
    assert info.page_label == state.structure_str


def test_a_transport_error_degrades_instead_of_raising():
    labeler = labeller(StubClient(RuntimeError("connection reset")))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, [elem("a")], graph)
    assert info.source == "none"


def test_a_failed_call_is_still_counted():
    """A call that failed after leaving the host still cost money."""
    labeler = labeller(StubClient(RuntimeError("boom")))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    labeler.observe(screen(), PAGE, [elem("a")], graph)
    assert labeler.llm_calls == 1


def test_a_fenced_reply_is_still_parsed():
    labeler = labeller(StubClient("```json\n" + reply([0, 1]) + "\n```"))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, five(), graph)
    assert info.source == "llm"
    assert info.groups == (frozenset({"a", "b"}),)


def test_a_reply_wrapped_in_prose_is_still_parsed():
    labeler = labeller(StubClient("Sure! " + reply([0, 1]) + " Hope that helps."))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    info = labeler.observe(screen(), PAGE, five(), graph)
    assert info.groups == (frozenset({"a", "b"}),)


def test_a_missing_page_description_falls_back_to_the_structure_hash():
    labeler = labeller(StubClient(json.dumps({"Same-function elements": []})))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    state = screen()
    info = labeler.observe(state, PAGE, [elem("a")], graph)
    assert info.page_label == state.structure_str
    assert info.source == "llm"


# ---------------------------------------------------------------------------
# The audit record — ARCHITECTURE §5.2 / §6
# ---------------------------------------------------------------------------


def test_every_group_is_audited_with_the_state_that_minted_it():
    """A wrong group is invisible in the corpus: the pruned action was never
    attempted, so nothing distinguishes "skipped" from "tried". The audit row
    is the only place it can be found afterwards."""
    labeler = labeller(StubClient(reply([0, 1], [2, 3])))
    graph = AIG(PACKAGE, semantic_labeling=True)
    graph.note_page(PAGE)
    state = screen(state_str="deadbe")
    labeler.observe(state, PAGE, five(), graph)

    rows = graph.same_function_groups
    assert len(rows) == 2
    assert {tuple(r.members) for r in rows} == {("a", "b"), ("c", "d")}
    for row in rows:
        assert row.minted_in_state == "deadbe"
        assert row.page_id == PAGE
        assert row.source == "llm"

    document = json.loads(json.dumps(graph.to_dict()))
    assert document["semantic_labeling"] is True
    assert document["same_function_groups"][0] == {
        "minted_in_state": "deadbe",
        "page_id": 0,
        "members": ["a", "b"],
        "source": "llm",
    }


def test_degraded_labelling_leaves_a_source_none_marker():
    """An empty ``members`` row is a NO-PRUNING-HERE marker, not a group.

    It is what makes a per-state failure visible: the top-level
    ``semantic_labeling`` flag cannot tell you that one structure out of thirty
    got an unusable reply.
    """
    labeler = labeller(StubClient(reply([0, 1]), "garbage"))
    graph = AIG(PACKAGE, semantic_labeling=True)
    graph.note_page(PAGE)
    labeler.observe(screen("st-0", "s0"), PAGE, five(), graph)
    labeler.observe(screen("st-1", "s1"), PAGE, five(), graph)

    rows = graph.same_function_groups
    assert [(r.source, r.members) for r in rows] == [
        ("llm", ["a", "b"]),
        ("none", []),
    ]


def test_a_reused_structure_is_not_audited_twice():
    labeler = labeller(StubClient(reply([0, 1])))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    for i in range(5):
        labeler.observe(screen("st-0", f"s{i}"), PAGE, five(), graph)
    assert len(graph.same_function_groups) == 1


def test_the_label_is_hung_on_the_node_and_never_mints_one():
    """Page identity is ``pagematch``'s alone (AGENTS §2(b)). Labelling an
    unknown page must not conjure a node."""
    labeler = labeller(StubClient(reply(page="Notes list")))
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    labeler.observe(screen(), PAGE, [elem("a")], graph)
    assert graph.nodes[PAGE].semantic_title == "Notes list"

    labeler.observe(screen("st-9", "s9"), "77", [elem("a")], graph)
    assert "77" not in graph.nodes


# ---------------------------------------------------------------------------
# The whole path, with and without a model
# ---------------------------------------------------------------------------


def drive(client, *, enabled: bool = True, seed: int = 42, steps: int = 12):
    """Run observe -> select over the real fixtures, as the M4 loop will."""
    names = ["settings_root", "subsettings_a", "subsettings_a_scrolled"]
    registry = PageRegistry()
    graph = AIG(PACKAGE, semantic_labeling=bool(client) and enabled)
    labeler = SemanticLabeler(client=client, app_name="Settings", enabled=enabled)
    engine = Explorer(graph, PACKAGE, config=ExplorationConfig(), seed=seed)

    pages: list[str] = []
    reasons: list[str] = []
    for step in range(steps):
        name = names[step % len(names)]
        state = state_of(name)
        match = registry.classify(state)
        graph.note_page(
            match.page_key,
            activity=state.activity,
            package=state.package,
            state_str=state.state_str,
            structure_str=state.structure_str,
            observation=step,
        )
        elements = elements_of(dump(name))
        labeler.observe(state, match.page_key, elements, graph)
        decision = engine.select(
            match.page_key,
            state,
            elements,
            groups_by_page=labeler.groups_by_page,
        )
        pages.append(match.page_key)
        reasons.append(decision.reason)
        if decision.element is not None and decision.action is not None:
            graph.record_transition(
                match.page_key,
                decision.element.signature,
                decision.action,
                match.page_key,
                step=step,
            )
    return pages, reasons, graph, labeler


def test_exploration_runs_end_to_end_with_no_llm_at_all():
    pages, reasons, graph, labeler = drive(None)
    assert labeler.llm_calls == 0
    assert graph.to_dict()["semantic_labeling"] is False
    assert all(r for r in reasons), "a decision every step"
    assert {r for r in reasons} <= {
        "explore_current",
        "explore_deferred",
        "navigate",
        "navigate_to_target",
        "fallback_random",
        "fallback_back",
        "escape_repeated_frame",
        "return_to_app",
    }
    assert graph.stats()["explored_actions"] > 0


def test_page_identity_is_identical_with_and_without_an_llm():
    """AGENTS §2(b)'s reproducibility contract, measured through the WHOLE path.

    The point is not that ``pagematch`` ignores the labeller — it is that
    nothing between them leaks either. If a run's page ids could move with an
    API's mood, the AIG and every coverage number from it would be meaningless.
    """
    without, _, _, _ = drive(None)
    with_llm, _, _, labeler = drive(StubClient(reply([0, 1, 2])))
    assert labeler.llm_calls > 0, "the model really was consulted"
    assert without == with_llm


def test_a_broken_model_does_not_stop_exploration():
    pages, reasons, graph, labeler = drive(StubClient("not json at all"))
    assert labeler.llm_calls > 0
    assert all(r for r in reasons)
    assert graph.stats()["explored_actions"] > 0
    assert {r.source for r in graph.same_function_groups} == {"none"}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_the_prompt_numbers_elements_from_zero():
    listing = describe_elements([elem("a", index=5), elem("b", index=9)])
    assert listing.startswith("0: <")
    assert "\n1: <" in listing


def test_the_prompt_carries_the_readable_label():
    elements = elements_of(dump("settings_root"))
    listing = describe_elements(elements)
    assert 'label="' in listing


def test_the_call_is_attributed_and_asks_for_json():
    client = StubClient(reply())
    graph = AIG(PACKAGE)
    graph.note_page(PAGE)
    labeller(client).observe(screen(), PAGE, [elem("a")], graph)
    assert client.kwargs[0]["agent"] == "semantic"
    assert client.kwargs[0]["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------


def test_the_shipped_config_matches_the_builtin_defaults():
    cfg = load_run_config()
    assert cfg.exploration == ExplorationConfig()
    assert cfg.llm.semantic_labeling is True


def test_semantic_info_reports_its_source():
    assert SemanticInfo("p", source="llm").from_llm
    assert not SemanticInfo("p").from_llm


def test_llm_calls_accumulate_onto_a_restored_count():
    """A resumed session must not rewrite its own billing history.

    ``AIG.load`` restores ``stats.llm_calls`` while the labeller starts fresh at
    zero, so ``observe`` must INCREMENT. Assigning would make a session that had
    already paid for 39 structures report 1.
    """
    graph = AIG(PACKAGE)
    graph.llm_calls = 39
    graph.note_page(PAGE)
    labeler = labeller(StubClient(reply(), reply()))
    labeler.observe(screen("st-0", "s0"), PAGE, five(), graph)
    labeler.observe(screen("st-1", "s1"), PAGE, five(), graph)
    labeler.observe(screen("st-0", "s2"), PAGE, five(), graph)
    assert labeler.llm_calls == 2
    assert graph.stats()["llm_calls"] == 41
