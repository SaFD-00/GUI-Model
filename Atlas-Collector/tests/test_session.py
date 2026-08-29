"""Session storage: observation numbering, triples, resume, and the image name."""

from __future__ import annotations

import json

import pytest

from atlas_collector.session import (
    DUMP_NAME,
    SCREENSHOT_NAME,
    Observation,
    Session,
    image_name,
)

PKG = "com.test.app"


def session(tmp_path, **kwargs) -> Session:
    return Session(PKG, tmp_path / "data", tmp_path / "runtime", **kwargs)


def obs(session_: Session, *, page="0", state="s0") -> Observation:
    return session_.write_observation(
        png=b"\x89PNG-fake",
        raw_xml="<hierarchy/>",
        page_key=page,
        activity=f"{PKG}/.Main",
        state_str=state,
        is_new_page=True,
        match_kind="new",
    )


# ---------------------------------------------------------------------------
# image_name — the silent-drop trap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("episode", "step", "expected"),
    [
        (0, 2, "episode_0_step_0002.jpg"),
        (0, 0, "episode_0_step_0000.jpg"),
        (12345, 9999, "episode_12345_step_9999.jpg"),
        (7, 12345, "episode_7_step_12345.jpg"),
    ],
)
def test_image_name_pads_the_step_but_not_the_episode(episode, step, expected):
    """EXP08 carries episode and step ONLY in this filename.

    A mis-padded step (`step_2`) still matches the builder's regex, so nothing
    aborts — it resolves to a file that does not exist and the record is dropped
    exactly as if it had been too long. Silent.
    """
    assert image_name(episode, step) == expected


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def test_an_observation_writes_both_artifacts(tmp_path):
    s = session(tmp_path)
    s.open()
    written = obs(s)
    directory = s.observation_path(written.index)
    assert (directory / SCREENSHOT_NAME).read_bytes() == b"\x89PNG-fake"
    assert (directory / DUMP_NAME).read_text() == "<hierarchy/>"


def test_observation_indices_are_dense_and_zero_padded(tmp_path):
    s = session(tmp_path)
    s.open()
    indices = [obs(s).index for _ in range(3)]
    assert indices == [0, 1, 2]
    assert (s.observations_dir / "0002").is_dir()


# ---------------------------------------------------------------------------
# Triples — stored as references, and `changed` as a fact
# ---------------------------------------------------------------------------


def test_a_triple_stores_two_references_not_two_copies(tmp_path):
    """The `after` of one step is the `before` of the next, byte-identical.

    Capturing them separately would let a clock tick or a notification slide in
    between, and the corpus would contain triples whose `before` never actually
    preceded that action.
    """
    s = session(tmp_path)
    s.open()
    a, b, c = obs(s, state="s0"), obs(s, state="s1"), obs(s, state="s2")
    s.write_triple(before=a, after=b, action={"action": "click"})
    s.write_triple(before=b, after=c, action={"action": "click"})

    triples = s.read_triples()
    assert [(t.before, t.after) for t in triples] == [(0, 1), (1, 2)]
    assert triples[0].after == triples[1].before, "one screen, one copy"
    assert len(list(s.observations_dir.iterdir())) == 3


def test_changed_and_page_changed_are_recorded_not_filtered(tmp_path):
    """Collect facts; filter at export. A filter applied here is unrecoverable
    and leaves no trace of what was dropped."""
    s = session(tmp_path)
    s.open()
    a = obs(s, page="0", state="same")
    b = obs(s, page="0", state="same")
    c = obs(s, page="0", state="scrolled")
    d = obs(s, page="1", state="elsewhere")

    noop = s.write_triple(before=a, after=b, action={"action": "click"})
    scroll = s.write_triple(before=b, after=c, action={"action": "swipe"})
    move = s.write_triple(before=c, after=d, action={"action": "click"})

    assert (noop.changed, noop.page_changed) == (False, False)
    assert (scroll.changed, scroll.page_changed) == (True, False), "same page, new state"
    assert (move.changed, move.page_changed) == (True, True)
    assert len(s.read_triples()) == 3, "the no-op is written, not dropped"


def test_steps_increment_per_triple_not_per_observation(tmp_path):
    s = session(tmp_path)
    s.open()
    a, b = obs(s), obs(s)
    assert s.step_count == 0
    s.write_triple(before=a, after=b, action={})
    assert s.step_count == 1
    assert s.observation_count == 2


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_continues_the_numbering(tmp_path):
    """Restarting at 0 would make older triples point at newer screens, and
    nothing in the data would flag it."""
    s = session(tmp_path)
    s.open()
    obs(s)
    obs(s)
    s.write_metadata(completed=False)

    again = session(tmp_path)
    assert again.open(resume=True) is True
    assert again.write_observation(
        png=b"x",
        raw_xml="<hierarchy/>",
        page_key="0",
        activity="a",
        state_str="s",
        is_new_page=False,
        match_kind="structure",
    ).index == 2


def test_a_fresh_session_does_not_inherit_a_previous_numbering(tmp_path):
    s = session(tmp_path)
    s.open()
    obs(s)
    s.write_triple(before=obs(s), after=obs(s), action={})

    fresh = session(tmp_path)
    fresh.open(resume=False)
    assert fresh.observation_count == 0
    assert fresh.read_triples() == [], "the old triples must not be appended to"


def test_completed_at_is_the_skip_signal_and_only_set_on_a_clean_end(tmp_path):
    s = session(tmp_path)
    s.open()
    assert not s.is_complete, "an in-flight session is resumed, not skipped"

    s.write_metadata(completed=True)
    assert session(tmp_path).is_complete


def test_metadata_carries_the_episode_and_extra_stats(tmp_path):
    s = session(tmp_path, episode=7)
    s.open()
    s.write_metadata(completed=True, extra={"pages": 3})
    meta = json.loads(s.metadata_path.read_text())
    assert meta["episode"] == 7
    assert meta["pages"] == 3
    assert meta["completed_at"] is not None


def test_persistent_and_volatile_roots_are_separate(tmp_path):
    """A reset of one must not silently resurrect the other."""
    s = session(tmp_path)
    s.open()
    assert s.root.is_relative_to(tmp_path / "data")
    assert s.runtime.is_relative_to(tmp_path / "runtime")
    assert not s.runtime.is_relative_to(s.root)


# ---------------------------------------------------------------------------
# Resume numbering
# ---------------------------------------------------------------------------


def test_extra_cannot_move_the_resume_point(tmp_path):
    """`extra` is the loop's stats dict, which carries its own `observations`
    key holding a PER-RUN count. Letting it win pinned the resume point at the
    first run's total: Markor resumed at observation 43 three times, each run
    overwriting the last one's screens while triples.jsonl kept appending."""
    session = Session("com.a", tmp_path / "data", tmp_path / "runtime", episode="com.a")
    session.open(resume=False)
    for index in range(5):
        session.write_observation(
            png=b"x",
            raw_xml="<hierarchy/>",
            page_key=str(index),
            activity="com.a/.Main",
            state_str=f"s{index}",
            is_new_page=True,
            match_kind="new",
        )
    session.write_metadata(completed=False, extra={"observations": 2, "steps": 1})
    assert session.read_metadata()["observations"] == 5

    resumed = Session("com.a", tmp_path / "data", tmp_path / "runtime", episode="com.a")
    assert resumed.open(resume=True)
    assert resumed.observation_count == 5, "a resume must not overwrite existing screens"


def test_resuming_twice_keeps_step_numbers_unique(tmp_path):
    """`image_name` derives the JPEG filename from the step, so a repeated step
    means two records claiming one image."""
    import json

    steps = []
    for _ in range(3):
        session = Session("com.a", tmp_path / "data", tmp_path / "runtime", episode="com.a")
        session.open(resume=True)
        previous = None
        for index in range(3):
            observation = session.write_observation(
                png=b"x",
                raw_xml="<hierarchy/>",
                page_key=str(index),
                activity="com.a/.Main",
                state_str=f"s{index}",
                is_new_page=True,
                match_kind="new",
            )
            if previous is not None:
                session.write_triple(
                    before=previous, after=observation, action={"action": "navigate_back"}
                )
            previous = observation
        session.write_metadata(completed=False, extra={"observations": 3, "steps": 2})
    rows = [json.loads(line) for line in session.triples_path.read_text().splitlines()]
    steps = [row["step"] for row in rows]
    assert len(steps) == len(set(steps)), f"duplicate step numbers: {steps}"


def test_a_session_killed_mid_run_resumes_past_what_is_on_disk(tmp_path):
    """Metadata is only rewritten when a run ENDS, so a killed session leaves it
    saying 0 -- and the supervisor restarts on exactly that kind of death."""
    import json

    session = Session("com.a", tmp_path / "data", tmp_path / "runtime", episode="com.a")
    session.open(resume=False)
    previous = None
    for index in range(4):
        observation = session.write_observation(
            png=b"x",
            raw_xml="<hierarchy/>",
            page_key=str(index),
            activity="com.a/.Main",
            state_str=f"s{index}",
            is_new_page=True,
            match_kind="new",
        )
        if previous is not None:
            session.write_triple(
                before=previous, after=observation, action={"action": "navigate_back"}
            )
        previous = observation
    # Killed: metadata still holds what open() wrote.
    assert json.loads(session.metadata_path.read_text())["observations"] == 0

    resumed = Session("com.a", tmp_path / "data", tmp_path / "runtime", episode="com.a")
    assert resumed.open(resume=True), "an existing session must be recognised"
    assert resumed.observation_count == 4

    observation = resumed.write_observation(
        png=b"y",
        raw_xml="<hierarchy/>",
        page_key="4",
        activity="com.a/.Main",
        state_str="s4",
        is_new_page=True,
        match_kind="new",
    )
    assert observation.index == 4, "the first screen after a crash must not overwrite one"
    after = resumed.write_observation(
        png=b"z",
        raw_xml="<hierarchy/>",
        page_key="5",
        activity="com.a/.Main",
        state_str="s5",
        is_new_page=True,
        match_kind="new",
    )
    resumed.write_triple(
        before=observation, after=after, action={"action": "navigate_back"}
    )
    rows = [json.loads(line) for line in resumed.triples_path.read_text().splitlines()]
    steps = [row["step"] for row in rows]
    assert len(steps) == len(set(steps)), f"duplicate step numbers: {steps}"
