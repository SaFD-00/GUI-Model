"""Bulk rules: the only way 10,000 steps get triaged by a human in an afternoon.

A RULE MAY ONLY ACT ON WHAT THE EXPORT WOULD SHIP
=================================================

The obvious rules to write first are the wrong ones. ``changed == false``,
"observation files missing", "screen belongs to another package", "dump will not
parse" -- the export ALREADY drops every one of those
(``ExportStats.dropped_unchanged`` and friends). A rule for them would show a
confident preview count, write hundreds of verdicts, and change not one record
in ``stage1_train.jsonl``. Preview counts that cannot move the output are worse
than no rules at all, because they look like progress.

So those live in the UI as view FILTERS (the reviewer can see why the export
already refuses them) and every rule here acts only on rows the export keeps.

WHAT THE MEASUREMENT SAID
=========================

Over the 2026-08-30 sweep (10 apps, 5,679 exportable triples), grouping by the
EXPORTED record -- ``(before html, translated action, after html)`` -- found
1,573 repeats, 27.7% of the corpus::

    code.name.monkey.retromusic   411 exportable    42 distinct   369 repeats (89.8%)
    com.simplemobiletools.gallery.pro
                                  668 exportable   135 distinct   533 repeats (79.8%)
    de.dennisguse.opentracks      440 exportable   249 distinct   191 repeats (43.4%)
    org.tasks                     683 exportable   529 distinct   154 repeats (22.5%)
    net.gsantner.markor           614 exportable   597 distinct    17 repeats ( 2.8%)

That is what ``duplicate`` is for, and it is also why NOTHING here is applied
automatically. An app whose first page load silently loses 90% of its rows is
the one bug in this tool that would be indistinguishable from it working. Every
rule is previewed with a count, applied only on an explicit press, and stamped
with its ``rule`` id so :meth:`~monkey_collector.review.store.DecisionStore.undo_rule`
can take it back.

``duplicate`` also is not always a corpus problem. retromusic's 369 repeats are
ONE stuck loop -- 417 recoveries, 8 restarts, 36 exhausted steps in its
metadata. Filtering it down to 42 rows produces a clean, tiny, and unrepresentative
app. The app card surfaces ``distinct`` for exactly that reason; the honest
response there is usually to collect it again, which this tool deliberately does
not do for you.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from monkey_collector.review.corpus import AppCorpus, StepRow

#: Below this many html-like nodes a screen is almost always a loading skeleton
#: rather than a page. Deliberately low: a genuinely sparse screen (a single
#: full-bleed photo, an empty-state) is a real screen, and a rule that eats those
#: is worse than one that misses a few skeletons.
TINY_NODE_THRESHOLD = 6

#: How many members of a duplicate group ``duplicate`` keeps. One, because the
#: record they collapse to is byte-identical -- a second copy adds no signal,
#: only weight in the loss.
DEFAULT_KEEP = 1


@dataclass(frozen=True)
class Rule:
    """One bulk predicate over the rows the export would keep."""

    id: str
    title: str
    detail: str
    reason: str
    #: Rows this rule proposes to EXCLUDE, given every row of one app.
    select: Callable[[list[StepRow], dict[str, Any]], list[StepRow]]
    #: Knobs the UI renders. ``{name: {"type": ..., "default": ...}}``.
    options: dict[str, Any] = None  # type: ignore[assignment]

    def apply(self, rows: list[StepRow], options: dict[str, Any] | None = None) -> list[StepRow]:
        merged = {k: v["default"] for k, v in (self.options or {}).items()}
        merged.update(options or {})
        exportable = [row for row in rows if row.exportable]
        return self.select(exportable, merged)


def _duplicates(rows: list[StepRow], options: dict[str, Any]) -> list[StepRow]:
    """Every member of a duplicate group past the first *keep*.

    Ordered by ``rank``, which :class:`~monkey_collector.review.corpus.AppCorpus`
    assigns in step order -- so the copies that survive are the EARLIEST ones.
    That is the arbitrary-but-stable choice: keeping the last would make the
    surviving record change every time the app is collected for longer.
    """
    try:
        keep = max(1, int(options.get("keep", DEFAULT_KEEP)))
    except (TypeError, ValueError):
        keep = DEFAULT_KEEP
    return [row for row in rows if row.group and row.rank >= keep]


def _same_html(rows: list[StepRow], options: dict[str, Any]) -> list[StepRow]:
    """Rows whose target is byte-identical to the state in the prompt.

    ``changed`` is computed from ``pagematch``'s ``state_str``, a hash over raw
    uiautomator node signatures. The export writes the SIMPLIFIED html-like tree.
    The two disagree: a change confined to a node the encoder prunes leaves
    ``changed=true`` and an exported record whose ``gpt`` value equals the XML in
    its own ``human`` turn -- teaching the model to echo its input, which is the
    thing ``keep_unchanged=False`` exists to prevent. Measured small but real
    (0 of 400 on org.tasks, 6 of 400 on markor).
    """
    return [row for row in rows if row.same_html]


def _tiny_dump(rows: list[StepRow], options: dict[str, Any]) -> list[StepRow]:
    try:
        threshold = max(1, int(options.get("nodes", TINY_NODE_THRESHOLD)))
    except (TypeError, ValueError):
        threshold = TINY_NODE_THRESHOLD
    return [row for row in rows if 0 < row.min_nodes < threshold]


def _coord_out_of_frame(rows: list[StepRow], options: dict[str, Any]) -> list[StepRow]:
    """Rows whose action lands outside the frame its boxes live in.

    The export COUNTS these (``coords_out_of_frame``, whose headline value in the
    canonical corpus is 0 of 20,000) but ships them anyway. A record pointing at
    a coordinate the image does not contain is unlearnable, so it is offered
    here as a rule rather than left to a stat nobody can act on.
    """
    return [row for row in rows if row.coord_out_of_frame > 0]


RULES: tuple[Rule, ...] = (
    Rule(
        id="duplicate",
        title="중복 레코드",
        detail=(
            "export 되는 형태가 완전히 같은 (before, action, after) 그룹에서 "
            "앞의 N개만 남기고 나머지를 제외한다."
        ),
        reason="no_effect",
        select=_duplicates,
        options={"keep": {"type": "int", "default": DEFAULT_KEEP, "min": 1, "max": 20}},
    ),
    Rule(
        id="same_html",
        title="before == after",
        detail="학습 타깃이 입력 XML 과 바이트 단위로 같은 레코드. 모델이 입력을 그대로 베끼게 된다.",
        reason="no_effect",
        select=_same_html,
        options={},
    ),
    Rule(
        id="tiny_dump",
        title="빈 화면 / 로딩 스켈레톤",
        detail="before 나 after 의 html 노드 수가 임계값 미만. 대개 아직 안 그려진 화면이다.",
        reason="broken",
        select=_tiny_dump,
        options={"nodes": {"type": "int", "default": TINY_NODE_THRESHOLD, "min": 2, "max": 40}},
    ),
    Rule(
        id="coord_oof",
        title="액션 좌표가 프레임 밖",
        detail="export 는 세기만 하고 그대로 내보낸다. 이미지 안에 없는 지점을 가리키는 레코드.",
        reason="broken",
        select=_coord_out_of_frame,
        options={},
    ),
)

RULES_BY_ID: dict[str, Rule] = {rule.id: rule for rule in RULES}


def catalog() -> list[dict[str, Any]]:
    """The rule list as the UI renders it."""
    return [
        {
            "id": rule.id,
            "title": rule.title,
            "detail": rule.detail,
            "reason": rule.reason,
            "options": rule.options or {},
        }
        for rule in RULES
    ]


def preview(
    corpus: AppCorpus,
    rule_id: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """How many rows *rule_id* would exclude, and a handful of examples.

    Split into ``new`` and ``already`` so a second preview after an apply reads
    "0 new" rather than repeating the original count and looking like nothing
    happened.
    """
    rule = RULES_BY_ID.get(rule_id)
    if rule is None:
        return {"error": f"unknown rule {rule_id!r}"}
    rows = rule.apply(corpus.rows(), options)
    return {
        "rule": rule_id,
        "reason": rule.reason,
        "matched": len(rows),
        "steps": [row.step for row in rows],
        "sample": [row.as_dict() for row in rows[:8]],
    }


def rows_for(corpus: AppCorpus, rule_id: str, options: dict[str, Any] | None = None) -> list[StepRow]:
    rule = RULES_BY_ID.get(rule_id)
    return [] if rule is None else rule.apply(corpus.rows(), options)


def verdict_rows(package: str, rows: Iterable[StepRow], *, rule: str, reason: str) -> list[dict[str, Any]]:
    """Turn selected rows into the verdicts the store appends.

    A rule's decision fans out to one verdict PER STEP -- never a group-level
    record. The exclusion unit is one step (that is the locked data model), and
    a group is only ever a way of showing many steps at once.
    """
    return [
        {
            "package": package,
            "step": row.step,
            "before": row.before,
            "after": row.after,
            "verdict": "exclude",
            "reason": reason,
            "key": row.key,
            "rule": rule,
        }
        for row in rows
    ]


__all__ = [
    "DEFAULT_KEEP",
    "RULES",
    "RULES_BY_ID",
    "TINY_NODE_THRESHOLD",
    "Rule",
    "catalog",
    "preview",
    "rows_for",
    "verdict_rows",
]
