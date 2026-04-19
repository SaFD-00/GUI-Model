"""Batch collector: schedule AppJobs across a pool of AVDs in parallel."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Protocol

from loguru import logger

from server.infra.device.apk_installer import ApkInstaller, InstallResult
from server.infra.device.avd import AvdHandle, AvdPool
from server.pipeline.app_catalog import AppCatalog, AppJob


class _CollectorLike(Protocol):
    def run(self, package: str | None = ...) -> str: ...


InstallerFactory = Callable[[AvdHandle], ApkInstaller]
CollectorFactory = Callable[[AvdHandle, AppJob, Path], _CollectorLike]


@dataclass
class JobResult:
    job: AppJob
    avd_name: str
    install_result: InstallResult
    session_id: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.session_id is not None and self.error is None

    @property
    def skipped(self) -> bool:
        return self.install_result in (InstallResult.MISSING_APK, InstallResult.FAILED)


class BatchCollector:
    """Run collection across a worker pool, one AVD per worker (sticky)."""

    def __init__(
        self,
        catalog: AppCatalog,
        avd_pool: AvdPool | Any,
        installer_factory: InstallerFactory,
        collector_factory: CollectorFactory,
        output_dir: str | Path,
        parallel: int = 2,
        uninstall_after: bool = False,
    ) -> None:
        self.catalog = catalog
        self.avd_pool = avd_pool
        self.installer_factory = installer_factory
        self.collector_factory = collector_factory
        self.output_dir = Path(output_dir)
        self.parallel = max(1, parallel)
        self.uninstall_after = uninstall_after

    def run(
        self,
        categories: list[str] | None = None,
        priorities: list[str] | None = None,
        dry_run: bool = False,
    ) -> list[JobResult]:
        jobs = self.catalog.filter(categories=categories, priorities=priorities)
        logger.info(f"BatchCollector: {len(jobs)} job(s) after filter")

        if dry_run:
            for job in jobs:
                logger.info(
                    f"[dry-run] {job.category}/{job.priority}: {job.package_id} "
                    f"({job.app_name})"
                )
            return []

        if not jobs:
            logger.warning("No jobs matched the filter; skipping AVD startup.")
            return []

        handles = self.avd_pool.start_all()
        if not handles:
            raise RuntimeError("AvdPool.start_all() returned no handles")

        num_workers = min(self.parallel, len(handles), len(jobs))
        active_handles = handles[:num_workers]
        logger.info(
            f"BatchCollector: {len(jobs)} job(s) across {num_workers} worker(s) "
            f"on AVDs {[h.name for h in active_handles]}"
        )

        queue: Queue[AppJob] = Queue()
        for j in jobs:
            queue.put(j)

        results: list[JobResult] = []
        try:
            with ThreadPoolExecutor(max_workers=num_workers) as pool:
                futures = [
                    pool.submit(self._worker, handle, queue)
                    for handle in active_handles
                ]
                for fut in as_completed(futures):
                    results.extend(fut.result())
        finally:
            self.avd_pool.stop_all()

        succeeded = sum(1 for r in results if r.succeeded)
        skipped = sum(1 for r in results if r.skipped)
        failed = len(results) - succeeded - skipped
        logger.info(
            f"BatchCollector done: {succeeded} succeeded, "
            f"{skipped} skipped, {failed} failed"
        )
        return results

    def _worker(self, handle: AvdHandle, queue: Queue[AppJob]) -> list[JobResult]:
        try:
            self.avd_pool.provision(handle)
        except Exception as exc:
            logger.warning(f"provision failed for {handle.name}: {exc}")

        installer = self.installer_factory(handle)
        local: list[JobResult] = []
        while True:
            try:
                job = queue.get_nowait()
            except Empty:
                return local
            local.append(self._run_one(handle, installer, job))

    def _run_one(
        self, handle: AvdHandle, installer: ApkInstaller, job: AppJob
    ) -> JobResult:
        install = installer.install(job)
        if install in (InstallResult.MISSING_APK, InstallResult.FAILED):
            logger.info(f"[{handle.name}] skip {job.package_id}: {install.value}")
            return JobResult(
                job=job, avd_name=handle.name, install_result=install
            )

        base_dir = self.output_dir / job.category
        session_id: str | None = None
        error: str | None = None
        try:
            collector = self.collector_factory(handle, job, base_dir)
            session_id = collector.run(job.package_id)
        except Exception as exc:
            logger.exception(f"[{handle.name}] collection failed for {job.package_id}")
            error = str(exc)
        finally:
            if self.uninstall_after:
                try:
                    installer.uninstall(job.package_id)
                except Exception as exc:
                    logger.warning(
                        f"[{handle.name}] uninstall failed for {job.package_id}: {exc}"
                    )

        return JobResult(
            job=job,
            avd_name=handle.name,
            install_result=install,
            session_id=session_id,
            error=error,
        )


__all__ = [
    "BatchCollector",
    "CollectorFactory",
    "InstallerFactory",
    "JobResult",
]
