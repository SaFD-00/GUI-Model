"""Catalog invariants.

``catalog/apps.csv`` was resolved against the live Pixel 6 and is committed
data, not a generated artifact. These tests pin the shape that milestone 3+
will depend on, so a careless edit is caught here rather than mid-collection.

Everything here is READ-ONLY against the committed CSV. The writer is exercised
only against ``tmp_path``. A round-trip through :func:`write_catalog` DOES
reproduce the committed ``catalog/apps.csv`` byte-for-byte (CRLF-terminated,
and ``csv.writer``'s QUOTE_MINIMAL happens to reproduce every gratuitously-
quoted row because each one carries an embedded quote or comma); that
byte-identity is pinned in
``tests/unit/test_provision.py::test_writer_reproduces_the_committed_catalog_byte_for_byte``,
not here — this file only checks that field *values* survive the round-trip
(:func:`test_write_then_read_preserves_field_values`).

The pinned counts below describe the FINALIZED 52-row catalog. Two things about
it are easy to get wrong and are therefore asserted explicitly:

* **No row is ``PENDING`` any more.** Every package id is resolved. The sentinel
  survives in code as a guard; a row reintroducing it must fail here.
* **``installed`` and ``status`` are independent columns** and disagree on many
  rows by design. See ``catalog.py`` for the full rationale, and
  ``test_installed_and_status_are_independent_columns`` for the pin.
"""

from __future__ import annotations

from collections import Counter

import pytest

from monkey_collector.catalog import (
    FIELDNAMES,
    PENDING_PACKAGE,
    VALID_STATUSES,
    AppRow,
    catalog_stats,
    filter_rows,
    load_catalog,
    write_catalog,
)
from monkey_collector.paths import catalog_path

#: The two rows whose on-device package is a re-skinned clone of the canonical
#: app. Deliberately kept: the user chose to collect what is actually installed.
CLONE_PACKAGES = ["com.emijotify.feesound", "com.yummely.app"]

TOTAL_ROWS = 52
INSTALLED_TRUE = 48

#: Rows the user decided not to collect (2026-08-28). Kept in the CSV rather
#: than deleted so the reason survives in ``notes``; see catalog.py's docstring.
EXCLUDED_PACKAGES = [
    "com.simplemobiletools.contacts.pro",
    "io.github.muntashirakon.Music",
    "org.gnucash.android",
    "com.ticktick.task",
]


def _row(**overrides: str) -> AppRow:
    """Build an AppRow from schema defaults, overriding only what a test cares about.

    Used where the behaviour under test is the SEMANTICS of the columns rather
    than a fact about the committed catalog, so the assertion survives any
    later curation or ``sync-installed`` change.
    """
    base = dict.fromkeys(FIELDNAMES, "")
    base.update(
        tier="playstore",
        app_name="Synthetic",
        package_id="synthetic.pkg",
        source="PlayStore",
        priority="Medium",
        auth_required="none",
        installed="false",
        device_verified="true",
        status="ok",
    )
    base.update(overrides)
    return AppRow(**base)


@pytest.fixture(scope="module")
def rows() -> list[AppRow]:
    return load_catalog()


# ---------------------------------------------------------------------------
# The pinned facts
# ---------------------------------------------------------------------------


def test_catalog_has_52_rows(rows):
    assert len(rows) == TOTAL_ROWS


def test_tier_counts(rows):
    assert Counter(r.tier for r in rows) == {
        "playstore": 30,
        "androidworld_fdroid": 14,
        "fdroid": 8,
    }


def test_installed_true_count_is_48(rows):
    # Explicit string comparison, never bool(field): bool("false") is True.
    assert sum(1 for r in rows if r.is_installed) == INSTALLED_TRUE


def test_status_counts(rows):
    assert Counter(r.status for r in rows) == {
        "ok": 46,
        "clone_accepted": 2,
        "excluded": 4,
    }


def test_no_row_is_pending_the_catalog_is_fully_resolved(rows):
    """Every package id is adjudicated; the PENDING sentinel is unused data."""
    pending = [r for r in rows if r.package_id == PENDING_PACKAGE]
    assert pending == []
    assert not any(r.is_pending for r in rows)
    assert catalog_stats(rows)["pending"] == 0


def test_exactly_two_clone_accepted_rows_with_the_known_packages(rows):
    clones = [r for r in rows if r.status == "clone_accepted"]
    assert len(clones) == 2
    assert sorted(r.package_id for r in clones) == sorted(CLONE_PACKAGES)


# ---------------------------------------------------------------------------
# `installed` (device fact) vs `status` (curation state) — independent columns
# ---------------------------------------------------------------------------


def test_installed_and_status_are_independent_columns():
    """Pin that the two columns are NOT mirrors of each other.

    ``installed`` is measured from the device and milestone 3's ``sync-installed``
    rewrites it; ``status`` is the hand-recorded curation verdict and must survive
    that sync untouched. If a future change derives one column from the other,
    this test is what catches it — the failure would otherwise be silent, because
    every row would still look individually plausible.

    Built from SYNTHETIC rows on purpose. An earlier version read the committed
    CSV and asserted that some ``status=ok`` row was absent from the device; that
    held only by accident of the snapshot, and it stopped holding the moment the
    device was provisioned (every ``ok`` row became installed). The semantics
    being pinned here do not depend on which apps happen to be on one handset.
    """
    absent_but_fine = _row(package_id="a.b.c", installed="false", status="ok")
    present_but_excluded = _row(package_id="d.e.f", installed="true", status="excluded")

    # Curation says fine, device says absent — a normal disagreement, not a bug.
    assert not absent_but_fine.is_installed
    assert not absent_but_fine.is_excluded
    assert not absent_but_fine.is_collectable, "absent apps cannot be driven"

    # The converse disagreement: on the device, but ruled out by curation.
    assert present_but_excluded.is_installed
    assert present_but_excluded.is_excluded
    assert not present_but_excluded.is_collectable, (
        "status=excluded must veto collection even when the APK is installed — "
        "otherwise a sync-installed run would silently re-enable a ruled-out app"
    )

    # Neither column is recoverable from the other.
    assert absent_but_fine.installed != present_but_excluded.installed
    assert absent_but_fine.status != present_but_excluded.status


def test_status_choices_offered_by_the_cli_match_the_data(rows):
    """`catalog --status X` must be able to select every status the CSV holds.

    The converse does NOT hold: ``VALID_STATUSES`` is the vocabulary the schema
    allows, not an inventory of what the current CSV uses. ``not_installed`` is
    currently unused, and asserting every valid status has a row would turn a
    legitimate curation change into a test failure.
    """
    present = {r.status for r in rows}
    assert present <= VALID_STATUSES
    for status in present:
        assert filter_rows(rows, status=status), f"no row has status {status!r}"


# ---------------------------------------------------------------------------
# Schema / parsing
# ---------------------------------------------------------------------------


def test_header_matches_fieldnames():
    with catalog_path().open(encoding="utf-8") as fh:
        header = fh.readline().strip()
    assert header == ",".join(FIELDNAMES)


def test_every_row_has_a_known_tier_auth_and_boolean_columns(rows):
    for row in rows:
        assert row.tier in {"androidworld_fdroid", "fdroid", "playstore"}
        assert row.auth_required in {"none", "account_optional", "account_required"}
        assert row.installed in {"true", "false"}
        assert row.device_verified in {"true", "false"}
        assert row.status in VALID_STATUSES
        assert row.app_name


def test_package_ids_are_unique(rows):
    package_ids = [r.package_id for r in rows]
    assert len(package_ids) == len(set(package_ids))


def test_every_installed_row_is_collectable(rows):
    """With no PENDING rows left, `installed` alone decides collectability."""
    assert [r for r in rows if r.is_collectable] == [r for r in rows if r.is_installed]


def test_missing_catalog_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_catalog(tmp_path / "nope.csv")


# ---------------------------------------------------------------------------
# Filters and stats
# ---------------------------------------------------------------------------


def test_filter_by_tier_and_installed(rows):
    playstore = filter_rows(rows, tier="playstore")
    assert len(playstore) == 30
    installed = filter_rows(rows, installed=True)
    assert len(installed) == INSTALLED_TRUE
    assert all(r.is_installed for r in installed)
    assert len(filter_rows(rows, installed=False)) == TOTAL_ROWS - INSTALLED_TRUE


def test_exclude_auth_drops_only_account_required(rows):
    kept = filter_rows(rows, include_auth=False)
    assert all(not r.needs_auth for r in kept)
    assert len(kept) == TOTAL_ROWS - sum(1 for r in rows if r.needs_auth)


def test_filter_by_status(rows):
    assert len(filter_rows(rows, status="clone_accepted")) == 2
    assert len(filter_rows(rows, status="excluded")) == len(EXCLUDED_PACKAGES)
    # `not_installed` is a valid status that no committed row currently uses.
    assert filter_rows(rows, status="not_installed") == []


def test_excluded_rows_are_the_four_unprovisionable_packages(rows):
    """The four apps the user ruled out, and the reason recorded for each."""
    excluded = [r for r in rows if r.is_excluded]
    assert sorted(r.package_id for r in excluded) == sorted(EXCLUDED_PACKAGES)
    for row in excluded:
        assert row.installed == "false", "an excluded row is not on the device"
        assert "수집 제외" in row.notes, f"{row.package_id} lost its exclusion reason"


def test_excluded_rows_are_never_collectable(rows):
    """The whole point of the status: M4 must not drive these apps."""
    assert not any(r.is_collectable for r in rows if r.is_excluded)
    assert catalog_stats(rows)["excluded"] == len(EXCLUDED_PACKAGES)
    assert catalog_stats(rows)["collectable"] == INSTALLED_TRUE


def test_catalog_stats_agree_with_the_pinned_facts(rows):
    stats = catalog_stats(rows)
    assert stats["total"] == TOTAL_ROWS
    assert stats["installed"] == INSTALLED_TRUE
    assert stats["not_installed"] == TOTAL_ROWS - INSTALLED_TRUE
    assert stats["pending"] == 0
    assert stats["excluded"] == len(EXCLUDED_PACKAGES)
    assert stats["collectable"] == INSTALLED_TRUE
    assert stats["tier"]["playstore"] == 30
    assert stats["status"]["clone_accepted"] == 2
    assert stats["status"]["excluded"] == len(EXCLUDED_PACKAGES)


# ---------------------------------------------------------------------------
# Writer — tmp_path only, never against the committed CSV
# ---------------------------------------------------------------------------


def test_write_then_read_preserves_field_values(rows, tmp_path):
    out = tmp_path / "apps.csv"
    write_catalog(rows, out)
    assert [r.as_dict() for r in load_catalog(out)] == [r.as_dict() for r in rows]
