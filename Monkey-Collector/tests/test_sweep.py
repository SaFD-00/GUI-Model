"""Tests for Sweep: sequential per-AVD iteration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.infra.device.apk_installer import ApkInstaller, InstallResult
from server.infra.device.avd import AvdHandle
from server.pipeline.app_catalog import AppCatalog, AppJob
from server.pipeline.sweep import JobResult, Sweep


def _job(category: str, package_id: str, priority: str = "High") -> AppJob:
    return AppJob(
        category=category,
        sub_category="",
        app_name=package_id,
        package_id=package_id,
        source="PlayStore",
        priority=priority,
    )


def _catalog(*jobs: AppJob) -> AppCatalog:
    return AppCatalog(list(jobs))


def _handle(name: str, serial: str, port: int) -> AvdHandle:
    return AvdHandle(name=name, serial=serial, host_port=port)


def _make_pool(avd_names: list[str]) -> MagicMock:
    pool = MagicMock()
    pool.avd_names = list(avd_names)

    def _start_one(name: str, *, index: int) -> AvdHandle:
        return _handle(name, f"emulator-{5554 + 2 * index}", 6000 + index)

    pool.start_one.side_effect = _start_one
    return pool


class _SuccessfulCollector:
    """Stand-in collector whose run() returns a session id based on the package."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, package: str) -> str:
        self.calls.append(package)
        return package


@pytest.fixture
def mock_installer():
    inst = MagicMock(spec=ApkInstaller)
    inst.install.return_value = InstallResult.INSTALLED
    inst.uninstall.return_value = True
    return inst


@pytest.fixture
def mock_pool():
    return _make_pool(["avd-1"])


def test_run_processes_all_jobs_on_single_avd(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(
        _job("Shopping", "com.a"),
        _job("Shopping", "com.b"),
        _job("Media", "com.c"),
    )
    collector = _SuccessfulCollector()
    collector_factory = MagicMock(return_value=collector)

    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=collector_factory,
        output_dir=tmp_path,
    )

    results = sweep.run()

    assert len(results) == 3
    assert collector.calls == ["com.a", "com.b", "com.c"]
    mock_pool.start_one.assert_called_once_with("avd-1", index=0)
    mock_pool.stop.assert_called_once()


def test_skip_jobs_with_missing_apk(tmp_path, mock_pool):
    catalog = _catalog(_job("Shopping", "com.missing"))
    installer = MagicMock(spec=ApkInstaller)
    installer.install.return_value = InstallResult.MISSING_APK

    collector_factory = MagicMock()
    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: installer,
        collector_factory=collector_factory,
        output_dir=tmp_path,
    )

    results = sweep.run()

    assert len(results) == 1
    assert results[0].install_result is InstallResult.MISSING_APK
    assert results[0].session_id is None
    assert results[0].skipped is True
    collector_factory.assert_not_called()


def test_skip_jobs_with_failed_install(tmp_path, mock_pool):
    catalog = _catalog(_job("Shopping", "com.broken"))
    installer = MagicMock(spec=ApkInstaller)
    installer.install.return_value = InstallResult.FAILED

    collector_factory = MagicMock()
    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: installer,
        collector_factory=collector_factory,
        output_dir=tmp_path,
    )

    results = sweep.run()

    assert results[0].install_result is InstallResult.FAILED
    assert results[0].skipped is True
    collector_factory.assert_not_called()


def test_multiple_avds_round_robin_assign_jobs(tmp_path, mock_installer):
    pool = _make_pool(["avd-1", "avd-2"])
    jobs = [_job("X", f"com.app{i}") for i in range(4)]
    catalog = _catalog(*jobs)

    calls_per_avd: dict[str, list[str]] = {}

    def factory(handle: AvdHandle, job: AppJob, base_dir: Path) -> _SuccessfulCollector:
        calls_per_avd.setdefault(handle.name, []).append(job.package_id)
        return _SuccessfulCollector()

    sweep = Sweep(
        catalog=catalog,
        avd_pool=pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=factory,
        output_dir=tmp_path,
    )

    results = sweep.run()

    assert len(results) == 4
    # Round-robin: avd-1 gets indices 0,2; avd-2 gets 1,3
    assert calls_per_avd == {
        "avd-1": ["com.app0", "com.app2"],
        "avd-2": ["com.app1", "com.app3"],
    }
    # AVDs are visited one at a time (sequential)
    assert pool.start_one.call_count == 2
    assert pool.stop.call_count == 2


def test_dry_run_skips_avd_start(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"), _job("Shopping", "com.b"))
    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=MagicMock(),
        output_dir=tmp_path,
    )

    results = sweep.run(dry_run=True)

    assert results == []
    mock_pool.start_one.assert_not_called()
    mock_pool.stop.assert_not_called()


def test_uninstall_called_when_flag_true(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))
    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=lambda h, j, b: _SuccessfulCollector(),
        output_dir=tmp_path,
        uninstall_after=True,
    )
    sweep.run()
    mock_installer.uninstall.assert_called_once_with("com.a")


def test_uninstall_not_called_by_default(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))
    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=lambda h, j, b: _SuccessfulCollector(),
        output_dir=tmp_path,
    )
    sweep.run()
    mock_installer.uninstall.assert_not_called()


def test_avd_stopped_on_exception(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))

    def bad_factory(handle, job, base_dir):
        raise RuntimeError("collector blew up")

    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=bad_factory,
        output_dir=tmp_path,
    )
    results = sweep.run()

    # error recorded on the JobResult, not raised
    assert len(results) == 1
    assert results[0].error is not None
    assert "collector blew up" in results[0].error
    assert results[0].session_id is None
    mock_pool.stop.assert_called_once()


def test_output_dir_includes_category(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))
    received: dict = {}

    def capturing_factory(handle, job, base_dir):
        received["base_dir"] = base_dir
        return _SuccessfulCollector()

    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=capturing_factory,
        output_dir=tmp_path,
    )
    sweep.run()

    assert Path(received["base_dir"]) == Path(tmp_path) / "Shopping"


def test_provision_called_once_per_avd(tmp_path, mock_installer):
    pool = _make_pool(["avd-1", "avd-2"])
    catalog = _catalog(*[_job("X", f"com.{i}") for i in range(6)])

    sweep = Sweep(
        catalog=catalog,
        avd_pool=pool,
        installer_factory=lambda h: mock_installer,
        collector_factory=lambda h, j, b: _SuccessfulCollector(),
        output_dir=tmp_path,
    )
    sweep.run()

    # provision called once per used AVD, regardless of job count
    assert pool.provision.call_count == 2


def test_excess_avds_are_skipped(tmp_path, mock_installer):
    """If there are more AVDs than jobs, only the first few AVDs are booted."""
    pool = _make_pool(["avd-1", "avd-2", "avd-3"])
    catalog = _catalog(_job("X", "com.only"))

    sweep = Sweep(
        catalog=catalog,
        avd_pool=pool,
        installer_factory=lambda h: mock_installer,
        collector_factory=lambda h, j, b: _SuccessfulCollector(),
        output_dir=tmp_path,
    )
    sweep.run()

    assert pool.start_one.call_count == 1
    assert pool.stop.call_count == 1


def test_filter_forwarded_to_catalog(tmp_path, mock_pool, mock_installer):
    jobs = [
        _job("Shopping", "com.a", priority="High"),
        _job("Shopping", "com.b", priority="Low"),
        _job("Media", "com.c", priority="High"),
    ]
    catalog = _catalog(*jobs)
    collector = _SuccessfulCollector()

    sweep = Sweep(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=lambda h, j, b: collector,
        output_dir=tmp_path,
    )

    results = sweep.run(categories=["Shopping"], priorities=["High"])

    assert len(results) == 1
    assert results[0].job.package_id == "com.a"


def test_succeeded_property(tmp_path):
    ok = JobResult(
        job=_job("X", "com.ok"),
        avd_name="avd-1",
        install_result=InstallResult.INSTALLED,
        session_id="com.ok",
    )
    failed = JobResult(
        job=_job("X", "com.fail"),
        avd_name="avd-1",
        install_result=InstallResult.INSTALLED,
        error="boom",
    )
    skipped = JobResult(
        job=_job("X", "com.miss"),
        avd_name="avd-1",
        install_result=InstallResult.MISSING_APK,
    )

    assert ok.succeeded is True
    assert failed.succeeded is False
    assert skipped.succeeded is False
    assert skipped.skipped is True
    assert ok.skipped is False


def _seed_session(root: Path, category: str, package: str, metadata: dict) -> None:
    import json

    session_dir = root / category / package
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(json.dumps(metadata))


class TestResumeSkip:
    def test_skip_completed_app(self, tmp_path, mock_pool, mock_installer):
        _seed_session(
            tmp_path, "Shopping", "com.done",
            {"completed_at": "2026-01-01T00:00:00Z"},
        )
        catalog = _catalog(_job("Shopping", "com.done"), _job("Shopping", "com.todo"))
        called: list[str] = []

        def factory(handle, job, base_dir):
            called.append(job.package_id)
            return _SuccessfulCollector()

        sweep = Sweep(
            catalog=catalog,
            avd_pool=mock_pool,
            installer_factory=lambda h: mock_installer,
            collector_factory=factory,
            output_dir=tmp_path,
        )
        results = sweep.run()

        assert called == ["com.todo"]
        skipped = [r for r in results if r.skip_reason == "already_complete"]
        assert len(skipped) == 1
        assert skipped[0].job.package_id == "com.done"
        assert skipped[0].skipped is True

    def test_force_flag_bypasses_resume(self, tmp_path, mock_pool, mock_installer):
        _seed_session(
            tmp_path, "Shopping", "com.done",
            {"completed_at": "2026-01-01T00:00:00Z"},
        )
        catalog = _catalog(_job("Shopping", "com.done"))
        called: list[str] = []

        def factory(handle, job, base_dir):
            called.append(job.package_id)
            return _SuccessfulCollector()

        sweep = Sweep(
            catalog=catalog,
            avd_pool=mock_pool,
            installer_factory=lambda h: mock_installer,
            collector_factory=factory,
            output_dir=tmp_path,
        )
        results = sweep.run(force=True)

        assert called == ["com.done"]
        assert all(r.skip_reason is None for r in results)

    def test_incomplete_session_not_skipped(
        self, tmp_path, mock_pool, mock_installer,
    ):
        _seed_session(
            tmp_path, "Shopping", "com.partial",
            {"completed_at": None, "total_steps": 5},
        )
        catalog = _catalog(_job("Shopping", "com.partial"))
        called: list[str] = []

        def factory(handle, job, base_dir):
            called.append(job.package_id)
            return _SuccessfulCollector()

        sweep = Sweep(
            catalog=catalog,
            avd_pool=mock_pool,
            installer_factory=lambda h: mock_installer,
            collector_factory=factory,
            output_dir=tmp_path,
        )
        sweep.run()

        assert called == ["com.partial"]

    def test_no_metadata_treated_as_incomplete(
        self, tmp_path, mock_pool, mock_installer,
    ):
        catalog = _catalog(_job("Shopping", "com.fresh"))
        called: list[str] = []

        def factory(handle, job, base_dir):
            called.append(job.package_id)
            return _SuccessfulCollector()

        sweep = Sweep(
            catalog=catalog,
            avd_pool=mock_pool,
            installer_factory=lambda h: mock_installer,
            collector_factory=factory,
            output_dir=tmp_path,
        )
        sweep.run()

        assert called == ["com.fresh"]
