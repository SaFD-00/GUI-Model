"""Sweep: run AppJobs across AVDs sequentially (one AVD at a time)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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
    install_result: InstallResult | None
    session_id: str | None = None
    error: str | None = None
    skip_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.session_id is not None and self.error is None

    @property
    def skipped(self) -> bool:
        if self.skip_reason is not None:
            return True
        return self.install_result in (InstallResult.MISSING_APK, InstallResult.FAILED)


def _is_complete(output_dir: Path, job: AppJob) -> bool:
    meta = output_dir / job.category / job.package_id / "metadata.json"
    if not meta.exists():
        return False
    try:
        return bool(json.loads(meta.read_text()).get("completed_at"))
    except (OSError, json.JSONDecodeError):
        return False


class Sweep:
    """Iterate over AVDs one at a time; each AVD drains its share of jobs in order."""

    def __init__(
        self,
        catalog: AppCatalog,
        avd_pool: AvdPool | Any,
        installer_factory: InstallerFactory,
        collector_factory: CollectorFactory,
        output_dir: str | Path,
        uninstall_after: bool = False,
    ) -> None:
        self.catalog = catalog
        self.avd_pool = avd_pool
        self.installer_factory = installer_factory
        self.collector_factory = collector_factory
        self.output_dir = Path(output_dir)
        self.uninstall_after = uninstall_after

    def run(
        self,
        categories: list[str] | None = None,
        priorities: list[str] | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> list[JobResult]:
        jobs = self.catalog.filter(categories=categories, priorities=priorities)
        logger.info(f"Sweep: {len(jobs)} job(s) after filter")

        results: list[JobResult] = []
        if not force:
            pending: list[AppJob] = []
            for job in jobs:
                if _is_complete(self.output_dir, job):
                    results.append(JobResult(
                        job=job,
                        avd_name="",
                        install_result=None,
                        skip_reason="already_complete",
                    ))
                else:
                    pending.append(job)
            if len(pending) < len(jobs):
                logger.info(
                    f"Sweep: skipping {len(jobs) - len(pending)} already-complete "
                    f"app(s) (use --force to redo)"
                )
            jobs = pending

        if dry_run:
            for job in jobs:
                logger.info(
                    f"[dry-run] {job.category}/{job.priority}: {job.package_id} "
                    f"({job.app_name})"
                )
            return []

        if not jobs:
            logger.warning("No jobs to run; skipping AVD startup.")
            return results

        avd_names = list(self.avd_pool.avd_names)
        if not avd_names:
            raise RuntimeError("AvdPool has no AVD names configured")

        # Round-robin job distribution across AVDs; each AVD handles its slice serially.
        num_avds = min(len(avd_names), len(jobs))
        assigned: list[list[AppJob]] = [[] for _ in range(num_avds)]
        for i, job in enumerate(jobs):
            assigned[i % num_avds].append(job)

        logger.info(
            f"Sweep: {len(jobs)} job(s) across {num_avds} AVD(s) "
            f"{avd_names[:num_avds]} (sequential)"
        )

        for idx in range(num_avds):
            name = avd_names[idx]
            slice_jobs = assigned[idx]
            handle = self.avd_pool.start_one(name, index=idx)
            try:
                try:
                    self.avd_pool.provision(handle)
                except Exception as exc:
                    logger.warning(f"provision failed for {handle.name}: {exc}")

                installer = self.installer_factory(handle)
                for job in slice_jobs:
                    results.append(self._run_one(handle, installer, job))
            finally:
                self.avd_pool.stop(handle)

        succeeded = sum(1 for r in results if r.succeeded)
        skipped = sum(1 for r in results if r.skipped)
        failed = len(results) - succeeded - skipped
        logger.info(
            f"Sweep done: {succeeded} succeeded, "
            f"{skipped} skipped, {failed} failed"
        )
        return results

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
    "CollectorFactory",
    "InstallerFactory",
    "JobResult",
    "Sweep",
]
