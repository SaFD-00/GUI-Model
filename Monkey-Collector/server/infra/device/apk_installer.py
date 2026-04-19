"""APK resolution and installation helpers."""

from __future__ import annotations

import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from server.infra.device.adb import AdbClient

if TYPE_CHECKING:
    from server.pipeline.app_catalog import AppJob


class InstallResult(str, Enum):
    INSTALLED = "installed"
    ALREADY = "already"
    MISSING_APK = "missing_apk"
    FAILED = "failed"


class ApkResolver:
    """Resolve ``{package_id}.apk`` files under a single directory."""

    def __init__(self, apks_dir: str | Path):
        self.apks_dir = Path(apks_dir)

    def resolve(self, package_id: str) -> Path | None:
        candidate = self.apks_dir / f"{package_id}.apk"
        return candidate if candidate.is_file() else None


class ApkInstaller:
    """Install/uninstall APKs on a single device via adb."""

    def __init__(self, adb: AdbClient, resolver: ApkResolver):
        self.adb = adb
        self.resolver = resolver

    def is_installed(self, package_id: str) -> bool:
        output = self.adb.shell(f"pm list packages {package_id}")
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("package:") and line[len("package:"):] == package_id:
                return True
        return False

    def install(self, job: AppJob, *, force: bool = False) -> InstallResult:
        package_id = job.package_id

        if not force and self.is_installed(package_id):
            logger.debug(f"APK already installed, skipping: {package_id}")
            return InstallResult.ALREADY

        apk_path = self.resolver.resolve(package_id)
        if apk_path is None:
            logger.warning(
                f"APK file not found for {package_id} under "
                f"{self.resolver.apks_dir}, skipping install"
            )
            return InstallResult.MISSING_APK

        output = self.adb.install(str(apk_path))
        if "Success" in output:
            logger.info(f"Installed {package_id} from {apk_path}")
            return InstallResult.INSTALLED

        logger.error(f"Failed to install {package_id}: {output}")
        return InstallResult.FAILED

    def uninstall(self, package_id: str) -> bool:
        adb_bin = shutil.which("adb") or "adb"
        cmd = [adb_bin]
        if self.adb.device_serial:
            cmd += ["-s", self.adb.device_serial]
        cmd += ["uninstall", package_id]

        logger.debug(f"ADB: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = (result.stdout or "").strip()
        if result.returncode == 0 and "Success" in output:
            logger.info(f"Uninstalled {package_id}")
            return True

        logger.warning(
            f"Uninstall failed for {package_id}: "
            f"rc={result.returncode} stdout={output!r} stderr={result.stderr!r}"
        )
        return False
