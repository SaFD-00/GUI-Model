"""Bulk rules. Two properties matter more than the predicates themselves.

**A rule may only act on what the export would ship.** A rule for something the
export already drops produces a confident count, writes hundreds of verdicts,
and changes nothing in ``stage1_train.jsonl`` -- the worst kind of bug, because
it looks like progress. Every test here asserts against the exportable set.

**A rule never applies itself.** ``duplicate`` would remove 90% of one real app
(369 of its 411 exportable triples are one repeated record). A first page load
that silently did that would be indistinguishable from the tool working.
"""

from __future__ import annotations

from monkey_collector.review import rules
from monkey_collector.review.corpus import AppCorpus
from tests.unit.test_export import dump, tap, triple, write_session

PACKAGE = "com.example.app"


def corpus(tmp_path, triples, **kwargs):
    write_session(tmp_path / "raw", PACKAGE, triples, **kwargs)
    return AppCorpus(tmp_path / "raw", PACKAGE, cache_dir=tmp_path / "review" / "cache")


def repeated(count: int, action=None):
    """*count* triples that are ONE record in the exported corpus."""
    return [triple(step, action or tap(100, 200)) for step in range(count)]


# ---------------------------------------------------------------------------
# duplicate
# ---------------------------------------------------------------------------


def test_duplicate_keeps_the_earliest_and_selects_the_rest(tmp_path):
    same = {index: dump(label="one") for index in range(6)}
    app = corpus(tmp_path, repeated(5), dumps=same)

    selected = rules.rows_for(app, "duplicate")

    assert [row.step for row in selected] == [1, 2, 3, 4]


def test_duplicate_keep_n_is_honoured(tmp_path):
    same = {index: dump(label="one") for index in range(6)}
    app = corpus(tmp_path, repeated(5), dumps=same)

    selected = rules.rows_for(app, "duplicate", {"keep": 2})

    assert [row.step for row in selected] == [2, 3, 4]


def test_duplicate_never_selects_a_row_the_export_already_drops(tmp_path):
    same = {index: dump(label="one") for index in range(6)}
    triples = repeated(3) + [triple(3, changed=False), triple(4, changed=False)]
    app = corpus(tmp_path, triples, dumps=same)

    selected = rules.rows_for(app, "duplicate")

    assert all(row.exportable for row in selected)
    assert 3 not in [row.step for row in selected]


def test_preview_reports_the_count_without_writing_anything(tmp_path):
    same = {index: dump(label="one") for index in range(6)}
    app = corpus(tmp_path, repeated(4), dumps=same)

    result = rules.preview(app, "duplicate")

    assert result["matched"] == 3
    assert result["reason"] == "no_effect"
    assert not (tmp_path / "review" / "by-anon.jsonl").exists()


# ---------------------------------------------------------------------------
# the others
# ---------------------------------------------------------------------------


def test_same_html_selects_the_records_whose_target_is_their_own_input(tmp_path):
    same = {index: dump(label="one") for index in range(3)}
    app = corpus(tmp_path, [triple(0)], dumps=same)

    assert [row.step for row in rules.rows_for(app, "same_html")] == [0]


def test_tiny_dump_selects_a_screen_below_the_node_threshold(tmp_path):
    app = corpus(tmp_path, [triple(0)])

    assert rules.rows_for(app, "tiny_dump", {"nodes": 2}) == []
    assert [row.step for row in rules.rows_for(app, "tiny_dump", {"nodes": 40})] == [0]


def test_an_unknown_rule_selects_nothing_rather_than_raising(tmp_path):
    app = corpus(tmp_path, [triple(0)])

    assert rules.rows_for(app, "no-such-rule") == []
    assert "error" in rules.preview(app, "no-such-rule")


# ---------------------------------------------------------------------------
# fan-out
# ---------------------------------------------------------------------------


def test_a_group_decision_becomes_one_verdict_per_step(tmp_path):
    # The exclusion unit is one step. A group is a way of SHOWING many steps at
    # once, never a second kind of verdict -- nothing downstream would know how
    # to expand one.
    same = {index: dump(label="one") for index in range(5)}
    app = corpus(tmp_path, repeated(4), dumps=same)

    selected = rules.rows_for(app, "duplicate")
    verdicts = rules.verdict_rows(PACKAGE, selected, rule="duplicate", reason="no_effect")

    assert [v["step"] for v in verdicts] == [1, 2, 3]
    assert {v["verdict"] for v in verdicts} == {"exclude"}
    assert {v["rule"] for v in verdicts} == {"duplicate"}
    assert all(v["key"] for v in verdicts)


def test_the_catalog_exposes_every_rule_with_its_options():
    catalog = rules.catalog()

    assert {entry["id"] for entry in catalog} == set(rules.RULES_BY_ID)
    duplicate = next(entry for entry in catalog if entry["id"] == "duplicate")
    assert duplicate["options"]["keep"]["default"] == rules.DEFAULT_KEEP
