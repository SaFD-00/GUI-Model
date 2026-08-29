"""The collection loop, driven by a fake device.

No test here touches a device: the collection target is a real, logged-in phone
(AGENTS §0.5/§1). Every screen comes from :mod:`tests.fakes`.
"""

from __future__ import annotations

import json

import pytest

from monkey_collector.adb import ScreenSize
from monkey_collector.aig import AIG
from monkey_collector.domain.activity_coverage import ActivityCoverageTracker
from monkey_collector.loop import (
    MAX_CONSECUTIVE_RECOVERIES,
    MAX_STALLED_STEPS,
    MAX_STEPS_OUTSIDE,
    CollectionLoop,
    is_launcher,
)
from monkey_collector.pagematch import PageRegistry
from monkey_collector.semantic import SemanticLabeler
from monkey_collector.session import Session
from monkey_collector.text_input import SAMPLE_TEXTS, TextGenerator
from tests.fakes import ACT, EMPTY_SCREEN, PKG, FakeAdb, Script, screen, text_field_screen

SIBLING = "com.other.target"


def build(tmp_path, adb, *, resume: bool = False, **kwargs) -> CollectionLoop:
    session = Session(PKG, tmp_path / "raw", tmp_path / "runtime")
    session.open(resume=resume)
    kwargs.setdefault("max_steps", 6)
    kwargs.setdefault("action_delay_ms", 0)
    kwargs.setdefault("stabilize", {"max_wait_sec": 0.2, "poll_ms": 0})
    kwargs.setdefault("launch_settle_sec", 0.0)
    return CollectionLoop(adb, session, **kwargs)


class StubTextGenerator(TextGenerator):
    """Records what it was asked for and answers with a fixed string."""

    def __init__(self, value: str = "generated-by-the-generator") -> None:
        self.value = value
        self.calls: list[dict[str, str]] = []

    def generate(self, **kwargs) -> str:
        self.calls.append(dict(kwargs))
        return self.value


# ---------------------------------------------------------------------------
# is_launcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ("com.google.android.apps.nexuslauncher", True),
        ("com.android.launcher3", True),
        (PKG, False),
        ("", False),
        (None, False),
    ],
)
def test_is_launcher(package, expected):
    assert is_launcher(package) is expected


# ---------------------------------------------------------------------------
# A whole session, end to end
# ---------------------------------------------------------------------------


def test_a_session_writes_observations_triples_graph_and_metadata(tmp_path):
    """The four artifacts of ARCHITECTURE §7, on disk, from one run."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcdefgh"])
    loop = build(tmp_path, adb, max_steps=4)
    stats = loop.run()

    root = tmp_path / "raw" / PKG
    assert stats.triples == 4
    assert stats.stop_reason == "step budget exhausted"
    assert [t.step for t in loop.session.read_triples()] == [0, 1, 2, 3]
    for index in range(loop.session.observation_count):
        assert (root / "observations" / f"{index:04d}" / "screenshot.png").is_file()
        assert (root / "observations" / f"{index:04d}" / "raw.xml").is_file()
    assert (root / "triples.jsonl").is_file()

    graph = json.loads((root / "graph.json").read_text())
    assert graph["package"] == PKG
    assert graph["nodes"] and graph["stats"]["pages"] == stats.pages
    assert graph["edges"], "the actions taken became edges"
    assert all(isinstance(e["from_page"], int) for e in graph["edges"]), "§6 spells ids as ints"

    meta = json.loads((root / "metadata.json").read_text())
    assert meta["triples"] == stats.triples
    assert meta["stop_reason"] == stats.stop_reason
    assert meta["observations"] == loop.session.observation_count


def test_consecutive_triples_share_an_observation(tmp_path):
    """The chain is what proves screens are stored once, not twice."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcdef"])
    loop = build(tmp_path, adb, max_steps=3)
    loop.run()
    triples = loop.session.read_triples()
    for earlier, later in zip(triples, triples[1:], strict=False):
        assert earlier.after == later.before


def test_the_action_record_is_the_domain_serialization(tmp_path):
    """NOT the EXP08 wire format: export translates and rescales at M5 (§8.2)."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcd"])
    loop = build(tmp_path, adb, max_steps=2)
    loop.run()
    for triple in loop.session.read_triples():
        assert triple.action["action_type"] in {"tap", "swipe", "long_press", "input_text",
                                                "press_back"}
        assert "action" not in triple.action, "the EXP08 name belongs to export"
        if triple.action["action_type"] == "tap":
            assert 0 <= triple.action["x"] <= 1080
            assert 0 <= triple.action["y"] <= 2400


def test_a_triple_carries_the_pages_either_side(tmp_path):
    """ARCHITECTURE §7's two extra fields, so a triple and an edge are traceable."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcd"])
    loop = build(tmp_path, adb, max_steps=2)
    loop.run()
    pages = {int(key) for key in loop.registry.pages}
    for triple in loop.session.read_triples():
        assert triple.from_page in pages and triple.to_page in pages
        assert triple.page_changed == (triple.from_page != triple.to_page)


def test_a_no_op_action_is_recorded_rather_than_dropped(tmp_path):
    """A world model learning that an action does nothing is learning something
    real. What to train on is export's decision, not the loop's."""
    adb = FakeAdb([Script(screen(tag="same"))])
    loop = build(tmp_path, adb, max_steps=3)
    stats = loop.run()
    assert stats.triples == 3
    assert stats.unchanged == 3


def test_the_graph_survives_a_session_that_never_finishes(tmp_path):
    """graph.json is written as the run goes, not only at the end."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcdef"])
    loop = build(tmp_path, adb, max_steps=6)

    saves: list[int] = []
    original = loop.graph.save

    def counting_save(path):
        saves.append(len(loop.graph.nodes))
        return original(path)

    loop.graph.save = counting_save  # type: ignore[method-assign]
    loop.run()
    assert len(saves) > 1, "the graph is saved during the run, not just at the end"
    assert (tmp_path / "raw" / PKG / "graph.json").is_file()


# ---------------------------------------------------------------------------
# The coordinate frame — export's only source for it
# ---------------------------------------------------------------------------


def test_metadata_records_the_device_size_from_wm_size(tmp_path):
    """§8.3: the frame comes from `wm size` at collection time, never from a
    dump (a dialog's window is legitimately smaller than the display) and never
    from config. A non-default size is used here so provenance is provable."""
    adb = FakeAdb([Script(screen())], size=ScreenSize(1440, 3120))
    loop = build(tmp_path, adb, max_steps=1)
    loop.run()

    meta = json.loads((tmp_path / "raw" / PKG / "metadata.json").read_text())
    assert (meta["device_width"], meta["device_height"]) == (1440, 3120)
    assert loop.session.device_size() == (1440, 3120)
    graph = json.loads((tmp_path / "raw" / PKG / "graph.json").read_text())
    assert graph["device"] == {"width": 1440, "height": 3120}


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def test_the_step_budget_stops_the_run(tmp_path):
    adb = FakeAdb([Script(screen(tag=str(i))) for i in range(50)])
    stats = build(tmp_path, adb, max_steps=3, max_duration_sec=600).run()
    assert stats.stop_reason == "step budget exhausted"
    assert stats.triples == 3


def test_the_time_budget_stops_the_run(tmp_path):
    # The screen must keep changing for the whole run: a static one ends the
    # session as stalled (MAX_STALLED_STEPS), and this test is about the CLOCK.
    adb = FakeAdb([Script(screen(tag=str(i))) for i in range(5000)])
    stats = build(tmp_path, adb, max_steps=0, max_duration_sec=0.4).run()
    assert stats.stop_reason == "time budget exhausted"
    assert stats.triples >= 1


def test_a_completed_budget_marks_the_session_complete(tmp_path):
    """`completed_at` is the skip signal, so it may only be set on a clean end."""
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcd"])
    loop = build(tmp_path, adb, max_steps=2)
    loop.run()
    assert loop.session.is_complete


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_continues_from_disk_and_does_not_duplicate_steps(tmp_path):
    """A session killed mid-flight leaves metadata claiming less than the disk
    holds. Resuming from the metadata would overwrite screens already written
    while triples.jsonl kept appending — measured on the sibling collector:
    three resumes at observation 43, 22 duplicate step numbers."""
    adb = FakeAdb([Script(screen(tag=str(i))) for i in range(50)])
    first = build(tmp_path, adb, max_steps=3)
    first.run()
    observations_after_first = first.session.observation_count
    steps_after_first = first.session.step_count

    # Simulate the death: metadata rewound to an empty session, disk untouched.
    meta_path = tmp_path / "raw" / PKG / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.update({"observations": 0, "steps": 0, "completed_at": None})
    meta_path.write_text(json.dumps(meta))

    second = build(tmp_path, adb, resume=True, max_steps=steps_after_first + 2)
    assert second.session.observation_count == observations_after_first
    second.run()

    steps = [t.step for t in second.session.read_triples()]
    assert len(steps) == len(set(steps)), "a resumed session must not reuse a step number"
    assert steps == sorted(steps)
    assert second.session.observation_count > observations_after_first


def test_extra_stats_cannot_move_the_resume_point(tmp_path):
    """LoopStats carries `observations` too; if it won, the resume point would
    stick at the first run's count."""
    adb = FakeAdb([Script(screen(tag=str(i))) for i in range(20)])
    loop = build(tmp_path, adb, max_steps=2)
    stats = loop.run()
    meta = json.loads((tmp_path / "raw" / PKG / "metadata.json").read_text())
    assert meta["observations"] == loop.session.observation_count
    assert meta["observations"] >= stats.observations


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def test_a_session_starts_the_app_cold(tmp_path):
    """A LAUNCHER intent resumes the app's existing task, so a session would
    otherwise inherit wherever the last run — or a person — left the app."""
    adb = FakeAdb([Script(screen())])
    build(tmp_path, adb, max_steps=1).run()
    assert adb.actions[0] == ("launch", (PKG, True))


def test_a_short_foreign_sub_flow_does_not_trigger_recovery(tmp_path):
    scripts = [Script(screen())]
    scripts += [
        Script(screen(package="com.other.helper", tag=str(i)), "com.other.helper/.Sub")
        for i in range(MAX_STEPS_OUTSIDE)
    ]
    stats = build(tmp_path, FakeAdb(scripts), max_steps=MAX_STEPS_OUTSIDE).run()
    assert stats.recoveries == 0, "a sub-flow is part of the app's experience"
    assert stats.steps_outside >= 1


def test_persistent_drift_into_another_package_restarts_cold(tmp_path):
    adb = FakeAdb(
        [
            Script(screen()),
            *[
                Script(screen(package="com.android.chrome", tag=str(i)),
                       "com.android.chrome/.Main")
                for i in range(MAX_STEPS_OUTSIDE + 3)
            ],
        ]
    )
    stats = build(tmp_path, adb, max_steps=MAX_STEPS_OUTSIDE + 3).run()
    assert stats.recoveries >= 1
    assert adb.clean_launches >= 2, "the start plus every drift recovery"


def test_recovering_from_the_launcher_resumes_rather_than_restarts(tmp_path):
    adb = FakeAdb(
        [
            Script(screen()),
            Script(
                screen(package="com.google.android.apps.nexuslauncher"),
                "com.google.android.apps.nexuslauncher/.Home",
            ),
            Script(screen(tag="back")),
        ]
    )
    stats = build(tmp_path, adb, max_steps=3).run()
    assert stats.recoveries >= 1
    assert ("launch", (PKG, False)) in adb.actions[1:], "the launcher recovery is soft"
    assert adb.clean_launches == 1, "only the session start was cold"


def test_a_launcher_excursion_never_becomes_a_graph_edge(tmp_path):
    """Otherwise the navigator plans routes over a transition only a mis-tap
    reproduces."""
    adb = FakeAdb(
        [
            Script(screen()),
            Script(
                screen(package="com.google.android.apps.nexuslauncher"),
                "com.google.android.apps.nexuslauncher/.Home",
            ),
            Script(screen(tag="back")),
        ]
    )
    loop = build(tmp_path, adb, max_steps=3)
    loop.run()
    recorded = {
        destination
        for transitions in loop.graph.edges.values()
        for destination in transitions.values()
    }
    assert recorded <= set(loop.registry.pages)
    assert len(loop.registry) == 1, "only the app's own screen became a page"


def test_repeated_dump_failures_relaunch_rather_than_crash(tmp_path):
    adb = FakeAdb([Script(screen())], fail_dumps=3)
    stats = build(tmp_path, adb, max_steps=2).run()
    assert stats.observe_failures == 3
    assert stats.recoveries >= 1


def test_a_run_that_cannot_stay_in_the_app_gives_up(tmp_path):
    """Spending the rest of a 2h budget on an app that will not stay open costs
    the whole schedule."""
    adb = FakeAdb(
        [
            Script(
                screen(package="com.google.android.apps.nexuslauncher"),
                "com.google.android.apps.nexuslauncher/.Home",
            )
        ]
    )
    loop = build(tmp_path, adb, max_steps=0, max_duration_sec=30)
    stats = loop.run()
    assert stats.stop_reason == "could not stay in the app"
    assert stats.recoveries == MAX_CONSECUTIVE_RECOVERIES
    assert not loop.session.is_complete, "giving up is not completion"


def test_an_empty_screen_still_advances_instead_of_spinning(tmp_path):
    adb = FakeAdb([Script(EMPTY_SCREEN)])
    stats = build(tmp_path, adb, max_steps=2).run()
    assert stats.exhausted >= 1
    assert ("key", ("KEYCODE_BACK",)) in adb.actions


def test_a_session_that_stops_making_progress_gives_up(tmp_path):
    """Markor opens on an onboarding carousel that BACK cannot leave. Measured
    on the sibling collector: 647 consecutive BACKs, 658 triples, 2 changed."""
    adb = FakeAdb([Script(screen())])  # one screen, forever: nothing ever changes
    stats = build(tmp_path, adb, max_steps=0, max_duration_sec=600).run()
    assert stats.stop_reason == "made no progress"
    assert stats.triples < MAX_STALLED_STEPS + 10, "it must not burn the budget"


def test_dead_ends_that_still_move_the_screen_do_not_count_as_stalled(tmp_path):
    """Backing out of a deep stack is also exhausted, and it is healthy."""
    scripts = [Script(screen(tag=str(i % 4))) for i in range(MAX_STALLED_STEPS * 3)]
    stats = build(tmp_path, FakeAdb(scripts), max_steps=MAX_STALLED_STEPS + 5).run()
    assert stats.stop_reason != "made no progress"


# ---------------------------------------------------------------------------
# Another app the catalog also collects
# ---------------------------------------------------------------------------


def test_a_sibling_targets_screen_never_enters_the_corpus(tmp_path):
    """A screen of app B is not app A's data. Export holds OOD out BY APP and an
    exported record carries no package, so one leaked frame is undetectable."""
    adb = FakeAdb(
        [
            Script(screen()),
            Script(screen(package=SIBLING, tag="x"), f"{SIBLING}/.Main"),
            Script(screen(tag="back")),
        ]
    )
    loop = build(tmp_path, adb, max_steps=3, sibling_packages={SIBLING})
    stats = loop.run()
    written = [
        (loop.session.observation_path(i) / "raw.xml").read_text()
        for i in range(loop.session.observation_count)
    ]
    assert not any(SIBLING in raw for raw in written), "a sibling screen was persisted"
    assert stats.recoveries >= 1
    assert stats.steps_outside == 0, "a sibling is never a tolerated excursion"


def test_the_first_sibling_handoff_is_backed_out_of_not_restarted(tmp_path):
    """A cold restart throws away the depth exploration reached; BACK pops the
    sibling off our task and keeps it."""
    adb = FakeAdb(
        [
            Script(screen()),
            Script(screen(package=SIBLING, tag="x"), f"{SIBLING}/.Main"),
            Script(screen(tag="back")),
        ]
    )
    build(tmp_path, adb, max_steps=3, sibling_packages={SIBLING}).run()
    assert ("key", ("KEYCODE_BACK",)) in adb.actions
    assert adb.clean_launches == 1, "only the session start was cold"


def test_a_sibling_that_survives_back_is_restarted_cold(tmp_path):
    """Joplin hands off to Markor's IntroActivity, which does not honour BACK."""
    adb = FakeAdb(
        [
            Script(screen()),
            *[Script(screen(package=SIBLING, tag=str(i)), f"{SIBLING}/.Main") for i in range(3)],
            Script(screen(tag="back")),
        ]
    )
    build(tmp_path, adb, max_steps=5, sibling_packages={SIBLING}).run()
    assert adb.clean_launches >= 2, "the start, then the escalation"


def test_a_non_catalog_helper_package_is_still_tolerated(tmp_path):
    """The Settings search screen really is
    `com.google.android.settings.intelligence` and belongs in the corpus."""
    adb = FakeAdb(
        [
            Script(screen()),
            Script(screen(package="com.helper.sheet", tag="h"), "com.helper.sheet/.Share"),
            Script(screen(tag="back")),
        ]
    )
    stats = build(tmp_path, adb, max_steps=3, sibling_packages={SIBLING}).run()
    assert stats.steps_outside >= 1
    assert stats.recoveries == 0


# ---------------------------------------------------------------------------
# Wiring: text, semantics, coverage, cost
# ---------------------------------------------------------------------------


def test_set_text_uses_the_generator_not_a_hardcoded_constant(tmp_path):
    """The sibling collector types the literal "test" because `text_for` was
    never wired. A corpus of one string is indistinguishable from a bug."""
    generator = StubTextGenerator()
    adb = FakeAdb([Script(text_field_screen())])
    loop = build(tmp_path, adb, max_steps=2, text_generator=generator)
    loop.run()

    assert adb.typed and adb.typed[0] == "generated-by-the-generator"
    assert "test" not in adb.typed
    assert generator.calls[0]["resource_id"] == "com.test.app:id/title"
    assert generator.calls[0]["screen_xml"], "the generator is given the screen"
    recorded = [t for t in loop.session.read_triples() if t.action["action_type"] == "input_text"]
    assert recorded and recorded[0].action["text"] == "generated-by-the-generator"


def test_without_a_generator_the_text_is_still_not_a_constant(tmp_path):
    adb = FakeAdb([Script(text_field_screen())])
    build(tmp_path, adb, max_steps=2).run()
    assert adb.typed and all(value in SAMPLE_TEXTS for value in adb.typed)


def test_a_failing_generator_never_fails_the_run(tmp_path):
    class Exploding(TextGenerator):
        def generate(self, **kwargs) -> str:
            raise RuntimeError("no API key")

    adb = FakeAdb([Script(text_field_screen())])
    stats = build(tmp_path, adb, max_steps=2, text_generator=Exploding()).run()
    assert stats.triples >= 1
    assert adb.typed and adb.typed[0] in SAMPLE_TEXTS


def test_the_keyboard_is_dismissed_after_typing(tmp_path):
    """Otherwise the next observation is a keyboard covering the page."""
    adb = FakeAdb([Script(text_field_screen())])
    build(tmp_path, adb, max_steps=2, text_generator=StubTextGenerator()).run()
    assert ("key", ("KEYCODE_ESCAPE",)) in adb.actions


def test_coverage_is_recorded_when_a_tracker_is_supplied(tmp_path):
    tracker = ActivityCoverageTracker()
    session_dir = tmp_path / "runtime" / "apps" / PKG
    session_dir.mkdir(parents=True)
    tracker.initialize(str(session_dir), [ACT], package=PKG, allow_dynamic_total=False)
    adb = FakeAdb([Script(screen(tag=t)) for t in "abc"])
    build(tmp_path, adb, max_steps=2, coverage=tracker).run()
    assert tracker.get_visited_count() == 1
    assert (session_dir / "activity_coverage.csv").is_file()


def test_the_labeller_titles_pages_and_leaves_an_audit_row(tmp_path):
    """Degraded labelling (no client) still records that pruning was off."""
    graph = AIG(package=PKG)
    labeler = SemanticLabeler(client=None, app_name="Test", enabled=False)
    adb = FakeAdb([Script(screen(tag=t)) for t in "abc"])
    loop = build(tmp_path, adb, max_steps=2, graph=graph, labeler=labeler)
    loop.run()

    document = json.loads((tmp_path / "raw" / PKG / "graph.json").read_text())
    assert document["semantic_labeling"] is False
    assert document["same_function_groups"], "every labelled state leaves a row"
    assert all(row["source"] == "none" for row in document["same_function_groups"])
    assert all(node["semantic_title"] for node in document["nodes"])


def test_semantic_labeling_records_what_actually_happened(tmp_path):
    """§5.2: a missing API key with labelling configured on must not be recorded
    as `true` — the field exists to explain exactly that divergence."""
    adb = FakeAdb([Script(screen())])
    loop = build(
        tmp_path,
        adb,
        max_steps=1,
        labeler=SemanticLabeler(client=None, enabled=True),  # configured on, no client
    )
    loop.run()
    meta = json.loads((tmp_path / "raw" / PKG / "metadata.json").read_text())
    assert meta["semantic_labeling"] is False


def test_the_explorer_shares_the_graph_the_loop_writes(tmp_path):
    """One graph: the routing memory and the deliverable are the same object."""
    graph = AIG(package=PKG)
    registry = PageRegistry()
    adb = FakeAdb([Script(screen(tag=t)) for t in "abcd"])
    loop = build(tmp_path, adb, max_steps=3, graph=graph, registry=registry)
    loop.run()
    assert loop.explorer.graph is graph
    assert graph.edge_records, "the explorer's actions became edges"
