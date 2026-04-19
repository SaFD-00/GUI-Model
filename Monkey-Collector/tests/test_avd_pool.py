"""Tests for server.infra.device.avd — AvdPool lifecycle and provisioning."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from server.infra.device import avd as avd_mod
from server.infra.device.avd import AvdHandle, AvdPool


@pytest.fixture(autouse=True)
def _stub_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests hermetic by pointing binary lookups at fake paths."""
    monkeypatch.setattr(avd_mod, "_find_android", lambda: "/fake/android")
    monkeypatch.setattr(avd_mod, "_find_adb", lambda: "/fake/adb")
    monkeypatch.setattr(avd_mod, "_find_emulator", lambda: "/fake/emulator")


class _RunRecorder:
    """Callable replacement for avd._run that records invocations."""

    def __init__(self, responder: Callable[[list[str]], str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._responder = responder or (lambda _cmd: "")

    def __call__(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        timeout: float | None = None,
    ) -> str:
        self.calls.append(list(cmd))
        return self._responder(cmd)


class _FakePopen:
    """Minimal stand-in for subprocess.Popen used by start_all."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int | None:
        return None


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> list[_FakePopen]:
    created: list[_FakePopen] = []

    def _factory(*args: Any, **kwargs: Any) -> _FakePopen:
        proc = _FakePopen(*args, **kwargs)
        created.append(proc)
        return proc

    monkeypatch.setattr(avd_mod.subprocess, "Popen", _factory)
    return created


@pytest.fixture
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid real sleeping inside the boot polling loop."""
    monkeypatch.setattr(avd_mod.time, "sleep", lambda _s: None)


def _boot_responder(boot_attempts: dict[str, int], threshold: int = 2) -> Callable[[list[str]], str]:
    """Return a responder where sys.boot_completed flips to "1" after N attempts per serial."""

    def _respond(cmd: list[str]) -> str:
        if "sys.boot_completed" in " ".join(cmd):
            serial = _extract_serial(cmd)
            boot_attempts[serial] = boot_attempts.get(serial, 0) + 1
            return "1" if boot_attempts[serial] > threshold else ""
        if "adb" in cmd[0] and "devices" in cmd:
            return "List of devices attached\nemulator-5554\tdevice\nemulator-5556\tdevice\n"
        if cmd[-2:] == ["emu", "avd", "name"] or cmd[-3:-1] == ["emu", "avd"]:
            # fallback path: return name matching avd
            return "monkey-0\nOK\n"
        return ""

    return _respond


def _extract_serial(cmd: list[str]) -> str:
    if "-s" in cmd:
        idx = cmd.index("-s")
        return cmd[idx + 1]
    return ""


# ---------------------------------------------------------------------------
# start_all / boot waiting
# ---------------------------------------------------------------------------


class TestStartAll:
    def test_start_waits_for_boot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_popen: list[_FakePopen],
        fast_sleep: None,
    ) -> None:
        """boot_completed returns "" twice then "1" → start_all succeeds."""
        attempts: dict[str, int] = {}

        def responder(cmd: list[str]) -> str:
            joined = " ".join(cmd)
            if "sys.boot_completed" in joined:
                serial = _extract_serial(cmd)
                attempts[serial] = attempts.get(serial, 0) + 1
                return "1" if attempts[serial] >= 3 else ""
            if "devices" in cmd:
                return "List of devices attached\nemulator-5554\tdevice\n"
            return ""

        recorder = _RunRecorder(responder)
        monkeypatch.setattr(avd_mod, "_run", recorder)

        pool = AvdPool(["monkey-0"], host_port_base=6000, boot_timeout=10.0)
        handles = pool.start_all()

        assert len(handles) == 1
        assert handles[0].serial == "emulator-5554"
        assert handles[0].console_port == 5554
        assert handles[0].name == "monkey-0"
        assert handles[0].host_port == 6000
        assert attempts["emulator-5554"] >= 3

    def test_start_timeout_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_popen: list[_FakePopen],
        fast_sleep: None,
    ) -> None:
        """boot never completes → TimeoutError or RuntimeError."""
        recorder = _RunRecorder(lambda _cmd: "")
        monkeypatch.setattr(avd_mod, "_run", recorder)
        monkeypatch.setattr(
            avd_mod, "_discover_emulator_serial", lambda _name, _adb=None: "emulator-5554"
        )

        pool = AvdPool(["monkey-0"], boot_timeout=0.5)
        with pytest.raises((TimeoutError, RuntimeError)):
            pool.start_all()

    def test_headless_adds_no_window_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_popen: list[_FakePopen],
        fast_sleep: None,
    ) -> None:
        recorder = _RunRecorder(lambda cmd: "1" if "sys.boot_completed" in " ".join(cmd) else "")
        monkeypatch.setattr(avd_mod, "_run", recorder)

        pool = AvdPool(["monkey-0"], boot_timeout=5.0, headless=True)
        pool.start_all()

        emu_cmd = list(fake_popen[0].args[0])
        assert "-no-window" in emu_cmd
        assert "-no-audio" in emu_cmd
        assert "-no-boot-anim" in emu_cmd
        assert "-avd" in emu_cmd and "monkey-0" in emu_cmd
        assert "-port" in emu_cmd and "5554" in emu_cmd

    def test_headless_disabled_omits_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_popen: list[_FakePopen],
        fast_sleep: None,
    ) -> None:
        recorder = _RunRecorder(lambda cmd: "1" if "sys.boot_completed" in " ".join(cmd) else "")
        monkeypatch.setattr(avd_mod, "_run", recorder)

        pool = AvdPool(["monkey-0"], boot_timeout=5.0)  # default: headless=False
        pool.start_all()

        emu_cmd = list(fake_popen[0].args[0])
        assert "-no-window" not in emu_cmd
        assert "-no-audio" not in emu_cmd
        assert "-no-boot-anim" not in emu_cmd

    def test_host_port_assignment(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_popen: list[_FakePopen],
        fast_sleep: None,
    ) -> None:
        """Two AVDs → host_port = 6000, 6001."""
        serials = iter(["emulator-5554", "emulator-5556"])

        def responder(cmd: list[str]) -> str:
            if "sys.boot_completed" in " ".join(cmd):
                return "1"
            return ""

        recorder = _RunRecorder(responder)
        monkeypatch.setattr(avd_mod, "_run", recorder)
        monkeypatch.setattr(
            avd_mod, "_discover_emulator_serial", lambda _name, _adb=None: next(serials)
        )

        pool = AvdPool(["monkey-0", "monkey-1"], host_port_base=6000, boot_timeout=5.0)
        handles = pool.start_all()

        assert [h.host_port for h in handles] == [6000, 6001]
        assert [h.serial for h in handles] == ["emulator-5554", "emulator-5556"]


# ---------------------------------------------------------------------------
# stop_all
# ---------------------------------------------------------------------------


class TestStopAll:
    def test_stop_all_calls_emu_kill(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_popen: list[_FakePopen],
        fast_sleep: None,
    ) -> None:
        def responder(cmd: list[str]) -> str:
            if "sys.boot_completed" in " ".join(cmd):
                return "1"
            return ""

        recorder = _RunRecorder(responder)
        monkeypatch.setattr(avd_mod, "_run", recorder)
        monkeypatch.setattr(
            avd_mod, "_discover_emulator_serial", lambda _name, _adb=None: "emulator-5554"
        )

        pool = AvdPool(["monkey-0"], boot_timeout=5.0)
        pool.start_all()
        recorder.calls.clear()

        pool.stop_all()

        joined = [" ".join(c) for c in recorder.calls]
        assert any("emu kill" in s for s in joined), f"expected emu kill, got {joined}"

    def test_stop_all_is_idempotent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calling stop_all on an empty pool is a no-op."""
        monkeypatch.setattr(avd_mod, "_run", _RunRecorder())
        pool = AvdPool([])
        pool.stop_all()  # should not raise


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------


class TestProvision:
    def test_provision_runs_expected_commands(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _RunRecorder()
        monkeypatch.setattr(avd_mod, "_run", recorder)

        pool = AvdPool(["monkey-0"])
        handle = AvdHandle(name="monkey-0", serial="emulator-5554", host_port=6000)

        pool.provision(handle)

        joined = [" ".join(c) for c in recorder.calls]
        assert any("reverse tcp:6000 tcp:6000" in s for s in joined), joined
        assert any("enabled_accessibility_services" in s for s in joined), joined
        assert any("accessibility_enabled 1" in s for s in joined), joined

    def test_provision_custom_port(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _RunRecorder()
        monkeypatch.setattr(avd_mod, "_run", recorder)

        pool = AvdPool(["monkey-0"])
        handle = AvdHandle(name="monkey-0", serial="emulator-5554", host_port=6000)
        pool.provision(handle, reverse_tcp_port=7777)

        joined = [" ".join(c) for c in recorder.calls]
        assert any("reverse tcp:7777 tcp:7777" in s for s in joined), joined

    def test_provision_tolerates_individual_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-step failures should log a warning, not raise."""

        def responder(cmd: list[str]) -> str:
            if "enabled_accessibility_services" in " ".join(cmd):
                raise RuntimeError("simulated failure")
            return ""

        recorder = _RunRecorder(responder)
        monkeypatch.setattr(avd_mod, "_run", recorder)

        pool = AvdPool(["monkey-0"])
        handle = AvdHandle(name="monkey-0", serial="emulator-5554", host_port=6000)
        pool.provision(handle)  # must not raise


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_context_manager_stops_on_exit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_popen: list[_FakePopen],
        fast_sleep: None,
    ) -> None:
        def responder(cmd: list[str]) -> str:
            if "sys.boot_completed" in " ".join(cmd):
                return "1"
            return ""

        recorder = _RunRecorder(responder)
        monkeypatch.setattr(avd_mod, "_run", recorder)
        monkeypatch.setattr(
            avd_mod, "_discover_emulator_serial", lambda _name, _adb=None: "emulator-5554"
        )

        with AvdPool(["monkey-0"], boot_timeout=5.0) as pool:
            pool.start_all()
            recorder.calls.clear()

        joined = [" ".join(c) for c in recorder.calls]
        assert any("emu kill" in s for s in joined), f"expected emu kill on __exit__, got {joined}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestFindAndroid:
    def test_missing_binary_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Reload the real implementation, bypassing the autouse stub above.
        import importlib

        real_mod = importlib.reload(avd_mod)
        monkeypatch.setattr(real_mod.shutil, "which", lambda _n: None)
        monkeypatch.delenv("ANDROID_HOME", raising=False)
        monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
        monkeypatch.setattr(real_mod.os.path, "isfile", lambda _p: False)
        with pytest.raises(FileNotFoundError):
            real_mod._find_android()
