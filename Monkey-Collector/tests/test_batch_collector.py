"""Tests for BatchCollector: worker pool + sticky AVD assignment."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.infra.device.apk_installer import ApkInstaller, InstallResult
from server.infra.device.avd import AvdHandle
from server.pipeline.app_catalog import AppCatalog, AppJob
from server.pipeline.batch_collector import BatchCollector, JobResult


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
    pool = MagicMock()
    pool.start_all.return_value = [_handle("avd-1", "emulator-5554", 6000)]
    return pool


def test_run_processes_all_jobs_with_single_worker(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(
        _job("Shopping", "com.a"),
        _job("Shopping", "com.b"),
        _job("Media", "com.c"),
    )
    collector = _SuccessfulCollector()
    collector_factory = MagicMock(return_value=collector)

    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=collector_factory,
        output_dir=tmp_path,
        parallel=1,
    )

    results = batch.run()

    assert len(results) == 3
    assert collector.calls == ["com.a", "com.b", "com.c"]
    assert mock_pool.start_all.called
    assert mock_pool.stop_all.called


def test_skip_jobs_with_missing_apk(tmp_path, mock_pool):
    catalog = _catalog(_job("Shopping", "com.missing"))
    installer = MagicMock(spec=ApkInstaller)
    installer.install.return_value = InstallResult.MISSING_APK

    collector_factory = MagicMock()
    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: installer,
        collector_factory=collector_factory,
        output_dir=tmp_path,
        parallel=1,
    )

    results = batch.run()

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
    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: installer,
        collector_factory=collector_factory,
        output_dir=tmp_path,
        parallel=1,
    )

    results = batch.run()

    assert results[0].install_result is InstallResult.FAILED
    assert results[0].skipped is True
    collector_factory.assert_not_called()


def test_parallel_workers_use_sticky_avd(tmp_path, mock_installer):
    import time

    pool = MagicMock()
    pool.start_all.return_value = [
        _handle("avd-1", "emulator-5554", 6000),
        _handle("avd-2", "emulator-5556", 6001),
    ]
    jobs = [_job("X", f"com.app{i}") for i in range(4)]
    catalog = _catalog(*jobs)

    class SlowCollector:
        def run(self, package: str) -> str:
            # Sleep so the second worker actually gets a chance to claim a job
            # before the first worker empties the queue.
            time.sleep(0.05)
            return package

    batch = BatchCollector(
        catalog=catalog,
        avd_pool=pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=lambda handle, job, base_dir: SlowCollector(),
        output_dir=tmp_path,
        parallel=2,
    )

    results = batch.run()

    assert len(results) == 4
    assigned = {r.avd_name for r in results}
    assert assigned == {"avd-1", "avd-2"}
    per_avd = {"avd-1": 0, "avd-2": 0}
    for r in results:
        per_avd[r.avd_name] += 1
    assert all(v >= 1 for v in per_avd.values())


def test_dry_run_skips_avd_start(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"), _job("Shopping", "com.b"))
    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=MagicMock(),
        output_dir=tmp_path,
        parallel=1,
    )

    results = batch.run(dry_run=True)

    assert results == []
    mock_pool.start_all.assert_not_called()
    mock_pool.stop_all.assert_not_called()


def test_uninstall_called_when_flag_true(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))
    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=lambda h, j, b: _SuccessfulCollector(),
        output_dir=tmp_path,
        parallel=1,
        uninstall_after=True,
    )
    batch.run()
    mock_installer.uninstall.assert_called_once_with("com.a")


def test_uninstall_not_called_by_default(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))
    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=lambda h, j, b: _SuccessfulCollector(),
        output_dir=tmp_path,
        parallel=1,
    )
    batch.run()
    mock_installer.uninstall.assert_not_called()


def test_avd_pool_stopped_on_exception(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))

    def bad_factory(handle, job, base_dir):
        raise RuntimeError("collector blew up")

    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=bad_factory,
        output_dir=tmp_path,
        parallel=1,
    )
    results = batch.run()

    # error recorded on the JobResult, not raised
    assert len(results) == 1
    assert results[0].error is not None
    assert "collector blew up" in results[0].error
    assert results[0].session_id is None
    mock_pool.stop_all.assert_called_once()


def test_output_dir_includes_category(tmp_path, mock_pool, mock_installer):
    catalog = _catalog(_job("Shopping", "com.a"))
    received: dict = {}

    def capturing_factory(handle, job, base_dir):
        received["base_dir"] = base_dir
        return _SuccessfulCollector()

    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=capturing_factory,
        output_dir=tmp_path,
        parallel=1,
    )
    batch.run()

    assert Path(received["base_dir"]) == Path(tmp_path) / "Shopping"


def test_provision_called_once_per_worker(tmp_path, mock_installer):
    pool = MagicMock()
    handles = [
        _handle("avd-1", "emulator-5554", 6000),
        _handle("avd-2", "emulator-5556", 6001),
    ]
    pool.start_all.return_value = handles
    catalog = _catalog(*[_job("X", f"com.{i}") for i in range(6)])

    batch = BatchCollector(
        catalog=catalog,
        avd_pool=pool,
        installer_factory=lambda h: mock_installer,
        collector_factory=lambda h, j, b: _SuccessfulCollector(),
        output_dir=tmp_path,
        parallel=2,
    )
    batch.run()

    # provision called once per used handle, regardless of job count
    assert pool.provision.call_count == 2


def test_filter_forwarded_to_catalog(tmp_path, mock_pool, mock_installer):
    jobs = [
        _job("Shopping", "com.a", priority="High"),
        _job("Shopping", "com.b", priority="Low"),
        _job("Media", "com.c", priority="High"),
    ]
    catalog = _catalog(*jobs)
    collector = _SuccessfulCollector()

    batch = BatchCollector(
        catalog=catalog,
        avd_pool=mock_pool,
        installer_factory=lambda handle: mock_installer,
        collector_factory=lambda h, j, b: collector,
        output_dir=tmp_path,
        parallel=1,
    )

    results = batch.run(categories=["Shopping"], priorities=["High"])

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
