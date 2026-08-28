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

# Milestones that are registered but not implemented yet. Keep this table and the
# docs (README.md / ARCHITECTURE.md / AGENTS.md) in sync.
UNIMPLEMENTED = {
    "sync-installed": "milestone 3 (exploration/device sync)",
    "run": "milestone 4 (collection loop)",
    "export": "milestone 5 (Stage-1 export)",
}


def _not_implemented(command: str) -> None:
    milestone = UNIMPLEMENTED[command]
    raise NotImplementedError(
        f"`atlas-collect {command}` is not implemented yet — it lands in {milestone}. "
        f"M1 (scaffold/catalog/config) and M2 (xml encoding) are done, but M2 is a "
        f"library with no CLI surface, so `atlas-collect catalog` is the one working "
        f"subcommand. See ARCHITECTURE.md for the milestone plan."
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
# Not implemented yet
# ---------------------------------------------------------------------------


def cmd_sync_installed(args: argparse.Namespace) -> int:
    """Refresh the ``installed`` column from ``adb pm list packages``."""
    _not_implemented("sync-installed")
    return 1  # unreachable


def cmd_run(args: argparse.Namespace) -> int:
    """Host-pull collection loop over one or more catalog apps."""
    _not_implemented("run")
    return 1  # unreachable


def cmd_export(args: argparse.Namespace) -> int:
    """Convert collected triples into the Stage-1 (NEXT_STATE_PREDICTION) jsonl."""
    _not_implemented("export")
    return 1  # unreachable


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

    # -- sync-installed (NOT IMPLEMENTED) -------------------------------------
    p_sync = sub.add_parser(
        "sync-installed",
        help="[NOT IMPLEMENTED - M3] Refresh catalog installed column from the device.",
    )
    p_sync.add_argument("--serial", default=None, help="Device serial (default: autodetect).")
    p_sync.add_argument("--dry-run", action="store_true", help="Report the diff, write nothing.")
    p_sync.set_defaults(func=cmd_sync_installed)

    # -- run (NOT IMPLEMENTED) ------------------------------------------------
    p_run = sub.add_parser(
        "run",
        help="[NOT IMPLEMENTED - M4] Run the host-pull collection loop.",
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
        "--include-auth",
        action="store_true",
        help="Also collect account_required apps (skipped by default).",
    )
    p_run.set_defaults(func=cmd_run)

    # -- export (NOT IMPLEMENTED) ---------------------------------------------
    p_export = sub.add_parser(
        "export",
        help="[NOT IMPLEMENTED - M5] Export collected triples as Stage-1 jsonl.",
    )
    p_export.add_argument("--out", default=None, help="Output directory (default: data/export).")
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
