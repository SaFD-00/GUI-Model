"""Provisioning contracts: sync-installed, APK resolution, install, the ledger.

No device and no network, ever. ``FakeAdb`` stands in for :class:`AdbClient` and
``_urlopen`` — provision.py's single network seam — is monkeypatched in the one
test that needs it.

The committed ``catalog/apps.csv`` is read here but NEVER written: every write
goes to ``tmp_path``. The one exception is
:func:`test_writer_reproduces_the_committed_catalog_byte_for_byte`, which writes
a copy to ``tmp_path`` and compares BYTES against the committed original. Bytes,
not text: ``Path.read_text`` normalises newlines, so a text comparison would pass
even if the writer turned the file's CRLF terminators into LF — which is exactly
the reformat sync-installed must not commit.
"""

from __future__ import annotations

import io
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas_collector import provision as prov
from atlas_collector.adb import AdbError
from atlas_collector.catalog import AppRow, load_catalog, write_catalog
from atlas_collector.paths import catalog_path

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


def make_row(package_id: str, **overrides: str) -> AppRow:
    """A catalog row with plausible defaults; override exactly what a test is about."""
    base = {
        "tier": "fdroid",
        "category": "Productivity",
        "sub_category": "Notes",
        "app_name": package_id.rsplit(".", 1)[-1],
        "package_id": package_id,
        "source": "F-Droid",
        "priority": "High",
        "auth_required": "none",
        "installed": "false",
        "device_verified": "true",
        "status": "ok",
        "notes": "",
    }
    base.update(overrides)
    return AppRow(**base)  # type: ignore[arg-type]


class FakeAdb:
    """Enough of :class:`AdbClient` for provisioning, with no subprocess at all.

    ``installs`` records every ``adb install`` argv so a test can assert the
    retry actually happened rather than inferring it from a return value.
    """

    def __init__(self, packages: set[str] | None = None) -> None:
        self.packages: set[str] = set(packages or set())
        self.installs: list[list[str]] = []
        #: Queued (adb_returncode_ok, output, package_appears) per install attempt.
        self.script: list[tuple[bool, str, bool]] = []
        self.list_raises: Exception | None = None
        self.serial = "FAKESERIAL"

    def list_packages(self) -> list[str]:
        if self.list_raises is not None:
            raise self.list_raises
        return sorted(self.packages)

    def _run(self, args: list[str], *, timeout: float | None = None) -> Any:
        self.installs.append(args)
        ok, output, appears = self.script.pop(0) if self.script else (True, "Success", True)
        if appears:
            self.packages.add(Path(args[-1]).stem.split("_")[0])
        if not ok:
            raise AdbError(output)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=output, stderr="")


@pytest.fixture
def committed_rows() -> list[AppRow]:
    return load_catalog()


# ---------------------------------------------------------------------------
# sync-installed
# ---------------------------------------------------------------------------


def test_sync_flips_installed_in_both_directions() -> None:
    rows = [
        make_row("com.gone", installed="true"),
        make_row("com.arrived", installed="false"),
        make_row("com.unchanged", installed="true"),
    ]
    updated, flips = prov.sync_installed_rows(rows, {"com.arrived", "com.unchanged"})

    assert [r.installed for r in updated] == ["false", "true", "true"]
    assert {(f.package_id, f.before, f.after) for f in flips} == {
        ("com.gone", "true", "false"),
        ("com.arrived", "false", "true"),
    }


def test_sync_leaves_status_and_every_other_column_untouched() -> None:
    """THE load-bearing one: `installed` is a device fact, `status` is curation.

    Both rows are deliberately ones where the two columns DISAGREE, because a
    "helpful" reconciliation only shows up there — on a row where they already
    agree, a status-clobbering sync is indistinguishable from a correct one.
    """
    rows = [
        # Excluded by curation, absent from the catalog, but really on the device.
        make_row("com.ticktick.task", installed="false", status="excluded", notes="split APK"),
        # Curation says fine, catalog says installed, device says it is gone.
        make_row("org.tasks", installed="true", status="ok"),
    ]
    updated, _ = prov.sync_installed_rows(rows, {"com.ticktick.task"})

    assert [r.installed for r in updated] == ["true", "false"]
    for before, after in zip(rows, updated, strict=True):
        assert after.status == before.status
        # Everything except `installed` must be byte-identical.
        assert {k: v for k, v in after.as_dict().items() if k != "installed"} == {
            k: v for k, v in before.as_dict().items() if k != "installed"
        }


def test_sync_covers_excluded_rows_rather_than_skipping_them() -> None:
    """An excluded row that IS on the device must read installed=true.

    Skipping excluded rows would be the same conflation as deriving `status`
    from `installed`, just in the other direction — and `is_collectable` already
    keeps the row out of collection regardless of what `installed` says.
    """
    rows = [make_row("com.excluded", installed="false", status="excluded")]
    updated, flips = prov.sync_installed_rows(rows, {"com.excluded"})

    assert updated[0].installed == "true"
    assert updated[0].status == "excluded"
    assert updated[0].is_collectable is False
    assert len(flips) == 1


def test_writer_reproduces_the_committed_catalog_byte_for_byte(tmp_path: Path) -> None:
    """sync-installed must not smuggle in a reformat. Measured on the real file.

    catalog.py once warned this round-trip was lossy. It is not: the committed
    file is CRLF-terminated, which matches csv.writer's excel dialect, and every
    gratuitously-quoted row contains an embedded quote or comma, which
    QUOTE_MINIMAL reproduces. If that ever stops being true this test fails
    BEFORE a sync rewrites 52 rows of committed data.
    """
    out = tmp_path / "apps.csv"
    write_catalog(load_catalog(), out)
    assert out.read_bytes() == catalog_path().read_bytes()


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserted on the file's BYTES, not on the exit code — exit-code-0 is what
    a no-op flag returns too."""
    from atlas_collector import cli

    target = tmp_path / "apps.csv"
    write_catalog(load_catalog(), target)
    before = target.read_bytes()

    fake = FakeAdb(packages=set())  # device has nothing: every true row would flip
    monkeypatch.setattr(cli, "_device_packages", lambda args, command: (fake, set()))

    assert cli.main(["sync-installed", "--catalog", str(target), "--dry-run"]) == 0
    assert target.read_bytes() == before

    assert cli.main(["sync-installed", "--catalog", str(target)]) == 0
    assert target.read_bytes() != before
    assert all(r.installed == "false" for r in load_catalog(target))
    # ...and the curation column rode through the real write untouched.
    assert [r.status for r in load_catalog(target)] == [r.status for r in load_catalog()]


# ---------------------------------------------------------------------------
# APK resolution / source priority
# ---------------------------------------------------------------------------


@pytest.fixture
def sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    cache = tmp_path / "cache"
    aw = tmp_path / "android_world"
    dest = tmp_path / "downloads"
    cache.mkdir()
    aw.mkdir()
    return cache, aw, dest


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(url: str, timeout: float) -> Any:
        raise AssertionError(f"a resolution reached the network: {url}")

    monkeypatch.setattr(prov, "_urlopen", explode)


def test_cache_wins_over_android_world_and_fdroid(
    sources: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, aw, dest = sources
    _no_network(monkeypatch)
    (cache / "org.tasks.apk").write_bytes(b"cached")
    (aw / "org.tasks_130605.apk").write_bytes(b"pinned")

    result = prov.resolve_apk(
        "org.tasks", cache_dir=cache, aw_dir=aw, dest_dir=dest, allow_download=True
    )
    assert result.source == prov.SOURCE_CACHE
    assert result.path == cache / "org.tasks.apk"


def test_android_world_wins_over_fdroid_when_the_cache_misses(
    sources: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, aw, dest = sources
    _no_network(monkeypatch)
    (aw / "com.simplemobiletools.calendar.pro_238.apk").write_bytes(b"pinned")

    result = prov.resolve_apk(
        "com.simplemobiletools.calendar.pro",
        cache_dir=cache,
        aw_dir=aw,
        dest_dir=dest,
        allow_download=True,
    )
    assert result.source == prov.SOURCE_ANDROID_WORLD
    assert result.version_code == 238


def test_fdroid_is_the_last_resort(
    sources: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, aw, dest = sources
    seen: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> Any:
        seen.append(url)
        if "/api/" in url:
            return io.BytesIO(json.dumps({"suggestedVersionCode": 42}).encode())
        return io.BytesIO(b"apk-bytes")

    monkeypatch.setattr(prov, "_urlopen", fake_urlopen)
    result = prov.resolve_apk(
        "net.example.app", cache_dir=cache, aw_dir=aw, dest_dir=dest, allow_download=True
    )

    assert result.source == prov.SOURCE_FDROID
    assert result.path is not None and result.path.read_bytes() == b"apk-bytes"
    assert result.path.name == "net.example.app_42.apk"
    assert seen == [
        "https://f-droid.org/api/v1/packages/net.example.app",
        "https://f-droid.org/repo/net.example.app_42.apk",
    ]


def test_tls_failure_is_recorded_not_bypassed(
    sources: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A certificate failure must surface as an unresolved package with a readable
    reason, never as a retry with verification switched off.

    The catalog records this failure for Metro; a live re-measurement on
    2026-08-28 downloaded that same APK successfully through `_urlopen`, so the
    failure is client-specific rather than a property of the host. The contract
    pinned here is what provision does WHEN verification fails, which is the part
    that must not drift regardless.
    """
    cache, aw, dest = sources

    def fake_urlopen(url: str, timeout: float) -> Any:
        if "/api/" in url:
            return io.BytesIO(json.dumps({"suggestedVersionCode": 10603}).encode())
        raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr(prov, "_urlopen", fake_urlopen)
    result = prov.resolve_apk(
        "io.github.muntashirakon.Music",
        cache_dir=cache,
        aw_dir=aw,
        dest_dir=dest,
        allow_download=True,
    )

    assert result.path is None
    assert "CERTIFICATE_VERIFY_FAILED" in result.reason
    # the .part file is removed, so the next run cannot mistake it for a cache hit
    assert list(dest.glob("*")) == []


def test_no_download_stops_at_the_local_sources(
    sources: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, aw, dest = sources
    _no_network(monkeypatch)
    result = prov.resolve_apk(
        "com.absent", cache_dir=cache, aw_dir=aw, dest_dir=dest, allow_download=False
    )
    assert result.path is None
    assert "download not attempted" in result.reason


def test_android_world_prefix_match_requires_a_separator(tmp_path: Path) -> None:
    """`..._pro_238.apk` must not resolve for the shorter package id.

    A bare startswith would install a different app and report success.
    """
    aw = tmp_path / "aw"
    aw.mkdir()
    (aw / "com.simplemobiletools.calendar.pro_238.apk").write_bytes(b"x")

    assert prov.find_android_world_apk("com.simplemobiletools.calendar", aw) is None
    assert prov.find_android_world_apk("com.simplemobiletools.calendar.pro", aw) is not None


def test_android_world_picks_the_highest_version_code(tmp_path: Path) -> None:
    aw = tmp_path / "aw"
    aw.mkdir()
    (aw / "org.videolan.vlc_13050407.apk").write_bytes(b"old")
    (aw / "org.videolan.vlc_13050408.apk").write_bytes(b"new")

    found = prov.find_android_world_apk("org.videolan.vlc", aw)
    assert found is not None
    assert found[0].name == "org.videolan.vlc_13050408.apk"
    assert found[1] == 13050408


def test_android_world_tolerates_a_version_name_suffix(tmp_path: Path) -> None:
    """`net.osmand-4.6.13.apk` carries a versionNAME; int() would raise on it."""
    aw = tmp_path / "aw"
    aw.mkdir()
    (aw / "net.osmand-4.6.13.apk").write_bytes(b"x")

    found = prov.find_android_world_apk("net.osmand", aw)
    assert found is not None
    assert found[0].name == "net.osmand-4.6.13.apk"
    assert found[1] is None


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_plan_skips_excluded_rows_even_under_force() -> None:
    """Exclusion is a decision about the app, not about this handset."""
    rows = [make_row("com.ticktick.task", status="excluded"), make_row("com.fine")]

    plan = prov.plan_provision(rows, set(), force=True)
    assert [r.package_id for r in plan.targets] == ["com.fine"]
    assert [(r.package_id, why) for r, why in plan.skipped] == [
        ("com.ticktick.task", prov.SKIP_EXCLUDED)
    ]


def test_plan_skips_installed_rows_unless_forced() -> None:
    rows = [make_row("com.here"), make_row("com.absent")]

    plan = prov.plan_provision(rows, {"com.here"})
    assert [r.package_id for r in plan.targets] == ["com.absent"]
    assert [why for _, why in plan.skipped] == [prov.SKIP_INSTALLED]

    forced = prov.plan_provision(rows, {"com.here"}, force=True)
    assert [r.package_id for r in forced.targets] == ["com.here", "com.absent"]


def test_plan_reads_the_device_not_the_installed_column() -> None:
    """A stale catalog column must not make provision skip an absent app."""
    rows = [make_row("com.stale", installed="true")]
    plan = prov.plan_provision(rows, set())
    assert [r.package_id for r in plan.targets] == ["com.stale"]


def test_plan_reports_only_ids_that_match_no_row() -> None:
    rows = [make_row("com.real")]
    plan = prov.plan_provision(rows, set(), only=["com.real", "com.typo"])
    assert [r.package_id for r in plan.targets] == ["com.real"]
    assert plan.unknown == ["com.typo"]


def test_scope_includes_skipped_rows_so_stale_entries_can_clear() -> None:
    rows = [make_row("com.here"), make_row("com.absent")]
    plan = prov.plan_provision(rows, {"com.here"})
    assert plan.scope_ids == {"com.here", "com.absent"}


# ---------------------------------------------------------------------------
# Install verification
# ---------------------------------------------------------------------------


def test_install_believes_the_device_over_adbs_exit_code() -> None:
    """The observed OsmAnd case: adb reported a timeout, the device had it."""
    fake = FakeAdb()
    fake.script = [(False, "adb timed out: install", True)]

    ok, note = prov.install_apk(fake, "net.osmand", Path("/tmp/net.osmand.apk"))  # type: ignore[arg-type]
    assert ok is True
    assert "device has it" in note
    assert len(fake.installs) == 1  # verified, not retried


def test_a_forced_reinstall_does_not_take_presence_as_proof() -> None:
    """Under --force the package is already there, so the re-query proves nothing.

    A rejected reinstall (downgrade, signature mismatch, corrupt file) leaves the
    OLD build in place; taking presence as success would report OK and write no
    ledger entry, which is exactly the trust-the-exit-code failure inverted.
    """
    fake = FakeAdb(packages={"org.tasks"})
    fake.script = [
        (False, "INSTALL_FAILED_VERSION_DOWNGRADE", True),
        (False, "INSTALL_FAILED_VERSION_DOWNGRADE", True),
    ]

    ok, note = prov.install_apk(
        fake,  # type: ignore[arg-type]
        "org.tasks",
        Path("/tmp/org.tasks.apk"),
        already_present=True,
    )
    assert ok is False
    assert "VERSION_DOWNGRADE" in note
    assert len(fake.installs) == 2  # retried, then gave up — never declared success


def test_a_forced_reinstall_that_adb_accepts_is_a_success() -> None:
    fake = FakeAdb(packages={"org.tasks"})
    fake.script = [(True, "Success", True)]

    ok, _ = prov.install_apk(
        fake,  # type: ignore[arg-type]
        "org.tasks",
        Path("/tmp/org.tasks.apk"),
        already_present=True,
    )
    assert ok is True


def test_install_retries_once_then_gives_up() -> None:
    fake = FakeAdb()
    fake.script = [(False, "boom", False), (False, "boom again", False)]

    ok, note = prov.install_apk(fake, "com.nope", Path("/tmp/com.nope.apk"))  # type: ignore[arg-type]
    assert ok is False
    assert note == "boom again"
    assert len(fake.installs) == 2


def test_install_retry_succeeds_on_the_second_attempt() -> None:
    fake = FakeAdb()
    fake.script = [(False, "timeout", False), (True, "Success", True)]

    ok, _ = prov.install_apk(fake, "net.osmand", Path("/tmp/net.osmand.apk"))  # type: ignore[arg-type]
    assert ok is True
    assert len(fake.installs) == 2


def test_split_apk_short_circuits_without_a_retry() -> None:
    """A missing-split base fails identically on every attempt; retrying only
    burns the timeout again."""
    fake = FakeAdb()
    fake.script = [(False, "adb: failed to install: INSTALL_FAILED_MISSING_SPLIT", False)]

    ok, note = prov.install_apk(fake, "com.ticktick.task", Path("/tmp/com.ticktick.task.apk"))  # type: ignore[arg-type]
    assert ok is False
    assert "install-multiple" in note
    assert len(fake.installs) == 1


def test_install_reports_an_unverifiable_result_as_failure() -> None:
    """If the link drops during verification the verdict is unknown, not success."""
    fake = FakeAdb()
    fake.list_raises = AdbError("no device is online")

    ok, note = prov.install_apk(fake, "com.x", Path("/tmp/com.x.apk"))  # type: ignore[arg-type]
    assert ok is False
    assert "verification failed" in note


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

STAMP_1 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
STAMP_2 = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


def test_ledger_upsert_keeps_first_seen_and_moves_last_seen() -> None:
    first = prov.update_ledger(
        prov.empty_ledger(),
        failures={"com.a": ("f-droid", "tls")},
        resolved=set(),
        scope_ids={"com.a"},
        now=STAMP_1,
    )
    second = prov.update_ledger(
        first,
        failures={"com.a": ("f-droid", "still tls")},
        resolved=set(),
        scope_ids={"com.a"},
        now=STAMP_2,
    )
    entry = second["entries"]["com.a"]
    assert entry["first_seen"] == "2026-08-27T10:00:00+00:00"
    assert entry["last_seen"] == "2026-08-28T10:00:00+00:00"
    assert entry["reason"] == "still tls"


def test_scoped_run_never_drops_out_of_scope_records() -> None:
    """`--only A` must leave B exactly as it found it — including a B that the
    scoped run happened to see installed."""
    ledger = prov.update_ledger(
        prov.empty_ledger(),
        failures={"com.a": ("f-droid", "tls"), "com.b": ("unresolved", "not in index")},
        resolved=set(),
        scope_ids={"com.a", "com.b"},
        now=STAMP_1,
    )
    scoped = prov.update_ledger(
        ledger,
        failures={"com.a": ("f-droid", "tls again")},
        resolved={"com.b"},  # observed present, but OUT of this run's scope
        scope_ids={"com.a"},
        now=STAMP_2,
    )
    assert set(scoped["entries"]) == {"com.a", "com.b"}
    assert scoped["entries"]["com.b"] == ledger["entries"]["com.b"]


def test_an_in_scope_package_that_is_present_is_dropped() -> None:
    """The ledger means "not held", not "the last attempt failed"."""
    ledger = prov.update_ledger(
        prov.empty_ledger(),
        failures={"com.a": ("f-droid", "tls")},
        resolved=set(),
        scope_ids={"com.a"},
        now=STAMP_1,
    )
    cleared = prov.update_ledger(
        ledger, failures={}, resolved={"com.a"}, scope_ids={"com.a"}, now=STAMP_2
    )
    assert cleared["entries"] == {}


def test_out_of_scope_failures_are_ignored() -> None:
    ledger = prov.update_ledger(
        prov.empty_ledger(),
        failures={"com.stray": ("f-droid", "tls")},
        resolved=set(),
        scope_ids=set(),
        now=STAMP_1,
    )
    assert ledger["entries"] == {}


def test_ledger_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / prov.LEDGER_NAME
    ledger = prov.update_ledger(
        prov.empty_ledger(),
        failures={"com.a": ("f-droid", "tls")},
        resolved=set(),
        scope_ids={"com.a"},
        now=STAMP_1,
    )
    prov.write_ledger(ledger, path)
    assert prov.load_ledger(path) == ledger
    assert not list(tmp_path.glob(".*tmp")), "the atomic tempfile was left behind"


def test_a_missing_ledger_is_a_fresh_ledger(tmp_path: Path) -> None:
    assert prov.load_ledger(tmp_path / "nope.json") == prov.empty_ledger()


def test_a_corrupt_ledger_raises_instead_of_resetting(tmp_path: Path) -> None:
    """A silent reset reproduces exactly the loss the ledger exists to prevent."""
    path = tmp_path / prov.LEDGER_NAME
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(prov.LedgerError, match="not valid JSON"):
        prov.load_ledger(path)


def test_a_broken_entry_is_caught_before_any_install(tmp_path: Path) -> None:
    """Validation reaches into each entry: a shape error found only at write time
    would surface after the installs had already run."""
    path = tmp_path / prov.LEDGER_NAME
    path.write_text(json.dumps({"entries": {"com.a": {"reason": "x"}}}), encoding="utf-8")
    with pytest.raises(prov.LedgerError, match="no string 'source'"):
        prov.load_ledger(path)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_provision_dry_run_installs_nothing_and_leaves_the_ledger_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas_collector import cli

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "org.tasks.apk").write_bytes(b"cached")
    ledger = tmp_path / prov.LEDGER_NAME

    fake = FakeAdb(packages=set())
    monkeypatch.setattr(cli, "_device_packages", lambda args, command: (fake, set()))
    _no_network(monkeypatch)

    code = cli.main(
        [
            "provision",
            "--only",
            "org.tasks",
            "--dry-run",
            "--apk-cache",
            str(cache),
            "--android-world",
            str(tmp_path / "absent"),
            "--ledger",
            str(ledger),
        ]
    )
    assert code == 0
    assert fake.installs == []
    assert not ledger.exists()
