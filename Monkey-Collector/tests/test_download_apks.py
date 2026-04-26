"""Tests for catalog.download_apks — CLI parsing, F-Droid build selection,
partition logic, Play Store subprocess command, and MISSING.md rendering.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MC_ROOT = Path(__file__).resolve().parents[1]
if str(_MC_ROOT) not in sys.path:
    sys.path.insert(0, str(_MC_ROOT))

from catalog.download_apks import (  # noqa: E402
    DownloadError,
    DownloadResult,
    build_gplaydl_command,
    finalize_playstore_output,
    parse_args,
    partition_jobs,
    pick_fdroid_build,
    render_missing_md,
)

from monkey_collector.pipeline.app_catalog import AppJob  # noqa: E402

# ── CLI ────────────────────────────────────────────────────────────────────

def test_parse_args_defaults() -> None:
    ns = parse_args([])
    assert ns.source == "all"
    assert ns.abi == "x86_64"
    assert ns.playstore_arch == "arm64"
    assert ns.force is False
    assert ns.dry_run is False


def test_parse_args_overrides() -> None:
    ns = parse_args(
        ["--source", "fdroid", "--abi", "arm64-v8a", "--only", "a.b,c.d", "--force"]
    )
    assert ns.source == "fdroid"
    assert ns.abi == "arm64-v8a"
    assert ns.only == "a.b,c.d"
    assert ns.force is True


# ── partition_jobs ─────────────────────────────────────────────────────────

def _job(pkg: str, source: str) -> AppJob:
    return AppJob(
        category="X",
        sub_category="Y",
        app_name=pkg,
        package_id=pkg,
        source=source,
        priority="Medium",
    )


def test_partition_jobs_splits_by_source() -> None:
    jobs = [
        _job("a.fdroid", "F-Droid"),
        _job("b.playstore", "PlayStore"),
        _job("c.system", "System"),
    ]
    fdroid, playstore, system = partition_jobs(jobs, {"fdroid", "playstore"}, None)
    assert [j.package_id for j in fdroid] == ["a.fdroid"]
    assert [j.package_id for j in playstore] == ["b.playstore"]
    assert [j.package_id for j in system] == ["c.system"]


def test_partition_jobs_honors_only_allowlist() -> None:
    jobs = [
        _job("keep.me", "F-Droid"),
        _job("skip.me", "F-Droid"),
        _job("also.keep", "PlayStore"),
    ]
    fdroid, playstore, _ = partition_jobs(
        jobs, {"fdroid", "playstore"}, only={"keep.me", "also.keep"}
    )
    assert [j.package_id for j in fdroid] == ["keep.me"]
    assert [j.package_id for j in playstore] == ["also.keep"]


def test_partition_jobs_source_filter_skips_other_source() -> None:
    jobs = [_job("a", "F-Droid"), _job("b", "PlayStore")]
    fdroid, playstore, _ = partition_jobs(jobs, {"fdroid"}, None)
    assert len(fdroid) == 1 and len(playstore) == 0


# ── F-Droid build selection ────────────────────────────────────────────────
# `pick_fdroid_build` now accepts an index-v2.json package entry of shape:
#   {"metadata": {...}, "versions": {<key>: {"manifest": {...}, "file": {...}}}}


def _entry(*versions: dict) -> dict:
    return {"versions": {f"k{i}": v for i, v in enumerate(versions)}}


def _version(version_code: int, nativecode: list[str] | None, file_name: str, sha: str = "") -> dict:
    manifest: dict = {"versionCode": version_code}
    if nativecode is not None:
        manifest["nativecode"] = nativecode
    return {"manifest": manifest, "file": {"name": file_name, "sha256": sha or "a" * 64}}


def test_pick_fdroid_build_prefers_matching_abi() -> None:
    entry = _entry(
        _version(100, ["arm64-v8a"], "/old_100.apk"),
        _version(200, ["x86_64", "arm64-v8a"], "/new_200.apk"),
    )
    build = pick_fdroid_build(entry, "x86_64")
    assert build["file_name"] == "/new_200.apk"
    assert build["versionCode"] == 200


def test_pick_fdroid_build_universal_when_no_nativecode() -> None:
    entry = _entry(_version(42, None, "/any_42.apk"))
    build = pick_fdroid_build(entry, "x86_64")
    assert build["file_name"] == "/any_42.apk"


def test_pick_fdroid_build_treats_empty_nativecode_as_universal() -> None:
    entry = _entry(_version(42, [], "/any_42.apk"))
    build = pick_fdroid_build(entry, "x86_64")
    assert build["file_name"] == "/any_42.apk"


def test_pick_fdroid_build_raises_when_no_matching_abi() -> None:
    entry = _entry(
        _version(1, ["arm64-v8a"], "/a.apk"),
        _version(2, ["armeabi-v7a"], "/b.apk"),
    )
    with pytest.raises(DownloadError) as ei:
        pick_fdroid_build(entry, "x86_64")
    assert "x86_64" in str(ei.value)


def test_pick_fdroid_build_raises_when_empty_entry() -> None:
    with pytest.raises(DownloadError):
        pick_fdroid_build({"versions": {}}, "x86_64")


def test_pick_fdroid_build_returns_highest_version_code() -> None:
    entry = _entry(
        _version(2, None, "/v2.apk"),
        _version(5, None, "/v5.apk"),
        _version(3, None, "/v3.apk"),
    )
    assert pick_fdroid_build(entry, "x86_64")["file_name"] == "/v5.apk"


# ── gplaydl command ────────────────────────────────────────────────────────

def test_build_gplaydl_command_shape(tmp_path: Path) -> None:
    cmd = build_gplaydl_command("com.example", "arm64", tmp_path)
    assert cmd[0] == "gplaydl"
    assert cmd[1] == "download"
    assert cmd[2] == "com.example"
    assert "--output" in cmd and str(tmp_path) in cmd
    assert "--arch" in cmd and "arm64" in cmd
    assert "--no-extras" in cmd


# ── finalize_playstore_output ──────────────────────────────────────────────

def test_finalize_playstore_output_picks_base_over_splits(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "com.example"
    pkg_dir.mkdir()
    base = pkg_dir / "base.apk"
    split = pkg_dir / "split_config.xhdpi.apk"
    base.write_bytes(b"A" * 2048)
    split.write_bytes(b"B" * 128)

    dest = tmp_path / "out.apk"
    result_path, had_splits = finalize_playstore_output(tmp_path, dest)
    assert result_path == dest
    assert had_splits is True
    assert dest.read_bytes() == b"A" * 2048


def test_finalize_playstore_output_no_splits(tmp_path: Path) -> None:
    (tmp_path / "pkg.apk").write_bytes(b"X" * 512)
    dest = tmp_path / "out.apk"
    _, had_splits = finalize_playstore_output(tmp_path, dest)
    assert had_splits is False
    assert dest.exists()


def test_finalize_playstore_output_raises_when_empty(tmp_path: Path) -> None:
    with pytest.raises(DownloadError):
        finalize_playstore_output(tmp_path, tmp_path / "out.apk")


# ── MISSING.md rendering ───────────────────────────────────────────────────

def test_render_missing_md_groups_by_source() -> None:
    results = [
        DownloadResult("a.ok", "F-Droid", "downloaded"),
        DownloadResult("b.bad", "F-Droid", "failed", reason="no x86_64 build"),
        DownloadResult("c.bad", "PlayStore", "failed", reason="gplaydl timeout"),
    ]
    system = [_job("com.android.settings", "System")]

    out = render_missing_md(
        abi="x86_64",
        playstore_arch="arm64",
        total_targets=3,
        downloaded=1,
        skipped=0,
        results=results,
        system_skipped=system,
        generated_at=datetime(2026, 4, 22, 14, 30, tzinfo=timezone.utc),
    )

    assert "# Missing APKs" in out
    assert "Generated: 2026-04-22T14:30:00+00:00" in out
    assert "## F-Droid (1)" in out
    assert "b.bad" in out and "no x86_64 build" in out
    assert "## PlayStore (1)" in out
    assert "c.bad" in out and "gplaydl timeout" in out
    assert "## System (1)" in out
    assert "com.android.settings" in out
    # Successful downloads are NOT listed
    assert "a.ok" not in out


def test_render_missing_md_omits_empty_sections() -> None:
    out = render_missing_md(
        abi="x86_64",
        playstore_arch="arm64",
        total_targets=5,
        downloaded=5,
        skipped=0,
        results=[DownloadResult(f"pkg.{i}", "F-Droid", "downloaded") for i in range(5)],
        system_skipped=[],
    )
    assert "## F-Droid" not in out
    assert "## PlayStore" not in out
    assert "## System" not in out
    assert "Failed: 0" in out


# ── End-to-end F-Droid mock ────────────────────────────────────────────────

def _mk_index(package_id: str, *, sha: str, file_name: str = "/pkg_1.apk") -> dict:
    return {
        "packages": {
            package_id: _entry(_version(1, ["x86_64"], file_name, sha=sha)),
        }
    }


def test_download_fdroid_writes_and_verifies_hash(tmp_path: Path) -> None:
    from catalog.download_apks import download_fdroid

    body = b"fake-apk-contents"
    sha = hashlib.sha256(body).hexdigest()

    apk_resp = MagicMock()
    apk_resp.status_code = 200
    apk_resp.iter_content = lambda chunk_size=65536: [body]
    apk_resp.raise_for_status = MagicMock()

    session = MagicMock()
    session.get.return_value = apk_resp

    dest = tmp_path / "com.example.apk"
    result = download_fdroid(
        session, "com.example", "x86_64", dest, index=_mk_index("com.example", sha=sha)
    )

    assert result.status == "downloaded"
    assert dest.exists() and dest.read_bytes() == body


def test_download_fdroid_records_hash_mismatch(tmp_path: Path) -> None:
    from catalog.download_apks import download_fdroid

    apk_resp = MagicMock()
    apk_resp.status_code = 200
    apk_resp.iter_content = lambda chunk_size=65536: [b"actual"]
    apk_resp.raise_for_status = MagicMock()

    session = MagicMock()
    session.get.return_value = apk_resp

    dest = tmp_path / "com.example.apk"
    result = download_fdroid(
        session,
        "com.example",
        "x86_64",
        dest,
        index=_mk_index("com.example", sha="deadbeef" * 8),
    )

    assert result.status == "failed"
    assert "mismatch" in result.reason
    assert not dest.exists()


def test_download_fdroid_raises_when_package_missing_from_index(tmp_path: Path) -> None:
    from catalog.download_apks import download_fdroid

    session = MagicMock()
    dest = tmp_path / "com.example.apk"
    result = download_fdroid(
        session, "missing.pkg", "x86_64", dest, index={"packages": {}}
    )

    assert result.status == "failed"
    assert "not in F-Droid index" in result.reason
    session.get.assert_not_called()


# ── PlayStore subprocess mock ──────────────────────────────────────────────

def test_download_playstore_invokes_gplaydl(tmp_path: Path) -> None:
    from catalog.download_apks import download_playstore

    def fake_run(cmd, capture_output, text, timeout, check):
        # Simulate gplaydl writing a base APK to the output dir.
        out_dir = Path(cmd[cmd.index("--output") + 1])
        (out_dir / "com.example").mkdir(parents=True, exist_ok=True)
        (out_dir / "com.example" / "base.apk").write_bytes(b"playstore-apk")
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        return proc

    dest = tmp_path / "com.example.apk"
    with patch("catalog.download_apks.subprocess.run", side_effect=fake_run):
        result = download_playstore("com.example", "arm64", dest)

    assert result.status == "downloaded"
    assert dest.read_bytes() == b"playstore-apk"


def test_download_playstore_failure_records_reason(tmp_path: Path) -> None:
    from catalog.download_apks import download_playstore

    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    proc.stderr = "auth token expired"

    with patch("catalog.download_apks.subprocess.run", return_value=proc):
        result = download_playstore("com.example", "arm64", tmp_path / "x.apk")

    assert result.status == "failed"
    assert "gplaydl" in result.reason
