"""AVD lifecycle management: start/stop emulators and provision them for collection."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from types import TracebackType

from loguru import logger


@dataclass
class AvdHandle:
    """Runtime reference to a single running AVD."""

    name: str
    serial: str
    host_port: int
    console_port: int = 0
    process: object | None = None


def _find_android() -> str:
    """Locate the ``android`` CLI, checking PATH then common SDK layouts."""
    found = shutil.which("android")
    if found:
        return found
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidate = os.path.join(android_home, "cmdline-tools", "latest", "bin", "android")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "Could not locate the 'android' CLI. Install Android cmdline-tools and set "
        "ANDROID_HOME, or put 'android' on PATH."
    )


def _find_emulator() -> str:
    """Locate the raw ``emulator`` binary, checking PATH then common SDK layouts."""
    found = shutil.which("emulator")
    if found:
        return found
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidate = os.path.join(android_home, "emulator", "emulator")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "Could not locate the 'emulator' binary. Install Android emulator and set "
        "ANDROID_HOME, or put 'emulator' on PATH."
    )


def _find_adb() -> str:
    """Locate the ``adb`` binary, checking PATH then common SDK layouts."""
    found = shutil.which("adb")
    if found:
        return found
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidate = os.path.join(android_home, "platform-tools", "adb")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "Could not locate the 'adb' binary. Install Android platform-tools and set "
        "ANDROID_HOME, or put 'adb' on PATH."
    )


def _run(cmd: list[str], *, check: bool = True, timeout: float | None = None) -> str:
    """Run a subprocess and return stdout. Raises on non-zero exit when ``check``."""
    logger.debug("avd: {}", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command {cmd!r} failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _wait_for_boot(serial: str, timeout: float, *, adb: str | None = None) -> None:
    """Poll ``getprop sys.boot_completed`` until it returns ``"1"`` or timeout."""
    adb_bin = adb or _find_adb()
    try:
        _run([adb_bin, "-s", serial, "wait-for-device"], timeout=timeout)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        logger.warning("avd: wait-for-device {} -> {}", serial, exc)

    deadline = time.monotonic() + timeout
    poll_interval = 1.0
    while time.monotonic() < deadline:
        try:
            out = _run(
                [adb_bin, "-s", serial, "shell", "getprop", "sys.boot_completed"],
                check=False,
                timeout=5.0,
            )
        except subprocess.TimeoutExpired:
            out = ""
        if out.strip() == "1":
            return
        time.sleep(poll_interval)
    raise TimeoutError(f"AVD '{serial}' did not finish booting within {timeout:.1f}s")


def _discover_emulator_serial(avd_name: str, adb: str | None = None) -> str:
    """Find the ``emulator-NNNN`` serial that corresponds to ``avd_name``.

    Strategy:
      1. List currently attached emulator serials via ``adb devices``.
      2. For each emulator serial, ask ``adb -s <serial> emu avd name`` and match.
    """
    adb_bin = adb or _find_adb()
    listing = _run([adb_bin, "devices"], check=False, timeout=10.0)

    serials: list[str] = []
    for line in listing.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("emulator-"):
            serials.append(parts[0])

    for serial in serials:
        try:
            out = _run(
                [adb_bin, "-s", serial, "emu", "avd", "name"],
                check=False,
                timeout=5.0,
            )
        except subprocess.TimeoutExpired:
            continue
        first_line = out.splitlines()[0].strip() if out else ""
        if first_line == avd_name:
            return serial

    if serials:
        logger.warning(
            "avd: could not match AVD name {!r} against {}; using first serial",
            avd_name,
            serials,
        )
        return serials[0]

    raise RuntimeError(f"No emulator serial found for AVD '{avd_name}'")


@dataclass
class _PoolState:
    handles: list[AvdHandle] = field(default_factory=list)


class AvdPool:
    """Start, stop, and provision a fixed pool of pre-created AVDs."""

    def __init__(
        self,
        avd_names: list[str],
        host_port_base: int = 6000,
        boot_timeout: float = 180.0,
        collector_package: str = "com.monkey.collector",
        collector_service: str = "com.monkey.collector/com.monkey.collector.CollectorService",
        headless: bool = False,
        console_port_base: int = 5554,
    ) -> None:
        self.avd_names = list(avd_names)
        self.host_port_base = host_port_base
        self.boot_timeout = boot_timeout
        self.collector_package = collector_package
        self.collector_service = collector_service
        self.headless = headless
        self.console_port_base = console_port_base
        self.handles: list[AvdHandle] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_one(self, name: str, *, index: int) -> AvdHandle:
        """Start a single AVD, wait for boot, and return its handle.

        AVD at ``index`` gets console port ``console_port_base + 2*index``,
        host_port ``host_port_base + index``. Serial is deterministic:
        ``emulator-<console_port>``.
        """
        emulator_bin = _find_emulator()
        adb_bin = _find_adb()

        host_port = self.host_port_base + index
        console_port = self.console_port_base + 2 * index
        serial = f"emulator-{console_port}"

        cmd = [emulator_bin, "-avd", name, "-port", str(console_port)]
        if self.headless:
            cmd += ["-no-window", "-no-audio", "-no-boot-anim"]

        logger.info(
            "avd: starting {} on console {} (headless={})",
            name, console_port, self.headless,
        )
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _wait_for_boot(serial, self.boot_timeout, adb=adb_bin)

        handle = AvdHandle(
            name=name,
            serial=serial,
            host_port=host_port,
            console_port=console_port,
            process=process,
        )
        self.handles.append(handle)
        logger.info("avd: {} booted as {}", name, serial)
        return handle

    def stop(self, handle: AvdHandle) -> None:
        """Stop a single running AVD via ``adb emu kill`` (fallback to terminate())."""
        try:
            adb_bin: str | None = _find_adb()
        except FileNotFoundError:
            adb_bin = None

        killed = False
        if adb_bin:
            try:
                _run(
                    [adb_bin, "-s", handle.serial, "emu", "kill"],
                    check=False,
                    timeout=10.0,
                )
                killed = True
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                logger.warning("avd: emu kill {} failed: {}", handle.serial, exc)

        proc = handle.process
        if not killed and proc is not None and hasattr(proc, "terminate"):
            try:
                proc.terminate()
            except Exception as exc:
                logger.warning("avd: terminate {} failed: {}", handle.name, exc)

        if handle in self.handles:
            self.handles.remove(handle)

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def provision(
        self, handle: AvdHandle, *, reverse_tcp_port: int | None = None
    ) -> None:
        """Configure reverse port, accessibility service, and overlay permission."""
        adb_bin = _find_adb()
        port = reverse_tcp_port if reverse_tcp_port is not None else handle.host_port

        steps: list[tuple[str, list[str]]] = [
            (
                "reverse port",
                [adb_bin, "-s", handle.serial, "reverse", f"tcp:{port}", f"tcp:{port}"],
            ),
            (
                "enable accessibility service",
                [
                    adb_bin, "-s", handle.serial, "shell",
                    "settings", "put", "secure",
                    "enabled_accessibility_services", self.collector_service,
                ],
            ),
            (
                "set accessibility_enabled",
                [
                    adb_bin, "-s", handle.serial, "shell",
                    "settings", "put", "secure", "accessibility_enabled", "1",
                ],
            ),
            (
                "grant overlay permission",
                [
                    adb_bin, "-s", handle.serial, "shell",
                    "appops", "set", self.collector_package,
                    "SYSTEM_ALERT_WINDOW", "allow",
                ],
            ),
        ]

        for label, cmd in steps:
            try:
                _run(cmd, check=True, timeout=10.0)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                logger.warning(
                    "avd: provision step '{}' failed on {}: {}",
                    label, handle.serial, exc,
                )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> AvdPool:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        for handle in list(self.handles):
            self.stop(handle)
