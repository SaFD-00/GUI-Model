"""CLI entrypoint for atlas-collector (``atlas-collect``).

M1 (scaffold + catalog + config) and M2 (``xml/`` encoding + coordinate frame)
are done; M2 is a library with no CLI surface of its own, so ``catalog`` is
still the only subcommand that runs. ``sync-installed``, ``run`` and ``export``
are registered so the surface is visible in ``--help``, but their bodies raise
:class:`NotImplementedError` naming the milestone that will fill them in.

``main()`` resolves the run config BEFORE dispatching, for every subcommand
including ``catalog``. That is deliberate: ``--config`` naming a missing or
unparseable file must fail loudly here rather than be discovered three
milestones later. A flag that silently ignores a typo'd path is worse than no
flag at all. The resolved :class:`~atlas_collector.config.RunConfig` is attached
to the parsed namespace as ``args.run_config`` so M3-M5 commands can consume it
without re-reading the file.

Command imports are kept local so that ``atlas-collect catalog`` touches only
the stdlib plus pyyaml (via ``config``) — never openai or pillow.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # imported for typing only; the runtime imports stay local
    from atlas_collector.adb import AdbClient

# Milestones that are registered but not implemented yet. Keep this table and the
# docs (README.md / ARCHITECTURE.md / AGENTS.md) in sync.
#: Subcommands still to come. Empty since M5 — kept so `_not_implemented` has a
#: single place to grow from rather than being reinvented per command.
UNIMPLEMENTED: dict[str, str] = {}


def _not_implemented(command: str) -> None:
    milestone = UNIMPLEMENTED[command]
    raise NotImplementedError(
        f"`atlas-collect {command}` is not implemented yet — it lands in {milestone}. "
        f"M1-M4 are done: catalog, sync-installed, provision and run all work. "
        f"See ARCHITECTURE.md for the milestone plan."
    )


# ---------------------------------------------------------------------------
# catalog — the one working subcommand
# ---------------------------------------------------------------------------


def _fmt_table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]
    return "\n".join([line, sep, *body])


def cmd_catalog(args: argparse.Namespace) -> int:
    """List / summarise ``catalog/apps.csv``."""
    from atlas_collector.catalog import catalog_stats, filter_rows, load_catalog

    rows = load_catalog(args.catalog)
    installed = None if args.installed is None else args.installed == "true"
    selected = filter_rows(
        rows,
        tier=args.tier,
        installed=installed,
        auth=args.auth,
        status=args.status,
        include_auth=not args.exclude_auth,
    )

    if args.stats:
        stats = catalog_stats(selected)
        print(f"catalog: {len(rows)} rows total, {stats['total']} selected")
        print(f"  installed      : {stats['installed']} (not installed: {stats['not_installed']})")
        print(f"  device_verified: {stats['device_verified']}")
        print(
            f"  collectable    : {stats['collectable']}"
            "  (installed AND package resolved AND not excluded)"
        )
        print(f"  excluded       : {stats['excluded']}  (status == excluded; never collected)")
        print(f"  pending        : {stats['pending']}  (package_id == PENDING)")
        for label in ("tier", "auth_required", "status", "category"):
            entries = stats[label]
            assert isinstance(entries, dict)
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(entries.items()))
            print(f"  {label:<15}: {rendered}")
        return 0

    if not selected:
        print("No catalog rows match the given filters.", file=sys.stderr)
        return 1

    headers = ["tier", "package_id", "app_name", "auth_required", "installed", "status"]
    table = [
        [r.tier, r.package_id, r.app_name, r.auth_required, r.installed, r.status]
        for r in selected
    ]
    print(_fmt_table(table, headers))
    print(f"\n{len(selected)} / {len(rows)} rows")
    return 0


# ---------------------------------------------------------------------------
# sync-installed / provision — device-facing catalog maintenance
# ---------------------------------------------------------------------------


def _device_packages(args: argparse.Namespace, command: str) -> tuple[AdbClient, set[str] | None]:
    """Open the device and read ``pm list packages``.

    Returns ``(client, packages)``; *packages* is None when adb failed, which the
    caller turns into a non-zero exit. Both commands start here because both of
    them treat the device — not the catalog — as the authority on what is
    installed, and neither has anything meaningful to do without it.
    """
    from atlas_collector.adb import AdbClient, AdbError

    client = AdbClient(serial=args.serial or args.run_config.device.serial)
    try:
        return client, set(client.list_packages())
    except AdbError as exc:
        print(f"atlas-collect {command}: {exc}", file=sys.stderr)
        return client, None


def cmd_sync_installed(args: argparse.Namespace) -> int:
    """Refresh the catalog's ``installed`` column from the device.

    That column and no other: ``status`` is the curation verdict and survives
    every sync untouched. See ``provision.sync_installed_rows`` and the
    ``catalog`` module docstring for why conflating the two loses data silently.
    """
    from atlas_collector.catalog import load_catalog, write_catalog
    from atlas_collector.provision import sync_installed_rows

    rows = load_catalog(args.catalog)
    client, device_packages = _device_packages(args, "sync-installed")
    if device_packages is None:
        return 1

    updated, flips = sync_installed_rows(rows, device_packages)
    before = sum(1 for r in rows if r.is_installed)
    after = sum(1 for r in updated if r.is_installed)
    print(f"device {client.serial}: {len(device_packages)} packages present")
    print(f"catalog: {len(rows)} rows — installed {before} -> {after}")
    if flips:
        gained = sum(1 for f in flips if f.after == "true")
        lost = len(flips) - gained
        print(f"  {len(flips)} row(s) flipped ({gained} false->true, {lost} true->false)")
        for flip in flips:
            print(f"    {flip.direction:<14} {flip.package_id}  ({flip.app_name})")
    else:
        print("  0 rows flipped — the catalog already matches the device")
    print("  status column: untouched (curation verdict, not a device fact)")

    if args.dry_run:
        print("dry-run: catalog NOT written")
        return 0
    print(f"wrote {write_catalog(updated, args.catalog)}")
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    """Resolve and install the APKs a collection run needs."""
    from pathlib import Path

    from atlas_collector import provision as prov
    from atlas_collector.catalog import load_catalog

    rows = load_catalog(args.catalog)
    client, device_packages = _device_packages(args, "provision")
    if device_packages is None:
        return 1

    ledger_file = Path(args.ledger) if args.ledger else prov.ledger_path()
    # Loaded BEFORE anything is installed: a corrupt ledger discovered afterwards
    # would mean a run whose outcome cannot be recorded anywhere.
    try:
        ledger = prov.load_ledger(ledger_file)
    except prov.LedgerError as exc:
        print(f"atlas-collect provision: {exc}", file=sys.stderr)
        return 1

    plan = prov.plan_provision(rows, device_packages, only=args.only, force=args.force)
    for package_id in plan.unknown:
        print(f"  ?       {package_id}: no such row in the catalog", file=sys.stderr)
    for row, reason in plan.skipped:
        print(f"  skip    {row.package_id}  ({reason})")
    print(f"{len(plan.targets)} package(s) to provision")

    cache_dir = Path(args.apk_cache) if args.apk_cache else prov.apk_cache_dir()
    aw_dir = Path(args.android_world) if args.android_world else prov.android_world_dir()

    failures: dict[str, tuple[str, str]] = {}
    # Already-installed skips count as resolved: they clear any stale ledger
    # entry, because the ledger records "not held", not "last attempt failed".
    present: set[str] = {r.package_id for r, why in plan.skipped if why == prov.SKIP_INSTALLED}
    for row in plan.targets:
        resolution = prov.resolve_apk(
            row.package_id,
            cache_dir=cache_dir,
            aw_dir=aw_dir,
            dest_dir=prov.download_dir(),
            allow_download=not args.no_download,
        )
        if resolution.path is None:
            print(f"  MISSING {row.package_id}: {resolution.reason}")
            failures[row.package_id] = ("unresolved", resolution.reason)
            continue
        label = f"{resolution.source}:{resolution.path.name}"
        if args.dry_run:
            print(f"  plan    {row.package_id}  <- {label}")
            continue
        # `already_present` is only ever True on the --force path. It tells
        # install_apk that a post-install re-query cannot prove anything here,
        # because the package was on the device before the install even ran.
        ok, note = prov.install_apk(
            client,
            row.package_id,
            resolution.path,
            already_present=row.package_id in device_packages,
        )
        if ok:
            present.add(row.package_id)
            print(f"  OK      {row.package_id}  <- {label}" + (f"  [{note}]" if note else ""))
        else:
            failures[row.package_id] = (resolution.source, note)
            print(f"  FAILED  {row.package_id}  <- {label}: {note}")

    if args.dry_run:
        print(f"dry-run: nothing installed, {ledger_file.name} not written")
        return 1 if failures else 0

    written = prov.write_ledger(
        prov.update_ledger(
            ledger, failures=failures, resolved=present, scope_ids=plan.scope_ids
        ),
        ledger_file,
    )
    print(f"{len(present)} present, {len(failures)} failed — ledger: {written}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Not implemented yet
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    """Host-pull collection loop over one or more catalog apps."""
    from atlas_collector.adb import AdbClient
    from atlas_collector.catalog import load_catalog
    from atlas_collector.coverage import ActivityCoverage
    from atlas_collector.explore import Explorer
    from atlas_collector.loop import CollectionLoop
    from atlas_collector.pagematch import MergePolicy, PageRegistry
    from atlas_collector.session import Session

    config = args.run_config
    collection = config.collection
    matching = config.page_matching

    rows = load_catalog()
    wanted = set(args.apps) - {"all"}
    targets = [
        row
        for row in rows
        if row.is_collectable and (not wanted or row.package_id in wanted)
    ]
    if not args.include_auth:
        skipped = [r for r in targets if r.needs_auth]
        targets = [r for r in targets if not r.needs_auth]
        if skipped:
            print(
                f"skipping {len(skipped)} account_required app(s); "
                f"pass --include-auth to include them"
            )
    if not targets:
        print("no collectable apps matched — nothing to do")
        return 0

    budget_mode = args.budget_mode or collection.budget_mode
    if args.max_duration:
        from atlas_collector.config import parse_duration

        duration = float(parse_duration(args.max_duration))
    else:
        duration = float(collection.max_duration_sec)
    max_steps = args.max_steps if args.max_steps else collection.max_steps
    # The inactive budget must not also stop the run: `time` means time.
    if budget_mode == "time":
        max_steps = 0
    else:
        duration = float("inf")

    adb = AdbClient(serial=args.serial or config.device.serial)
    print(f"device {adb.serial}: {len(targets)} app(s), budget {budget_mode}")

    failures = 0
    for position, row in enumerate(targets, start=1):
        session = Session(
            row.package_id, args.data_dir, args.runtime_dir, episode=row.package_id
        )
        if session.is_complete and not args.force:
            print(f"[{position}/{len(targets)}] {row.package_id}: already complete, skipping")
            continue

        session.open(resume=not args.force)
        declared = _declared_activities(adb, row.package_id)
        coverage = ActivityCoverage(
            package=row.package_id,
            declared=declared,
            source="dumpsys (incomplete — resolver table only)",
            # ALWAYS dynamic with this source. dumpsys cannot see an activity
            # that has no intent filter, so a fixed denominator built from it
            # scores a perfectly healthy run at 0%. A fixed denominator is only
            # honest when it comes from the manifest.
            allow_dynamic_total=True,
        )
        coverage.open(session.runtime / "activity_coverage.csv")

        loop = CollectionLoop(
            adb,
            session,
            registry=PageRegistry(
                policy=MergePolicy(matching.merge_policy),
                max_diff_elements=matching.max_diff_elements,
                same_activity_only=matching.same_activity_only,
            ),
            explorer=Explorer(),
            coverage=coverage,
            max_duration_sec=duration,
            max_steps=max_steps,
            action_delay_ms=collection.action_delay_ms,
            stabilize={
                "max_wait_sec": collection.stabilize_max_wait_sec,
                "poll_ms": collection.stabilize_poll_ms,
                "pixel_threshold": collection.stabilize_pixel_threshold,
                "luma_delta": collection.stabilize_luma_delta,
                "low_res_width": collection.stabilize_low_res_width,
            },
        )
        print(f"[{position}/{len(targets)}] {row.app_name} ({row.package_id})")
        try:
            stats = loop.run()
        except KeyboardInterrupt:
            # Metadata is written by run()'s own exit path only on a clean end,
            # so record the interruption here — otherwise the session looks
            # untouched and a resume would restart its numbering.
            session.write_metadata(completed=False, extra={"stop_reason": "interrupted"})
            print("interrupted — session left resumable")
            raise
        except Exception as error:  # noqa: BLE001 - one bad app must not end the sweep
            failures += 1
            session.write_metadata(completed=False, extra={"stop_reason": f"error: {error}"})
            print(f"    FAILED: {error}")
            continue

        coverage.log_summary()
        print(
            f"    {stats.triples} triples / {stats.observations} observations / "
            f"{stats.pages} pages / {coverage.unique_visited}"
            f"{'/' + str(coverage.total) if coverage.total else ''} activities "
            f"— {stats.stop_reason}"
        )
    return 1 if failures else 0


def _declared_activities(adb: object, package: str) -> set[str]:
    """Activities `dumpsys` will admit to, scraped from the Activity Resolver Table.

    THIS IS STRUCTURALLY INCOMPLETE and the caller must treat it as such. The
    resolver table lists only activities carrying an intent filter; one launched
    internally has no filter and never appears. Measured on the target device,
    Markor declares 11 components here — none of which is the `IntroActivity`
    that was actually on screen for the whole session. Scoring against it as a
    fixed denominator reported 0% while the app ran perfectly.

    So this returns a HINT, not ground truth, and `cmd_run` pairs it with
    `allow_dynamic_total=True`. A fixed denominator needs the APK manifest
    (androguard, cached in `catalog/activities.json`), which is the only source
    that is complete and stable across devices.

    Scoped to the resolver table rather than the whole dump because the whole
    dump also yields providers and receivers — `androidx.startup.
    InitializationProvider` is not an Activity, and counting it inflates the
    denominator with things no exploration can ever reach.
    """
    import re

    try:
        out = adb.shell(f"dumpsys package {package}")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - an unmeasurable denominator is not fatal
        return set()
    start = out.find("Activity Resolver Table:")
    if start < 0:
        return set()
    end = out.find("Receiver Resolver Table:", start)
    section = out[start : end if end > 0 else len(out)]
    return set(re.findall(rf"({re.escape(package)}/[\w.$]+)", section))


def cmd_export(args: argparse.Namespace) -> int:
    """Convert collected triples into the Stage-1 (NEXT_STATE_PREDICTION) jsonl."""
    from atlas_collector.export import Exporter

    config = args.run_config
    exporter = Exporter(
        args.data_dir,
        args.runtime_dir,
        args.out_dir,
        frame=tuple(config.export.target_size),
        device_size=(config.device.width, config.device.height),
        ood_apps=args.ood_apps if args.ood_apps is not None else config.export.ood_apps,
        id_ratio=args.id_ratio if args.id_ratio is not None else config.export.id_ratio,
        seed=args.seed if args.seed is not None else config.collection.seed,
        keep_unchanged=args.keep_unchanged,
    )
    stats = exporter.run()
    if not stats.total_written:
        print("nothing exported — no collected session produced a usable triple")
        return 1

    print(f"{stats.apps} app(s), {stats.triples_seen} triples seen")
    from atlas_collector.export import SPLIT_ID, SPLIT_OOD, SPLIT_TRAIN

    for split in (SPLIT_TRAIN, SPLIT_ID, SPLIT_OOD):
        print(f"  stage1_{split}.jsonl : {stats.written.get(split, 0)}")
    print(
        f"  dropped: {stats.dropped_unchanged} unchanged, "
        f"{stats.dropped_unparsable} unparsable, "
        f"{stats.dropped_missing_files} missing files"
    )
    if stats.ood_apps:
        print(f"  held out for OOD: {', '.join(stats.ood_apps)}")
    print(f"  -> {args.out_dir}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-collect",
        description=(
            "Atlas-Collector — host-pull Android GUI data collector producing Stage-1 "
            "world-modeling triples. M1+M2 are done; `catalog` is the only subcommand "
            "that runs (M2 ships as a library with no CLI surface)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Milestones: M1 scaffold (DONE) | M2 xml encoding + coordinate frame (DONE) | "
            "M3 exploration | M4 collection loop | M5 Stage-1 export.\n"
            "Registered-but-unimplemented subcommands raise NotImplementedError."
        ),
    )
    from atlas_collector import __version__
    from atlas_collector.catalog import VALID_STATUSES

    parser.add_argument("--version", action="version", version=f"atlas-collector {__version__}")
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to run.yaml. Resolved before any subcommand runs; a missing or "
            "unparseable file named here is an error (exit 2). Omitted: config/run.yaml "
            "is used when present, builtin defaults when it is absent."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- catalog (IMPLEMENTED) ------------------------------------------------
    p_catalog = sub.add_parser(
        "catalog",
        help="List or summarise the app catalog (IMPLEMENTED).",
        description="Read catalog/apps.csv and list or summarise its rows.",
    )
    p_catalog.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_catalog.add_argument(
        "--tier",
        choices=["androidworld_fdroid", "fdroid", "playstore"],
        help="Only rows in this tier.",
    )
    p_catalog.add_argument(
        "--installed",
        choices=["true", "false"],
        help="Only rows whose installed column has this value.",
    )
    p_catalog.add_argument(
        "--auth",
        choices=["none", "account_optional", "account_required"],
        help="Only rows with this auth_required value.",
    )
    p_catalog.add_argument(
        "--status",
        choices=sorted(VALID_STATUSES),
        help=(
            "Only rows with this catalog-curation status. Independent of --installed, "
            "which is the device-measured fact — see catalog.py for why they differ."
        ),
    )
    p_catalog.add_argument(
        "--exclude-auth",
        action="store_true",
        help="Drop account_required rows (the runner's default posture).",
    )
    p_catalog.add_argument("--stats", action="store_true", help="Print a summary instead of rows.")
    p_catalog.set_defaults(func=cmd_catalog)

    # -- sync-installed (IMPLEMENTED) -----------------------------------------
    p_sync = sub.add_parser(
        "sync-installed",
        help="Refresh the catalog's installed column from the device (IMPLEMENTED).",
        description=(
            "Read `adb pm list packages` and rewrite catalog/apps.csv's `installed` "
            "column to match. ONLY that column: `status` is the human curation verdict "
            "and is never derived from the device."
        ),
    )
    p_sync.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_sync.add_argument("--serial", default=None, help="Device serial (default: autodetect).")
    p_sync.add_argument("--dry-run", action="store_true", help="Report the diff, write nothing.")
    p_sync.set_defaults(func=cmd_sync_installed)

    # -- provision (IMPLEMENTED) ----------------------------------------------
    p_prov = sub.add_parser(
        "provision",
        help="Install the APKs a collection run needs (IMPLEMENTED).",
        description=(
            "Resolve each target APK through Monkey-Collector's cache, then AndroidWorld's "
            "version-pinned APKs, then f-droid.org, install it, and VERIFY by re-querying "
            "the device. Failures accumulate in catalog/PROVISION_MISSING.json."
        ),
    )
    p_prov.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_prov.add_argument("--serial", default=None, help="Device serial (default: autodetect).")
    p_prov.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="PKG",
        help="Limit the run to these package ids. Out-of-scope ledger records are kept.",
    )
    p_prov.add_argument(
        "--force",
        action="store_true",
        help="Reinstall rows already present on the device. Never overrides status=excluded.",
    )
    p_prov.add_argument(
        "--dry-run", action="store_true", help="Resolve and report; install nothing."
    )
    p_prov.add_argument(
        "--no-download",
        action="store_true",
        help="Local sources only — do not reach out to f-droid.org.",
    )
    p_prov.add_argument(
        "--apk-cache", default=None, help="Override the Monkey-Collector APK cache directory."
    )
    p_prov.add_argument(
        "--android-world", default=None, help="Override AndroidWorld's pinned-APK directory."
    )
    p_prov.add_argument(
        "--ledger",
        default=None,
        help="Failure ledger path (default: catalog/PROVISION_MISSING.json).",
    )
    p_prov.set_defaults(func=cmd_provision)

    # -- run (NOT IMPLEMENTED) ------------------------------------------------
    p_run = sub.add_parser(
        "run",
        help="Run the host-pull collection loop over the catalog.",
    )
    p_run.add_argument("--apps", nargs="+", default=["all"], help="Package ids, or 'all'.")
    p_run.add_argument("--serial", default=None, help="Device serial (default: autodetect).")
    p_run.add_argument(
        "--budget-mode",
        choices=["steps", "time"],
        default=None,
        help="Override collection.budget_mode (default: time).",
    )
    p_run.add_argument(
        "--max-duration",
        default=None,
        metavar="DURATION",
        help='Override collection.max_duration, e.g. "2h" / "120m" / "7200s". '
        "Used when budget_mode is time.",
    )
    p_run.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Override collection.max_steps. Used when budget_mode is steps.",
    )
    p_run.add_argument("--seed", type=int, default=None, help="Override the explorer seed.")
    p_run.add_argument(
        "--input-mode",
        choices=["api", "random"],
        default=None,
        help="Input-text generation mode. The LLM is used for input text ONLY.",
    )
    p_run.add_argument(
        "--data-dir", default="data/raw", help="Persistent corpus root (default: data/raw)."
    )
    p_run.add_argument(
        "--runtime-dir", default="runtime", help="Volatile run-state root (default: runtime)."
    )
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Re-collect apps already marked complete, restarting their numbering.",
    )
    p_run.add_argument(
        "--include-auth",
        action="store_true",
        help="Also collect account_required apps (skipped by default).",
    )
    p_run.set_defaults(func=cmd_run)

    # -- export (NOT IMPLEMENTED) ---------------------------------------------
    p_export = sub.add_parser(
        "export",
        help="Export collected triples as EXP08 Stage-1 jsonl.",
    )
    p_export.add_argument(
        "--out-dir",
        default="data/AtlasCollection",
        help=(
            "Output directory (default: data/AtlasCollection). Named to sit "
            "alongside data/MonkeyCollection, which is where the training "
            "pipeline reads the sibling collector's corpus from."
        ),
    )
    p_export.add_argument(
        "--data-dir", default="data/raw", help="Collected corpus root (default: data/raw)."
    )
    p_export.add_argument(
        "--runtime-dir", default="runtime", help="Volatile run-state root (default: runtime)."
    )
    p_export.add_argument("--seed", type=int, default=None, help="Split seed.")
    p_export.add_argument(
        "--keep-unchanged",
        action="store_true",
        help="Export triples where the screen did not change (dropped by default).",
    )
    p_export.add_argument(
        "--ood-apps",
        type=float,
        default=None,
        help="Fraction of APPS held out entirely from train (OOD eval).",
    )
    p_export.add_argument(
        "--id-ratio",
        type=float,
        default=None,
        help="Fraction of the SEEN apps' triples reserved as ID eval.",
    )
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse argv, resolve the run config, dispatch. Returns the process exit code.

    Exit codes: 0 success, 1 no subcommand / empty result set, 2 bad config.
    """
    from atlas_collector.config import ConfigError, load_run_config

    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve config BEFORE dispatch, for every subcommand. `--config` pointing at a
    # missing or unparseable file must fail here and now — see the module docstring.
    try:
        args.run_config = load_run_config(args.config)
    except ConfigError as exc:
        print(f"atlas-collect: {exc}", file=sys.stderr)
        return 2

    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
