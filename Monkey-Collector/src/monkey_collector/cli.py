"""CLI entrypoint for monkey-collector (``monkey-collect``).

Host-pull rebuild: the foundation (``adb.py``, ``paths.py``, ``xml/``,
``pagematch.py``, ``stabilize.py``, ``session.py``, ``catalog.py``,
``provision.py``) is done. Four subcommands are wired here because their
implementations already exist: ``catalog``, ``sync-installed``, ``provision``
and ``reset``.

``run`` (the host-pull collection loop) and ``export`` (the Stage-1 jsonl
export) do NOT exist yet. Neither is registered as a subcommand — not even as
a placeholder that raises ``NotImplementedError`` — because a registered
subcommand that only ever raises makes ``--help`` describe capabilities the
tool does not have.

``main()`` resolves the run config BEFORE dispatching, for every subcommand
including ``catalog``. That is deliberate: ``--config`` naming a missing or
unparseable file must fail loudly here rather than be discovered later. The
resolved :class:`~monkey_collector.config.RunConfig` is attached to the parsed
namespace as ``args.run_config`` so a subcommand can consume it without
re-reading the file.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from monkey_collector.paths import (
    DEFAULT_ROOT,
    collection_root,
    export_root,
    raw_root,
    runtime_root,
)

if TYPE_CHECKING:  # imported for typing only; the runtime imports stay local
    from monkey_collector.adb import AdbClient

# ---------------------------------------------------------------------------
# catalog
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
    from monkey_collector.catalog import catalog_stats, filter_rows, load_catalog

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
    from monkey_collector.adb import AdbClient, AdbError

    client = AdbClient(serial=args.serial or args.run_config.device.serial)
    try:
        return client, set(client.list_packages())
    except AdbError as exc:
        print(f"monkey-collect {command}: {exc}", file=sys.stderr)
        return client, None


def cmd_sync_installed(args: argparse.Namespace) -> int:
    """Refresh the catalog's ``installed`` column from the device.

    That column and no other: ``status`` is the curation verdict and survives
    every sync untouched. See ``provision.sync_installed_rows`` and the
    ``catalog`` module docstring for why conflating the two loses data silently.
    """
    from monkey_collector.catalog import load_catalog, write_catalog
    from monkey_collector.provision import sync_installed_rows

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
    from monkey_collector import provision as prov
    from monkey_collector.catalog import load_catalog

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
        print(f"monkey-collect provision: {exc}", file=sys.stderr)
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
# reset — one root, one removal
# ---------------------------------------------------------------------------


def cmd_reset(args: argparse.Namespace) -> int:
    """Delete a collection root's artifacts so the next run starts clean.

    Exists because "throw the pilot away before the real run" must be one
    reliable action. A leftover ``raw/`` beside a fresh export is
    indistinguishable from a consistent one, and a resumed session would
    continue the OLD observation numbering — older triples would then reference
    newer screens with nothing in the data to flag it.

    ``run.log`` is deliberately NOT deleted: the data can be re-collected, the
    record of what went wrong while collecting it cannot.
    """
    root = collection_root(args.root)
    if not root.exists():
        print(f"{root} does not exist — nothing to reset")
        return 0

    scopes: list[tuple[str, list[Path]]] = []
    if args.raw or args.all:
        scopes.append(("raw", [raw_root(args.root)]))
    if args.runtime or args.all:
        scopes.append(("runtime", [runtime_root(args.root)]))
    if args.export or args.all:
        exports = sorted(export_root(args.root).glob("stage1_*.jsonl"))
        exports += [p for p in (export_root(args.root) / "images",) if p.exists()]
        exports += [p for p in (export_root(args.root) / "export_meta.json",) if p.exists()]
        scopes.append(("export", exports))
    if not scopes:
        print("pick a scope: --raw / --runtime / --export / --all")
        return 2

    targets = [(name, p) for name, paths in scopes for p in paths if p.exists()]
    if not targets:
        print(f"{root}: nothing to delete in the selected scope(s)")
        return 0

    for name, path in targets:
        size = (
            sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            if path.is_dir()
            else path.stat().st_size
        )
        print(f"  {name:<8} {path}  ({size / 1e6:.1f} MB)")
    if args.dry_run:
        print("dry-run: nothing deleted")
        return 0

    for _, path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    print(f"deleted {len(targets)} path(s) under {root}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monkey-collect",
        description=(
            "Monkey-Collector — host-pull Android GUI data collector. "
            "`catalog`, `sync-installed`, `provision` and `reset` are implemented "
            "against the finished host-pull foundation (adb.py, paths.py, xml/, "
            "pagematch.py, stabilize.py, session.py, catalog.py, provision.py)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Implemented: catalog, sync-installed, provision, reset.\n"
            "NOT implemented yet: `run` (the collection loop) and `export` (the "
            "Stage-1 jsonl export). Neither is registered as a subcommand."
        ),
    )
    from monkey_collector import __version__
    from monkey_collector.catalog import VALID_STATUSES

    parser.add_argument("--version", action="version", version=f"monkey-collector {__version__}")
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

    # -- catalog ---------------------------------------------------------------
    p_catalog = sub.add_parser(
        "catalog",
        help="List or summarise the app catalog.",
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

    # -- sync-installed ----------------------------------------------------------
    p_sync = sub.add_parser(
        "sync-installed",
        help="Refresh the catalog's installed column from the device.",
        description=(
            "Read `adb pm list packages` and rewrite catalog/apps.csv's `installed` "
            "column to match. ONLY that column: `status` is the human curation verdict "
            "and is never derived from the device."
        ),
    )
    p_sync.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_sync.add_argument("--serial", default=None, help="Device serial (default: config device.serial).")
    p_sync.add_argument("--dry-run", action="store_true", help="Report the diff, write nothing.")
    p_sync.set_defaults(func=cmd_sync_installed)

    # -- provision ----------------------------------------------------------
    p_prov = sub.add_parser(
        "provision",
        help="Install the APKs a collection run needs.",
        description=(
            "Resolve each target APK through Monkey-Collector's own cache, then "
            "AndroidWorld's version-pinned APKs, then f-droid.org, install it, and "
            "VERIFY by re-querying the device. Failures accumulate in "
            "catalog/PROVISION_MISSING.json."
        ),
    )
    p_prov.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_prov.add_argument("--serial", default=None, help="Device serial (default: config device.serial).")
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
        "--apk-cache", default=None, help="Override the local APK cache directory."
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

    # -- reset ----------------------------------------------------------------
    p_reset = sub.add_parser(
        "reset",
        help="Delete a collection root's artifacts so the next run starts clean.",
    )
    p_reset.add_argument("--root", default=DEFAULT_ROOT, help=f"Collection root (default: {DEFAULT_ROOT}).")
    p_reset.add_argument("--raw", action="store_true", help="Delete raw/ (the collected corpus).")
    p_reset.add_argument("--runtime", action="store_true", help="Delete runtime/ (session state).")
    p_reset.add_argument("--export", action="store_true", help="Delete the Stage-1 jsonl + images.")
    p_reset.add_argument("--all", action="store_true", help="Delete every artifact under the root.")
    p_reset.add_argument("--dry-run", action="store_true", help="List what would go, delete nothing.")
    p_reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse argv, resolve the run config, dispatch. Returns the process exit code.

    Exit codes: 0 success, 1 no subcommand / empty result set, 2 bad config.
    """
    from monkey_collector.config import ConfigError, load_run_config

    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve config BEFORE dispatch, for every subcommand. `--config` pointing at a
    # missing or unparseable file must fail here and now — see the module docstring.
    try:
        args.run_config = load_run_config(args.config)
    except ConfigError as exc:
        print(f"monkey-collect: {exc}", file=sys.stderr)
        return 2

    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
