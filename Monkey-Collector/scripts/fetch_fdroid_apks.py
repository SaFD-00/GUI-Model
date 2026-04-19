#!/usr/bin/env python3
"""Fetch F-Droid APKs listed in apps.csv into apks/{package_id}.apk.

Strategy: download F-Droid index-v2.json once (~50 MB, cached under /tmp),
look each F-Droid-sourced apps.csv package up, pick the highest-versionCode
version entry, and fetch its ``file.name`` from the repo. This copes with
multi-variant APKs (per-ABI, per-screen) that do not follow the default
``{pkg}_{versionCode}.apk`` naming.

Apps.csv rows with source=PlayStore or System are not downloadable via this
script; they are emitted into ``apks/MISSING.md`` so the operator knows what
to supply manually.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from server.pipeline.app_catalog import AppCatalog, AppJob  # noqa: E402

FDROID_INDEX_URL = "https://f-droid.org/repo/index-v2.json"
FDROID_REPO_BASE = "https://f-droid.org/repo"
USER_AGENT = "Monkey-Collector-fetch/1.0"
INDEX_CACHE = Path("/tmp/monkey-fdroid-index-v2.json")
INDEX_TTL_SEC = 24 * 3600


@dataclass
class FetchResult:
    job: AppJob
    status: str   # "ok" | "skipped-exists" | "not-in-repo" | "failed"
    reason: str = ""
    path: Path | None = None


def _http_get_stream(url: str, dest: Path, timeout: float) -> None:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fh:  # noqa: S310
        shutil.copyfileobj(resp, fh, length=1024 * 1024)
    tmp.replace(dest)


def load_index(*, force_refresh: bool) -> dict:
    need_fetch = force_refresh or not INDEX_CACHE.exists()
    if not need_fetch:
        age = time.time() - INDEX_CACHE.stat().st_mtime
        if age > INDEX_TTL_SEC:
            need_fetch = True
    if need_fetch:
        print(f"fetching F-Droid index -> {INDEX_CACHE} ...", flush=True)
        _http_get_stream(FDROID_INDEX_URL, INDEX_CACHE, timeout=180.0)
    with INDEX_CACHE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _latest_file_name(pkg_entry: dict) -> str | None:
    """Return the highest-versionCode version's APK path (starts with '/')."""
    versions = pkg_entry.get("versions") or {}
    best_vc = -1
    best_name: str | None = None
    for v in versions.values():
        vc = (v.get("manifest") or {}).get("versionCode")
        file_info = v.get("file") or {}
        name = file_info.get("name")
        if isinstance(vc, int) and isinstance(name, str) and vc > best_vc:
            best_vc = vc
            best_name = name
    return best_name


def fetch_one(job: AppJob, apks_dir: Path, index: dict, *, force: bool) -> FetchResult:
    dest = apks_dir / f"{job.package_id}.apk"
    if dest.exists() and not force:
        return FetchResult(job=job, status="skipped-exists", path=dest)

    packages = index.get("packages") or {}
    entry = packages.get(job.package_id)
    if entry is None:
        return FetchResult(
            job=job,
            status="not-in-repo",
            reason="package_id not found in F-Droid index-v2 (check apps.csv)",
        )

    file_path = _latest_file_name(entry)
    if not file_path:
        return FetchResult(
            job=job,
            status="not-in-repo",
            reason="no published version with file.name",
        )

    url = f"{FDROID_REPO_BASE}{file_path}"
    try:
        _http_get_stream(url, dest, timeout=300.0)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return FetchResult(job=job, status="failed", reason=f"{type(exc).__name__}: {exc} ({url})")
    return FetchResult(job=job, status="ok", path=dest)


def write_missing_report(
    apks_dir: Path,
    playstore: list[AppJob],
    system: list[AppJob],
    not_in_repo: list[FetchResult],
    failed: list[FetchResult],
) -> None:
    lines: list[str] = []
    lines.append("# Missing APKs report")
    lines.append("")
    lines.append("`fetch_fdroid_apks.py` 가 자동으로 다운로드할 수 없는 앱 목록입니다.")
    lines.append("수동으로 APK 를 확보해 `apks/{package_id}.apk` 로 넣으면 `collect-batch` 가 사용합니다.")
    lines.append("")
    lines.append(f"## F-Droid 인덱스에 없음 ({len(not_in_repo)}개)")
    lines.append("")
    if not_in_repo:
        lines.append("apps.csv 의 `package_id` 가 F-Droid 퍼블리시된 패키지와 일치하지 않습니다. 정확한 package_id 로 수정 후 재실행하거나 수동 수집하세요.")
        lines.append("")
        for r in not_in_repo:
            lines.append(f"- `{r.job.package_id}` ({r.job.app_name}, {r.job.category}/{r.job.sub_category}) — {r.reason}")
    else:
        lines.append("없음")
    lines.append("")
    lines.append(f"## F-Droid 다운로드 실패 ({len(failed)}개)")
    lines.append("")
    if failed:
        for r in failed:
            lines.append(f"- `{r.job.package_id}` — {r.reason}")
    else:
        lines.append("없음")
    lines.append("")
    lines.append(f"## PlayStore 전용 ({len(playstore)}개) — 저작권/정책상 자동 수집 불가")
    lines.append("")
    for job in playstore:
        lines.append(f"- `{job.package_id}` ({job.app_name}, {job.category}/{job.sub_category}, priority={job.priority})")
    lines.append("")
    lines.append(f"## System 앱 ({len(system)}개) — system-image 에 포함, 별도 APK 불필요")
    lines.append("")
    for job in system:
        lines.append(f"- `{job.package_id}` ({job.app_name})")
    lines.append("")

    (apks_dir / "MISSING.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch F-Droid APKs into apks/")
    parser.add_argument("--apps-csv", default="apps.csv")
    parser.add_argument("--apks-dir", default="apks")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Re-download even if file exists")
    parser.add_argument("--refresh-index", action="store_true", help="Force re-download of F-Droid index")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)

    apks_dir = Path(args.apks_dir)
    apks_dir.mkdir(parents=True, exist_ok=True)

    catalog = AppCatalog.load(args.apps_csv)
    all_jobs = catalog.filter()
    fdroid = [j for j in all_jobs if j.source.strip().lower() == "f-droid"]
    playstore = [j for j in all_jobs if j.source.strip().lower() == "playstore"]
    system = [j for j in all_jobs if j.source.strip().lower() == "system"]

    if args.limit is not None:
        fdroid = fdroid[: args.limit]

    print(f"F-Droid: {len(fdroid)} apps to fetch into {apks_dir}/")
    print(f"PlayStore: {len(playstore)} apps — will be listed in MISSING.md")
    print(f"System: {len(system)} apps — listed in MISSING.md")

    index = load_index(force_refresh=args.refresh_index)

    results: list[FetchResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_one, job, apks_dir, index, force=args.force) for job in fdroid]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            r = fut.result()
            results.append(r)
            tag = {"ok": "OK  ", "skipped-exists": "SKIP", "not-in-repo": "NONE", "failed": "FAIL"}[r.status]
            extra = f" — {r.reason}" if r.status in ("failed", "not-in-repo") else ""
            print(f"[{i:>2}/{len(futures)}] {tag} {r.job.package_id}{extra}", flush=True)

    ok = [r for r in results if r.status == "ok"]
    skipped = [r for r in results if r.status == "skipped-exists"]
    not_in_repo = [r for r in results if r.status == "not-in-repo"]
    failed = [r for r in results if r.status == "failed"]

    write_missing_report(
        apks_dir,
        playstore=playstore,
        system=system,
        not_in_repo=not_in_repo,
        failed=failed,
    )

    print("")
    print("== summary ==")
    print(f"  downloaded : {len(ok)}")
    print(f"  already exists (skipped): {len(skipped)}")
    print(f"  not in F-Droid repo     : {len(not_in_repo)}")
    print(f"  network/other failures  : {len(failed)}")
    print(f"  missing report -> {apks_dir / 'MISSING.md'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
