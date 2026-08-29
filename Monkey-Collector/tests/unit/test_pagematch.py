"""Page identity, ported from LLM-Explorer.

The fixtures under ``tests/fixtures/pages/`` are real ``uiautomator`` dumps from
the target Pixel 6, so the numbers pinned here are measurements rather than
constructions.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from monkey_collector.pagematch import (
    DEFAULT_MAX_DIFF_ELEMENTS,
    MatchKind,
    MergePolicy,
    PageRegistry,
    ScreenState,
    content_free_signature,
    view_signature,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pages"

#: The real activity each dump was captured under.
ACTIVITIES = {
    "settings_root": "com.android.settings/.Settings",
    "subsettings_a": "com.android.settings/.SubSettings",
    "subsettings_a_scrolled": "com.android.settings/.SubSettings",
    "files_list": "me.zhanghai.android.files/.filelist.FileListActivity",
    "markor_intro": "net.gsantner.markor/.activity.IntroActivity",
}

#: The same Settings > Notifications page, before and after a scroll. Eight rows
#: of text change; the only content-free signatures that differ are an
#: off-screen Switch and its wrapper.
SAME_PAGE_PAIR = ("subsettings_a", "subsettings_a_scrolled")


def state(name: str) -> ScreenState:
    return ScreenState.from_dump(
        (FIXTURES / f"{name}.xml").read_text(encoding="utf-8"), ACTIVITIES[name]
    )


@pytest.fixture(scope="module")
def states() -> dict[str, ScreenState]:
    return {name: state(name) for name in ACTIVITIES}


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


def _node(**attrib: str):
    from xml.etree import ElementTree as ET

    return ET.Element("node", attrib)


def test_content_free_signature_drops_text_and_interaction_state():
    a = _node(**{"class": "X", "resource-id": "r", "visible-to-user": "true", "text": "One"})
    b = _node(**{"class": "X", "resource-id": "r", "visible-to-user": "true", "text": "Two"})
    assert content_free_signature(a) == content_free_signature(b)
    assert view_signature(a) != view_signature(b)


def test_long_text_is_nulled_in_the_content_aware_signature():
    """device_state.py:272 — text over 50 chars is content, not identity.

    Without this a body of prose makes every screen showing it unique, and a
    reading app never revisits a page.
    """
    short = _node(**{"class": "X", "text": "a" * 50})
    longer = _node(**{"class": "X", "text": "a" * 51})
    other = _node(**{"class": "X", "text": "b" * 80})
    assert view_signature(longer) == view_signature(other), "both nulled to None"
    assert view_signature(short) != view_signature(longer)


def test_interaction_state_is_part_of_the_content_aware_signature():
    off = _node(**{"class": "X", "checked": "false"})
    on = _node(**{"class": "X", "checked": "true"})
    assert view_signature(off) != view_signature(on)
    assert content_free_signature(off) == content_free_signature(on)


def test_system_chrome_is_excluded_from_identity():
    """A status/navigation bar is on every screen; letting it into the hash
    would make two otherwise-identical screens differ when it changes."""
    body = '<node class="A" resource-id="x"/>'
    bar = '<node class="B" resource-id="android:id/statusBarBackground"/>'
    plain = ScreenState.from_dump(f"<hierarchy>{body}</hierarchy>", "p/.A")
    with_bar = ScreenState.from_dump(f"<hierarchy>{body}{bar}</hierarchy>", "p/.A")
    assert plain.structure_str == with_bar.structure_str
    assert plain.state_str == with_bar.state_str


def test_identity_is_scoped_by_activity():
    xml = '<hierarchy><node class="A" resource-id="x"/></hierarchy>'
    assert (
        ScreenState.from_dump(xml, "p/.One").structure_str
        != ScreenState.from_dump(xml, "p/.Two").structure_str
    )


# ---------------------------------------------------------------------------
# The measured separation the default policy rests on
# ---------------------------------------------------------------------------


def test_the_same_page_scrolled_differs_by_the_budget(states):
    a, b = (states[n] for n in SAME_PAGE_PAIR)
    diff = len(a.element_sigs.symmetric_difference(b.element_sigs))
    assert diff == 2
    assert diff <= DEFAULT_MAX_DIFF_ELEMENTS
    assert a.structure_str != b.structure_str, "the hashes alone do NOT merge these"


def test_genuinely_different_screens_are_far_outside_the_budget(states):
    """The margin is what makes a budget of 2 safe rather than merely inherited.

    If this ever drops near the budget on a new device, narrow the policy to
    structure_only — do not widen the budget. Over-merging marks actions
    explored that were never tried, and that damage is invisible afterwards.
    """
    nearest = min(
        len(a.element_sigs.symmetric_difference(b.element_sigs))
        for (na, a), (nb, b) in itertools.combinations(states.items(), 2)
        if {na, nb} != set(SAME_PAGE_PAIR)
    )
    assert nearest >= 18, f"nearest distinct pair is {nearest}, margin has collapsed"


# ---------------------------------------------------------------------------
# PageRegistry
# ---------------------------------------------------------------------------


def test_an_identical_screen_matches_exactly(states):
    registry = PageRegistry()
    first = registry.classify(states["settings_root"])
    again = registry.classify(state("settings_root"))
    assert first.kind is MatchKind.NEW
    assert again.kind is MatchKind.STATE_EXACT
    assert again.page_key == first.page_key
    assert len(registry) == 1


def test_similar_elements_merges_the_scrolled_page(states):
    registry = PageRegistry(policy=MergePolicy.SIMILAR_ELEMENTS)
    first = registry.classify(states[SAME_PAGE_PAIR[0]])
    second = registry.classify(states[SAME_PAGE_PAIR[1]])
    assert second.page_key == first.page_key
    assert second.kind is MatchKind.SIMILAR_ELEMENTS
    assert second.diff == 2


def test_structure_only_reproduces_the_references_effective_behaviour(states):
    """The reference's LLM chooser is dead code, so it mints a new page here."""
    registry = PageRegistry(policy=MergePolicy.STRUCTURE_ONLY)
    first = registry.classify(states[SAME_PAGE_PAIR[0]])
    second = registry.classify(states[SAME_PAGE_PAIR[1]])
    assert second.page_key != first.page_key
    assert second.kind is MatchKind.NEW


def test_distinct_screens_never_merge_under_either_policy(states):
    for policy in MergePolicy:
        registry = PageRegistry(policy=policy)
        for name in ("settings_root", "files_list", "markor_intro"):
            registry.classify(states[name])
        assert len(registry) == 3, f"{policy} collapsed distinct screens"


def test_activities_are_not_merged_across(states):
    registry = PageRegistry(same_activity_only=True)
    registry.classify(states["files_list"])
    registry.classify(states["markor_intro"])
    assert len(registry) == 2


def test_page_keys_are_stable_insertion_ordered_integers(states):
    registry = PageRegistry()
    keys = [registry.classify(states[n]).page_key for n in ACTIVITIES]
    assert keys[:3] == ["0", "1", "1"], "the scrolled page reuses its page key"
    assert all(k.isdigit() for k in keys)


def test_observations_are_counted_per_page(states):
    registry = PageRegistry()
    for _ in range(3):
        registry.classify(states["settings_root"])
    assert registry.pages["0"].observations == 3


def test_a_zero_budget_disables_similar_merging(states):
    registry = PageRegistry(max_diff_elements=0)
    first = registry.classify(states[SAME_PAGE_PAIR[0]])
    second = registry.classify(states[SAME_PAGE_PAIR[1]])
    assert second.page_key != first.page_key


def test_a_negative_budget_is_rejected():
    with pytest.raises(ValueError, match="max_diff_elements"):
        PageRegistry(max_diff_elements=-1)


def test_in_app_check_matches_the_reference_guard(states):
    assert states["markor_intro"].is_in_app("net.gsantner.markor")
    assert not states["markor_intro"].is_in_app("com.android.settings")
    assert not states["markor_intro"].is_in_app("")


# ---------------------------------------------------------------------------
# Python 3.10 has no enum.StrEnum (the reference used 3.11+); MergePolicy and
# MatchKind are a plain str/Enum mixin instead. Pin that the substitution is
# behaviourally equivalent — including str(), which a bare mixin gets wrong
# by default (see the classes' docstrings).
# ---------------------------------------------------------------------------


def test_str_enum_substitution_matches_strenum_behaviour():
    assert MatchKind.NEW == "new"
    assert MatchKind.NEW.value == "new"
    assert str(MatchKind.NEW) == "new"
    assert f"{MatchKind.NEW}" == "new"
    assert MergePolicy("similar_elements") is MergePolicy.SIMILAR_ELEMENTS
    assert str(MergePolicy.SIMILAR_ELEMENTS) == "similar_elements"
