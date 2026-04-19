"""Tests for server.infra.device.apk_installer — APK resolution and install."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.infra.device.adb import AdbClient
from server.infra.device.apk_installer import (
    ApkInstaller,
    ApkResolver,
    InstallResult,
)


@dataclass
class _FakeJob:
    """Duck-typed stand-in for AppJob exposing only what ApkInstaller needs."""

    package_id: str
    app_name: str = ""


# ── ApkResolver ──

class TestApkResolver:
    def test_resolver_returns_none_for_missing_file(self, tmp_path: Path):
        resolver = ApkResolver(tmp_path)
        assert resolver.resolve("com.missing.app") is None

    def test_resolver_finds_apk(self, tmp_path: Path):
        apk = tmp_path / "com.foo.apk"
        apk.write_bytes(b"fake apk bytes")
        resolver = ApkResolver(tmp_path)
        result = resolver.resolve("com.foo")
        assert result == apk

    def test_resolver_accepts_string_dir(self, tmp_path: Path):
        apk = tmp_path / "com.bar.apk"
        apk.write_bytes(b"")
        resolver = ApkResolver(str(tmp_path))
        assert resolver.resolve("com.bar") == apk


# ── ApkInstaller ──

@pytest.fixture
def mock_adb() -> MagicMock:
    adb = MagicMock(spec=AdbClient)
    adb.device_serial = "emulator-5554"
    adb.shell.return_value = ""
    adb.install.return_value = ""
    return adb


@pytest.fixture
def resolver(tmp_path: Path) -> ApkResolver:
    return ApkResolver(tmp_path)


class TestApkInstallerIsInstalled:
    def test_returns_true_when_package_listed(self, mock_adb, resolver):
        mock_adb.shell.return_value = "package:com.foo"
        installer = ApkInstaller(mock_adb, resolver)
        assert installer.is_installed("com.foo") is True

    def test_returns_false_when_package_not_listed(self, mock_adb, resolver):
        mock_adb.shell.return_value = ""
        installer = ApkInstaller(mock_adb, resolver)
        assert installer.is_installed("com.foo") is False

    def test_exact_match_not_prefix(self, mock_adb, resolver):
        # pm list packages uses a substring filter — require exact match.
        mock_adb.shell.return_value = "package:com.foo.bar"
        installer = ApkInstaller(mock_adb, resolver)
        assert installer.is_installed("com.foo") is False


class TestApkInstallerInstall:
    def test_already_installed_is_skipped(self, mock_adb, resolver):
        mock_adb.shell.return_value = "package:com.foo"
        installer = ApkInstaller(mock_adb, resolver)
        result = installer.install(_FakeJob(package_id="com.foo"))
        assert result == InstallResult.ALREADY
        mock_adb.install.assert_not_called()

    def test_missing_apk_returns_missing(self, mock_adb, tmp_path):
        mock_adb.shell.return_value = ""  # not installed
        resolver = ApkResolver(tmp_path)  # empty dir
        installer = ApkInstaller(mock_adb, resolver)
        result = installer.install(_FakeJob(package_id="com.ghost"))
        assert result == InstallResult.MISSING_APK
        mock_adb.install.assert_not_called()

    def test_calls_adb_install_on_success(self, mock_adb, tmp_path):
        (tmp_path / "com.foo.apk").write_bytes(b"")
        mock_adb.shell.return_value = ""
        mock_adb.install.return_value = "Success\n"
        resolver = ApkResolver(tmp_path)
        installer = ApkInstaller(mock_adb, resolver)

        result = installer.install(_FakeJob(package_id="com.foo"))

        assert result == InstallResult.INSTALLED
        mock_adb.install.assert_called_once()
        call_arg = mock_adb.install.call_args[0][0]
        assert call_arg == str(tmp_path / "com.foo.apk")

    def test_failed_install_returns_failed(self, mock_adb, tmp_path):
        (tmp_path / "com.foo.apk").write_bytes(b"")
        mock_adb.shell.return_value = ""
        mock_adb.install.return_value = "Failure [INSTALL_FAILED_OLDER_SDK]"
        resolver = ApkResolver(tmp_path)
        installer = ApkInstaller(mock_adb, resolver)

        result = installer.install(_FakeJob(package_id="com.foo"))
        assert result == InstallResult.FAILED

    def test_force_reinstalls_even_when_present(self, mock_adb, tmp_path):
        (tmp_path / "com.foo.apk").write_bytes(b"")
        mock_adb.shell.return_value = "package:com.foo"  # already installed
        mock_adb.install.return_value = "Success"
        resolver = ApkResolver(tmp_path)
        installer = ApkInstaller(mock_adb, resolver)

        result = installer.install(_FakeJob(package_id="com.foo"), force=True)
        assert result == InstallResult.INSTALLED
        mock_adb.install.assert_called_once()


class TestApkInstallerUninstall:
    def test_uninstall_calls_adb(self, mock_adb, resolver):
        installer = ApkInstaller(mock_adb, resolver)
        with patch(
            "server.infra.device.apk_installer.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Success", stderr=""
            )
            ok = installer.uninstall("com.foo")

        assert ok is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "uninstall" in args
        assert "com.foo" in args
        assert "-s" in args
        assert "emulator-5554" in args

    def test_uninstall_returns_false_on_failure(self, mock_adb, resolver):
        installer = ApkInstaller(mock_adb, resolver)
        with patch(
            "server.infra.device.apk_installer.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Failure", stderr=""
            )
            ok = installer.uninstall("com.foo")
        assert ok is False

    def test_uninstall_without_serial(self, resolver):
        adb = MagicMock(spec=AdbClient)
        adb.device_serial = None
        installer = ApkInstaller(adb, resolver)
        with patch(
            "server.infra.device.apk_installer.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Success", stderr=""
            )
            installer.uninstall("com.foo")
        args = mock_run.call_args[0][0]
        assert "-s" not in args
