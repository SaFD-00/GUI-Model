"""Device provisioning — refresh the ``installed`` column, install missing APKs.

Two jobs behind ``monkey-collect sync-installed`` and ``monkey-collect provision``.
They share one rule: **the device is the authority on what is installed**, never
the catalog and never ``adb install``'s exit code.

``sync-installed`` WRITES THE ``installed`` COLUMN AND NOTHING ELSE
==================================================================

``installed`` is a device measurement, ``status`` is a human curation verdict
(see ``catalog.py``). Deriving one from the other wipes the curation record on
the first sync, silently — every row still looks plausible afterwards, so the
loss is only discovered when someone asks why an app that was ruled out is being
collected. :func:`sync_installed_rows` therefore rebuilds each row with
``dataclasses.replace(row, installed=...)``: no other field is even nameable at
that call site.

For the same reason the sync covers **every** row, ``status=excluded`` ones
included. An excluded row that IS on the device must read ``installed=true`` —
that is simply the truth about the handset — and it stays uncollected regardless,
because :attr:`AppRow.is_collectable` gates on ``is_excluded`` independently.
Skipping excluded rows here would be the same conflation in the other direction.

``provision`` RESOLVES AN APK THROUGH THREE SOURCES, IN THIS ORDER
=================================================================

Measured against the live Pixel 6 (serial 19101FDF6004EH, oriole, Android 16 /
SDK 36, ABI ``arm64-v8a`` only — there is no x86_64 build path here):

1. :data:`APK_CACHE_DIRNAME` — this repo's own local APK cache
   (``catalog/apks/``). First because it is offline, already ABI-checked, and
   F-Droid drops apps from its index without notice, which makes the local
   cache the only durable copy.
2. :data:`ANDROID_WORLD_DIRNAME` — AndroidWorld's benchmark APKs, named
   ``{package_id}_{versionCode}.apk``. These are VERSION-PINNED, so for the
   ``androidworld_fdroid`` tier they are *more* correct than F-Droid's latest:
   AndroidWorld's tasks were authored against these exact builds.
3. f-droid.org — last, because it is the only source that can fail for reasons
   outside this repo, and because F-Droid ships the LATEST build, which for the
   ``androidworld_fdroid`` tier is the wrong one.

   A note on its recorded failure: the catalog's excluded rows say the ``/repo``
   binary path fails TLS certificate verification from this host. Re-measured on
   2026-08-28 through :func:`_urlopen` (stdlib ``urllib``, macOS system CA bundle
   at ``/private/etc/ssl/cert.pem``) it SUCCEEDED — 8.9 MB of real zip for
   ``io.github.muntashirakon.Music`` vc 10603. So that failure is specific to the
   client that hit it, not a property of the network, and this path is kept live.
   Either way certificate verification is never disabled to route around a
   failure: an APK pulled over an unverified channel is an unattributable binary
   installed on a device holding real accounts.

Installs are verified by re-querying ``pm list packages``, not by reading
``adb install``'s return code. That is not belt-and-braces: OsmAnd (335 MB)
timed out on the host once and had in fact completed on the device, so the exit
code and the device disagreed. Re-query makes the retry decision on the only
fact that matters — is it there now?

The ledger (``catalog/PROVISION_MISSING.json``) is cumulative and means "not
installable from here", not "the last attempt failed". A scoped run (``--only``)
updates the packages inside its own scope and never drops the rest; a corrupt
ledger raises instead of resetting to empty, because a silent reset reproduces
exactly the loss the ledger exists to prevent.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from monkey_collector.adb import AdbClient, AdbError
from monkey_collector.catalog import AppRow
from monkey_collector.paths import catalog_path, project_root

# -- sources ---------------------------------------------------------------

SOURCE_CACHE = "monkey-cache"
SOURCE_ANDROID_WORLD = "android-world"
SOURCE_FDROID = "f-droid"

#: This repo's own committed APK cache, resolved through ``catalog_path()``
#: (i.e. ``catalog/apks/``) so the path is single-sourced through paths.py
#: rather than hard-coded here.
APK_CACHE_DIRNAME = "apks"
#: AndroidWorld's pinned APKs live in EpochDroid, a sibling checkout of the
#: monorepo (``Implicit-World-Modeling``) that contains this repo — two levels
#: up from ``project_root()`` (``parents[1]``), not one. Derived from
#: ``project_root()`` rather than a hard-coded absolute path so a clone
#: elsewhere still resolves.
ANDROID_WORLD_DIRNAME = Path("EpochDroid") / "benchmarks" / "apks" / "android_world"

FDROID_API_URL = "https://f-droid.org/api/v1/packages/{package}"
FDROID_REPO_URL = "https://f-droid.org/repo/{package}_{version_code}.apk"
FDROID_API_TIMEOUT = 30.0
FDROID_DOWNLOAD_TIMEOUT = 600.0

#: ``adb install`` timeout. Deliberately far above :data:`adb.DEFAULT_TIMEOUT`
#: (30s): OsmAnd is 335 MB and pushing it over USB blows straight through it.
INSTALL_TIMEOUT = 600.0
#: One retry, because the observed failure (large APK, host-side timeout) is
#: transient. Anything that fails twice is a real failure and goes to the ledger.
INSTALL_RETRIES = 1

#: The device's only ABI. Recorded for the ledger's reason strings; the small
#: F-Droid API carries no ``nativecode``, so an incompatible build cannot be
#: detected before install — it surfaces as INSTALL_FAILED_NO_MATCHING_ABIS.
DEVICE_ABI = "arm64-v8a"

#: ``adb install`` failure for a split APK whose base was handed over alone
#: (com.ticktick.task). Retrying cannot help: the other splits are missing, so
#: this short-circuits to a reason naming ``adb install-multiple``.
MISSING_SPLIT_MARKER = "INSTALL_FAILED_MISSING_SPLIT"

LEDGER_NAME = "PROVISION_MISSING.json"
LEDGER_SCHEMA_VERSION = 1
LEDGER_ENTRY_KEYS = ("source", "reason", "first_seen", "last_seen")

SKIP_EXCLUDED = "excluded by curation (status=excluded)"
SKIP_PENDING = "package id unresolved (PENDING)"
SKIP_INSTALLED = "already on the device"


class ProvisionError(RuntimeError):
    """An APK could not be resolved or installed, for a reason worth recording."""


class LedgerError(RuntimeError):
    """The ledger could not be read. Never repaired automatically — see module docs."""


# ---------------------------------------------------------------------------
# sync-installed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstalledFlip:
    """One row whose ``installed`` value disagreed with the device."""

    package_id: str
    app_name: str
    before: str
    after: str

    @property
    def direction(self) -> str:
        return f"{self.before} -> {self.after}"


def sync_installed_rows(
    rows: list[AppRow], device_packages: set[str]
) -> tuple[list[AppRow], list[InstalledFlip]]:
    """Return (rows with ``installed`` refreshed, the flips that were applied).

    Pure: nothing is written here, so ``--dry-run`` and the real run share one
    code path and cannot drift apart. ``dataclasses.replace`` is used instead of
    building a fresh :class:`AppRow` so a future column addition cannot silently
    lose a value, and so ``status`` is not even reachable from this call.

    Every row is considered, ``status=excluded`` included — see the module
    docstring for why filtering them here would be the same mistake as deriving
    ``status`` from ``installed``.
    """
    updated: list[AppRow] = []
    flips: list[InstalledFlip] = []
    for row in rows:
        after = "true" if row.package_id in device_packages else "false"
        if after != row.installed:
            flips.append(InstalledFlip(row.package_id, row.app_name, row.installed, after))
            updated.append(replace(row, installed=after))
        else:
            updated.append(row)
    return updated, flips


# ---------------------------------------------------------------------------
# APK resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApkResolution:
    """Where an APK came from, or why it could not be found."""

    package_id: str
    path: Path | None
    source: str = ""
    version_code: int | None = None
    #: Populated only when ``path`` is None: one line per source that was tried.
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.path is not None


def apk_cache_dir() -> Path:
    """This repo's own offline APK cache: ``catalog/apks/``."""
    return catalog_path(APK_CACHE_DIRNAME)


def android_world_dir() -> Path:
    """AndroidWorld's version-pinned benchmark APKs (EpochDroid checkout)."""
    return project_root().parents[1] / ANDROID_WORLD_DIRNAME


def download_dir() -> Path:
    """Where f-droid downloads land: under ``runtime/``, which is gitignored.

    Downloads are NOT written into the committed APK cache (``catalog/apks/``):
    that directory is a committed asset of this repo, and a half-written file
    there would look like a cache hit to every later run.
    """
    return project_root() / "runtime" / "apks"


def find_cached_apk(package_id: str, cache_dir: Path) -> Path | None:
    """``{cache_dir}/{package_id}.apk`` if it exists. Exact name, no globbing."""
    candidate = cache_dir / f"{package_id}.apk"
    return candidate if candidate.is_file() else None


def _version_sort_key(token: str) -> tuple[int, int, str]:
    """Order AndroidWorld filename suffixes: numeric versionCodes win, then name.

    ``net.osmand-4.6.13.apk`` carries a versionNAME, not a versionCode, so
    ``int()`` would raise. Non-numeric tokens sort below every numeric one
    instead of crashing the whole resolution.
    """
    return (1, int(token), token) if token.isdigit() else (0, 0, token)


def find_android_world_apk(package_id: str, aw_dir: Path) -> tuple[Path, int | None] | None:
    """Highest-versioned ``{package_id}_{versionCode}.apk`` in *aw_dir*.

    The separator check is load-bearing. A bare ``stem.startswith(package_id)``
    would resolve ``com.simplemobiletools.calendar.pro_238.apk`` for the package
    ``com.simplemobiletools.calendar`` — a DIFFERENT app — and the run would then
    install the wrong APK and report success, which no later step can detect.
    So the character after the package id must be a separator (``_`` as
    AndroidWorld writes it, ``-`` as ``net.osmand-4.6.13.apk`` does) or the name
    must be the bare ``{package_id}.apk``.
    """
    if not aw_dir.is_dir():
        return None
    candidates: list[tuple[tuple[int, int, str], Path, int | None]] = []
    for path in sorted(aw_dir.glob("*.apk")):
        stem = path.stem
        if stem == package_id:
            token = ""
        elif stem.startswith(package_id) and stem[len(package_id)] in "_-":
            token = stem[len(package_id) + 1 :]
        else:
            continue
        version_code = int(token) if token.isdigit() else None
        candidates.append((_version_sort_key(token), path, version_code))
    if not candidates:
        return None
    _, best_path, best_vc = max(candidates, key=lambda item: item[0])
    return best_path, best_vc


# -- f-droid ---------------------------------------------------------------


def _urlopen(url: str, timeout: float) -> Any:
    """The module's ONE network seam; tests monkeypatch this and nothing else.

    Note what is absent: no ``ssl._create_unverified_context``. A certificate
    failure on the ``/repo`` path is recorded in the ledger and the package goes
    uninstalled; it is never retried with verification off, which would put an
    unauthenticated APK on a device that holds real accounts.
    """
    return urllib.request.urlopen(url, timeout=timeout)  # noqa: S310


def fdroid_version_code(package_id: str, *, timeout: float = FDROID_API_TIMEOUT) -> int:
    """Suggested versionCode from ``/api/v1/packages/<pkg>``.

    This small endpoint returns versionCode/versionName only — no ``nativecode``,
    so the ABI cannot be pre-filtered the way Monkey-Collector does off the 47 MB
    ``index-v2.json``. Pulling that index to learn the ABI is not worth it while
    the download below cannot complete anyway.
    """
    url = FDROID_API_URL.format(package=package_id)
    try:
        with _urlopen(url, timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError as exc:  # URLError/HTTPError/SSLCertVerificationError all land here
        raise ProvisionError(f"f-droid api unreachable: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProvisionError(f"f-droid api returned unparseable JSON: {exc}") from exc

    suggested = payload.get("suggestedVersionCode")
    if isinstance(suggested, int):
        return suggested
    versions: list[int] = [
        v["versionCode"]
        for v in payload.get("packages") or []
        if isinstance(v.get("versionCode"), int)
    ]
    if not versions:
        raise ProvisionError(f"not in the f-droid index: {package_id}")
    return max(versions)


def download_fdroid_apk(
    package_id: str, dest_dir: Path, *, timeout: float = FDROID_DOWNLOAD_TIMEOUT
) -> tuple[Path, int]:
    """Fetch ``{package_id}_{versionCode}.apk`` from f-droid.org into *dest_dir*.

    Streamed to a ``.part`` file and renamed only on success, so an interrupted
    download can never be mistaken for a cache hit by the next run.
    """
    version_code = fdroid_version_code(package_id)
    url = FDROID_REPO_URL.format(package=package_id, version_code=version_code)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{package_id}_{version_code}.apk"
    part = dest.with_suffix(".apk.part")
    logger.info(f"[provision] downloading {package_id} (vc={version_code}) from f-droid")
    try:
        with _urlopen(url, timeout) as response, part.open("wb") as fh:
            shutil.copyfileobj(response, fh)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise ProvisionError(f"f-droid download failed: {exc}") from exc
    part.replace(dest)
    return dest, version_code


def resolve_apk(
    package_id: str,
    *,
    cache_dir: Path,
    aw_dir: Path,
    dest_dir: Path,
    allow_download: bool = True,
) -> ApkResolution:
    """Find an APK for *package_id* through the three sources, in priority order.

    The first hit wins and no later source is consulted — the cache is offline
    and already ABI-checked, so probing the network past it would only add
    latency and a way to fail.
    """
    cached = find_cached_apk(package_id, cache_dir)
    if cached is not None:
        return ApkResolution(package_id, cached, SOURCE_CACHE)

    pinned = find_android_world_apk(package_id, aw_dir)
    if pinned is not None:
        path, version_code = pinned
        return ApkResolution(package_id, path, SOURCE_ANDROID_WORLD, version_code)

    tried = f"not in {cache_dir.name}/ or {aw_dir.name}/"
    if not allow_download:
        return ApkResolution(package_id, None, reason=f"{tried}; download not attempted")
    try:
        path, version_code = download_fdroid_apk(package_id, dest_dir)
    except ProvisionError as exc:
        return ApkResolution(package_id, None, reason=f"{tried}; {exc}")
    return ApkResolution(package_id, path, SOURCE_FDROID, version_code)


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def _adb_install(client: AdbClient, apk_path: Path, *, timeout: float) -> tuple[bool, str]:
    """Run ``adb install -r <apk>``; return (adb-said-ok, its output).

    Reaches into ``AdbClient._run`` on purpose. The two public alternatives are
    both worse: ``AdbClient.shell`` cannot run a host-side ``install``, and
    re-resolving the adb binary here with a private copy of ``_find_adb`` would
    give provision a second, independently-drifting notion of which adb and which
    serial it is talking to — i.e. it could install onto a device the collection
    run never drives. One narrow call keeps serial pinning and binary resolution
    single-sourced. Adding a public ``AdbClient.install`` is the real fix and is
    left to whoever owns ``adb.py``.
    """
    try:
        result = client._run(["install", "-r", str(apk_path)], timeout=timeout)
    except AdbError as exc:
        return False, str(exc)
    return True, str(result.stdout or "").strip()


def install_apk(
    client: AdbClient,
    package_id: str,
    apk_path: Path,
    *,
    already_present: bool = False,
    timeout: float = INSTALL_TIMEOUT,
    retries: int = INSTALL_RETRIES,
) -> tuple[bool, str]:
    """Install *apk_path* and confirm with ``pm list packages``. Returns (ok, note).

    For a FRESH install, success is decided by the re-query and never by adb's
    exit code: OsmAnd's 335 MB push reported a host-side timeout while the device
    had in fact finished, so trusting the code would have retried a completed
    install and then declared a working app missing.

    *already_present* (the ``--force`` reinstall path) inverts that. The package
    was on the device BEFORE the install ran, so finding it afterwards proves
    nothing — a rejected APK (signature mismatch, ``INSTALL_FAILED_VERSION_DOWNGRADE``,
    a corrupt file) leaves the old build in place and the re-query would happily
    report success and write no ledger entry. When the caller says the package
    was already there, adb's verdict is the only signal left, so it is required.

    A split-APK base (``INSTALL_FAILED_MISSING_SPLIT``) short-circuits instead of
    retrying — the other splits are not on disk, so a second attempt fails the
    same way and only costs the timeout again.
    """
    note = ""
    for attempt in range(retries + 1):
        adb_ok, output = _adb_install(client, apk_path, timeout=timeout)
        note = output
        try:
            present = package_id in set(client.list_packages())
        except AdbError as exc:  # the link dropped; the install verdict is unknown
            return False, f"install verification failed: {exc}"
        if present and (adb_ok or not already_present):
            if not adb_ok:
                note = f"adb reported failure but the device has it ({output})"
            return True, note
        if MISSING_SPLIT_MARKER in output:
            return False, (
                f"{MISSING_SPLIT_MARKER}: base APK alone is not installable; "
                f"needs the full split set via `adb install-multiple`"
            )
        if attempt < retries:
            logger.warning(f"[provision] {package_id}: install attempt {attempt + 1} failed, retrying")
    return False, note or "install failed for an unreported reason"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvisionPlan:
    """What a provision run will attempt, and what it will not."""

    targets: list[AppRow]
    skipped: list[tuple[AppRow, str]]
    #: ``--only`` ids that match no catalog row — a typo'd package would
    #: otherwise be silently provisioned as nothing at all.
    unknown: list[str]

    @property
    def scope_ids(self) -> set[str]:
        """Packages this run is entitled to touch in the ledger.

        Skipped rows are inside the scope: a row that is now installed must be
        able to CLEAR its stale ledger entry, which is the whole point of the
        ledger meaning "not held" rather than "last attempt failed".
        """
        return {r.package_id for r in self.targets} | {r.package_id for r, _ in self.skipped}


def plan_provision(
    rows: list[AppRow],
    device_packages: set[str],
    *,
    only: list[str] | None = None,
    force: bool = False,
) -> ProvisionPlan:
    """Decide which rows to install.

    ``status=excluded`` rows are dropped unconditionally, ``--force`` included:
    the exclusion is a curation decision about the app, not a statement about
    this handset, so no flag here may overrule it.

    "Already installed" is read from *device_packages*, not from the catalog's
    ``installed`` column — a stale column would otherwise make provision skip an
    app that is genuinely absent.
    """
    selected = rows
    unknown: list[str] = []
    if only is not None:
        known = {r.package_id for r in rows}
        unknown = [pkg for pkg in only if pkg not in known]
        wanted = set(only)
        selected = [r for r in rows if r.package_id in wanted]

    targets: list[AppRow] = []
    skipped: list[tuple[AppRow, str]] = []
    for row in selected:
        if row.is_excluded:
            skipped.append((row, SKIP_EXCLUDED))
        elif row.is_pending:
            skipped.append((row, SKIP_PENDING))
        elif row.package_id in device_packages and not force:
            skipped.append((row, SKIP_INSTALLED))
        else:
            targets.append(row)
    return ProvisionPlan(targets=targets, skipped=skipped, unknown=unknown)


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def ledger_path() -> Path:
    return catalog_path(LEDGER_NAME)


def empty_ledger() -> dict[str, Any]:
    return {"schema_version": LEDGER_SCHEMA_VERSION, "entries": {}}


def load_ledger(path: Path) -> dict[str, Any]:
    """Read the cumulative ledger. An absent file is a fresh ledger; a corrupt
    one raises.

    Falling back to :func:`empty_ledger` on a parse error would drop every record
    the ledger exists to keep, and the run that did it would look successful.
    Validation reaches into each entry rather than stopping at the top level,
    because syntactically valid JSON with a broken entry would otherwise blow up
    later — after the installs have already run. Repair is manual on purpose.
    """
    if not path.exists():
        return empty_ledger()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LedgerError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise LedgerError(f"{path}: unexpected shape (no 'entries' object)")
    for package_id, entry in data["entries"].items():
        if not isinstance(entry, dict):
            raise LedgerError(
                f"{path}: entry '{package_id}' is {type(entry).__name__}, expected an object"
            )
        for key in LEDGER_ENTRY_KEYS:
            if not isinstance(entry.get(key), str):
                raise LedgerError(f"{path}: entry '{package_id}' has no string '{key}'")
    data.setdefault("schema_version", LEDGER_SCHEMA_VERSION)
    return data


def update_ledger(
    ledger: dict[str, Any],
    *,
    failures: dict[str, tuple[str, str]],
    resolved: set[str],
    scope_ids: set[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Merge one run's outcome in, touching in-scope packages ONLY.

    Args:
        failures: ``package_id -> (source, reason)`` for this run's failures.
        resolved: packages confirmed present on the device at run end.
        scope_ids: what this run was allowed to look at. A ``--only`` run must
            leave every other record exactly as it found it — otherwise the
            first scoped run silently truncates the accumulated history.

    ``first_seen`` survives an upsert so the ledger records how long a package
    has been unobtainable, not just that it failed again today.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    entries: dict[str, Any] = {
        key: dict(value) for key, value in (ledger.get("entries") or {}).items()
    }

    for package_id, (source, reason) in failures.items():
        if package_id not in scope_ids:
            continue
        previous = entries.get(package_id) or {}
        entries[package_id] = {
            "source": source,
            "reason": reason,
            "first_seen": previous.get("first_seen") or stamp,
            "last_seen": stamp,
        }

    # Resolution: the ledger means "not held", not "last attempt failed", so an
    # in-scope package that is present at run end is dropped — whether this run
    # installed it or found it already there.
    for package_id in resolved & scope_ids:
        entries.pop(package_id, None)

    out = dict(ledger)
    out["schema_version"] = LEDGER_SCHEMA_VERSION
    out["entries"] = dict(sorted(entries.items()))
    return out


def write_ledger(ledger: dict[str, Any], path: Path) -> Path:
    """Write the ledger atomically (tempfile + ``os.replace``).

    A half-written ledger is worse than none: :func:`load_ledger` would refuse to
    read it and every later run would abort until someone repaired it by hand.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "ANDROID_WORLD_DIRNAME",
    "APK_CACHE_DIRNAME",
    "DEVICE_ABI",
    "INSTALL_RETRIES",
    "INSTALL_TIMEOUT",
    "LEDGER_NAME",
    "LEDGER_SCHEMA_VERSION",
    "SKIP_EXCLUDED",
    "SKIP_INSTALLED",
    "SKIP_PENDING",
    "SOURCE_ANDROID_WORLD",
    "SOURCE_CACHE",
    "SOURCE_FDROID",
    "ApkResolution",
    "InstalledFlip",
    "LedgerError",
    "ProvisionError",
    "ProvisionPlan",
    "android_world_dir",
    "apk_cache_dir",
    "download_dir",
    "download_fdroid_apk",
    "empty_ledger",
    "fdroid_version_code",
    "find_android_world_apk",
    "find_cached_apk",
    "install_apk",
    "ledger_path",
    "load_ledger",
    "plan_provision",
    "resolve_apk",
    "sync_installed_rows",
    "update_ledger",
    "write_ledger",
]
