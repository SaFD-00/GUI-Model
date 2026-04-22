"""Download APKs for apps in apps.csv from F-Droid and Google Play.

F-Droid uses the public HTTP API (https://f-droid.org/api/v1/packages/<pkg>).
Play Store uses `gplaydl` v2 as a subprocess (Aurora Store token dispenser,
anonymous auth).

Output layout (compatible with /setup-apks):

    Monkey-Collector/apks/{package_id}.apk   # base APK only
    Monkey-Collector/apks/MISSING.md          # per-source failure log

Run:

    python -m scripts.download_apks --help
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from loguru import logger

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from server.pipeline.app_catalog import AppCatalog, AppJob  # noqa: E402

FDROID_INDEX_URL = "https://f-droid.org/repo/index-v2.json"
FDROID_REPO_BASE = "https://f-droid.org/repo"
DEFAULT_ABI = "x86_64"
DEFAULT_PLAYSTORE_ARCH = "arm64"
DEFAULT_CONCURRENCY = 4
GPLAYDL_TIMEOUT_SEC = 300
HTTP_CHUNK_BYTES = 65536


class DownloadError(Exception):
    """Download failed for a recorded reason (logged and added to MISSING.md)."""


@dataclass
class DownloadResult:
    package_id: str
    source: str
    status: str
    reason: str = ""
    path: Path | None = None


# ── F-Droid ────────────────────────────────────────────────────────────────
# F-Droid exposes rich per-version metadata (nativecode, sha256, file path) only
# through the ~47 MB ``index-v2.json`` feed. The small ``/api/v1/packages/<pkg>``
# endpoint returns versionCode+versionName only. We fetch the index once and
# cache it in-process for all F-Droid lookups.


def fetch_fdroid_index(session: requests.Session) -> dict:
    logger.info(f"fetching F-Droid index ({FDROID_INDEX_URL})…")
    resp = session.get(FDROID_INDEX_URL, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    count = len(data.get("packages") or {})
    logger.info(f"F-Droid index loaded: {count} packages")
    return data


def pick_fdroid_build(package_entry: dict, abi: str) -> dict:
    """Select the best F-Droid version entry for the target ABI.

    ``package_entry`` is a node from ``index-v2.json`` → ``packages[<pkg>]``
    shaped as ``{"metadata": {...}, "versions": {<key>: {"manifest": {...}, "file": {...}}}}``.

    A manifest with empty or absent ``nativecode`` is universal and always
    eligible. Otherwise ``nativecode`` must include ``abi``. The highest
    ``versionCode`` wins.

    Returns a dict with keys: ``versionCode``, ``file_name`` (leading slash as
    stored in the index), ``sha256`` (optional), ``size`` (optional).
    """
    versions = (package_entry or {}).get("versions") or {}
    if not versions:
        raise DownloadError("no versions in F-Droid index")

    candidates: list[dict] = []
    seen_archs: set[str] = set()
    for v in versions.values():
        manifest = v.get("manifest") or {}
        file_info = v.get("file") or {}
        native = manifest.get("nativecode") or []
        seen_archs.update(native or ["universal"])
        if native and abi not in native:
            continue
        name = file_info.get("name")
        if not name:
            continue
        candidates.append(
            {
                "versionCode": manifest.get("versionCode", 0),
                "file_name": name,
                "sha256": file_info.get("sha256"),
                "size": file_info.get("size"),
            }
        )

    if not candidates:
        raise DownloadError(f"no {abi} build in F-Droid (available: {sorted(seen_archs)})")

    candidates.sort(key=lambda b: b["versionCode"], reverse=True)
    return candidates[0]


def download_fdroid(
    session: requests.Session,
    package_id: str,
    abi: str,
    dest: Path,
    *,
    index: dict,
) -> DownloadResult:
    try:
        pkgs = index.get("packages") or {}
        entry = pkgs.get(package_id)
        if entry is None:
            raise DownloadError(f"not in F-Droid index: {package_id}")
        build = pick_fdroid_build(entry, abi)
        file_name = build["file_name"]
        expected_sha = build.get("sha256")

        url = f"{FDROID_REPO_BASE}{file_name}" if file_name.startswith("/") else f"{FDROID_REPO_BASE}/{file_name}"
        logger.info(f"[fdroid] {package_id} → {file_name} (vc={build['versionCode']})")
        resp = session.get(url, timeout=180, stream=True)
        resp.raise_for_status()

        tmp = dest.with_suffix(".apk.part")
        hasher = hashlib.sha256() if expected_sha else None
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=HTTP_CHUNK_BYTES):
                fh.write(chunk)
                if hasher is not None:
                    hasher.update(chunk)

        if hasher is not None and hasher.hexdigest().lower() != expected_sha.lower():
            tmp.unlink(missing_ok=True)
            raise DownloadError(
                f"sha256 mismatch: expected {expected_sha[:12]}…, got {hasher.hexdigest()[:12]}…"
            )
        tmp.replace(dest)
        return DownloadResult(package_id, "F-Droid", "downloaded", path=dest)
    except DownloadError as exc:
        return DownloadResult(package_id, "F-Droid", "failed", reason=str(exc))
    except requests.RequestException as exc:
        return DownloadResult(package_id, "F-Droid", "failed", reason=f"http error: {exc}")


# ── PlayStore (gplaydl) ────────────────────────────────────────────────────

def build_gplaydl_command(package_id: str, arch: str, output_dir: Path) -> list[str]:
    return [
        "gplaydl",
        "download",
        package_id,
        "--output",
        str(output_dir),
        "--arch",
        arch,
        "--no-extras",
    ]


def run_gplaydl(package_id: str, arch: str, output_dir: Path) -> None:
    cmd = build_gplaydl_command(package_id, arch, output_dir)
    logger.debug(f"[playstore] {' '.join(cmd)}")
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=GPLAYDL_TIMEOUT_SEC, check=False
    )
    if proc.returncode != 0:
        combined = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " | ".join(combined[-5:]) if combined else "no output"
        raise DownloadError(f"gplaydl rc={proc.returncode}: {tail}")


_SPLIT_PREFIXES = ("split_", "split.", "config.")


def finalize_playstore_output(tmp_dir: Path, dest: Path) -> tuple[Path, bool]:
    """Copy the base APK out of the gplaydl output directory.

    Returns (dest, had_splits). ``had_splits`` flags that split APKs existed
    alongside the base, so the caller can record a warning in MISSING.md.
    """
    apks = sorted(tmp_dir.rglob("*.apk"))
    if not apks:
        raise DownloadError("gplaydl produced no APK files")

    base_candidates = [p for p in apks if not p.name.startswith(_SPLIT_PREFIXES)]
    if not base_candidates:
        base_candidates = apks
    base = max(base_candidates, key=lambda p: p.stat().st_size)
    had_splits = any(p.name.startswith(_SPLIT_PREFIXES) for p in apks)
    shutil.copy2(base, dest)
    return dest, had_splits


def download_playstore(package_id: str, arch: str, dest: Path) -> DownloadResult:
    try:
        with tempfile.TemporaryDirectory(prefix="gplaydl_") as tmp:
            tmp_path = Path(tmp)
            run_gplaydl(package_id, arch, tmp_path)
            dest_path, had_splits = finalize_playstore_output(tmp_path, dest)
        reason = "base-only saved (split APKs present, may need manual fallback)" if had_splits else ""
        return DownloadResult(package_id, "PlayStore", "downloaded", reason=reason, path=dest_path)
    except subprocess.TimeoutExpired:
        return DownloadResult(package_id, "PlayStore", "failed", reason="gplaydl timeout")
    except DownloadError as exc:
        return DownloadResult(package_id, "PlayStore", "failed", reason=str(exc))


# ── Orchestration ──────────────────────────────────────────────────────────

def partition_jobs(
    jobs: list[AppJob],
    sources: set[str],
    only: set[str] | None,
) -> tuple[list[AppJob], list[AppJob], list[AppJob]]:
    """Split jobs into (fdroid, playstore, system_skipped) lists.

    `sources` is a subset of {"fdroid", "playstore"}. `only`, if set, filters
    to those package_ids. Unknown-source jobs are logged and dropped.
    """
    fdroid: list[AppJob] = []
    playstore: list[AppJob] = []
    system: list[AppJob] = []
    for job in jobs:
        if only is not None and job.package_id not in only:
            continue
        src = (job.source or "").strip().lower()
        if src == "f-droid":
            if "fdroid" in sources:
                fdroid.append(job)
        elif src == "playstore":
            if "playstore" in sources:
                playstore.append(job)
        elif src == "system":
            system.append(job)
        else:
            logger.warning(f"{job.package_id}: unknown source '{job.source}'")
    return fdroid, playstore, system


def render_missing_md(
    *,
    abi: str,
    playstore_arch: str,
    total_targets: int,
    downloaded: int,
    skipped: int,
    results: list[DownloadResult],
    system_skipped: list[AppJob],
    generated_at: datetime | None = None,
) -> str:
    now = generated_at or datetime.now(timezone.utc)
    failed = [r for r in results if r.status == "failed"]
    by_source: dict[str, list[DownloadResult]] = {"F-Droid": [], "PlayStore": []}
    for r in failed:
        by_source.setdefault(r.source, []).append(r)

    lines: list[str] = []
    lines.append("# Missing APKs")
    lines.append("")
    lines.append(f"- Generated: {now.isoformat(timespec='seconds')}")
    lines.append(f"- Target ABI (F-Droid filter): `{abi}`")
    lines.append(f"- PlayStore gplaydl arch: `{playstore_arch}`")
    lines.append(f"- Total targets: {total_targets}")
    lines.append(f"- Downloaded: {downloaded}")
    lines.append(f"- Skipped (already present): {skipped}")
    lines.append(f"- Failed: {len(failed)}")
    lines.append(f"- System (skipped, not downloadable): {len(system_skipped)}")
    lines.append("")

    for src in ("F-Droid", "PlayStore"):
        items = by_source.get(src, [])
        if not items:
            continue
        lines.append(f"## {src} ({len(items)})")
        for r in items:
            lines.append(f"- `{r.package_id}` — {r.reason}")
        lines.append("")

    if system_skipped:
        lines.append(f"## System ({len(system_skipped)})")
        for job in system_skipped:
            lines.append(f"- `{job.package_id}` — platform built-in, not downloadable")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="download-apks",
        description="Download APKs from F-Droid and Google Play based on apps.csv",
    )
    p.add_argument("--source", choices=["all", "fdroid", "playstore"], default="all")
    p.add_argument(
        "--abi",
        default=DEFAULT_ABI,
        help=f"F-Droid native-lib ABI filter (default: {DEFAULT_ABI})",
    )
    p.add_argument(
        "--playstore-arch",
        choices=["arm64", "armv7"],
        default=DEFAULT_PLAYSTORE_ARCH,
        help="gplaydl device profile (default: arm64; x86_64 AVDs use ARM translation)",
    )
    p.add_argument("--only", default="", help="Comma-separated package_id allowlist")
    p.add_argument("--force", action="store_true", help="Re-download even if apk exists")
    p.add_argument("--dry-run", action="store_true", help="Print targets only, no downloads")
    p.add_argument("--apks-dir", default=None, help="Override apks directory (default: ./apks)")
    p.add_argument("--csv", default=None, help="Override apps.csv path (default: ./apps.csv)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    apks_dir = Path(args.apks_dir) if args.apks_dir else _REPO_ROOT / "apks"
    csv_path = Path(args.csv) if args.csv else _REPO_ROOT / "apps.csv"
    apks_dir.mkdir(parents=True, exist_ok=True)

    sources: set[str] = {"fdroid", "playstore"} if args.source == "all" else {args.source}
    only = {p.strip() for p in args.only.split(",") if p.strip()} or None

    catalog = AppCatalog.load(csv_path)
    fdroid_jobs, playstore_jobs, system_jobs = partition_jobs(catalog.filter(), sources, only)
    total = len(fdroid_jobs) + len(playstore_jobs)

    logger.info(
        f"targets: fdroid={len(fdroid_jobs)} playstore={len(playstore_jobs)} "
        f"system-skipped={len(system_jobs)} abi={args.abi} ps-arch={args.playstore_arch}"
    )

    if args.dry_run:
        for job in fdroid_jobs + playstore_jobs:
            print(f"{job.source}\t{job.package_id}")
        return 0

    def needs_download(job: AppJob) -> bool:
        dest = apks_dir / f"{job.package_id}.apk"
        if dest.exists() and not args.force:
            logger.info(f"[skip] {job.package_id} already present")
            return False
        return True

    fdroid_pending = [j for j in fdroid_jobs if needs_download(j)]
    playstore_pending = [j for j in playstore_jobs if needs_download(j)]
    skipped = total - len(fdroid_pending) - len(playstore_pending)

    results: list[DownloadResult] = []

    if fdroid_pending:
        with requests.Session() as session:
            try:
                index = fetch_fdroid_index(session)
            except requests.RequestException as exc:
                logger.error(f"failed to fetch F-Droid index: {exc}")
                for job in fdroid_pending:
                    results.append(
                        DownloadResult(
                            job.package_id, "F-Droid", "failed", reason=f"index fetch failed: {exc}"
                        )
                    )
                index = None
            if index is not None:
                with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
                    futures = {
                        ex.submit(
                            download_fdroid,
                            session,
                            job.package_id,
                            args.abi,
                            apks_dir / f"{job.package_id}.apk",
                            index=index,
                        ): job
                        for job in fdroid_pending
                    }
                    for fut in as_completed(futures):
                        r = fut.result()
                        results.append(r)
                        marker = "[+]" if r.status == "downloaded" else "[-]"
                        suffix = f" — {r.reason}" if r.reason else ""
                        print(f"{marker} F-Droid   {r.package_id}{suffix}")

    for job in playstore_pending:
        dest = apks_dir / f"{job.package_id}.apk"
        r = download_playstore(job.package_id, args.playstore_arch, dest)
        results.append(r)
        marker = "[+]" if r.status == "downloaded" else "[-]"
        suffix = f" — {r.reason}" if r.reason else ""
        print(f"{marker} PlayStore {r.package_id}{suffix}")

    downloaded = sum(1 for r in results if r.status == "downloaded")
    failed = sum(1 for r in results if r.status == "failed")

    missing_md = apks_dir / "MISSING.md"
    missing_md.write_text(
        render_missing_md(
            abi=args.abi,
            playstore_arch=args.playstore_arch,
            total_targets=total,
            downloaded=downloaded,
            skipped=skipped,
            results=results,
            system_skipped=system_jobs,
        ),
        encoding="utf-8",
    )

    print()
    print("=== download-apks summary ===")
    print(f"targets:     {total}   (fdroid {len(fdroid_jobs)} + playstore {len(playstore_jobs)})")
    print(f"downloaded:  {downloaded}")
    print(f"skipped:     {skipped}   (already present)")
    print(f"failed:      {failed}")
    print(f"system:      {len(system_jobs)}   (not downloadable)")
    print(f"missing log: {missing_md}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
