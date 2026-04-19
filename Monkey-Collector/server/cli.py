"""CLI entrypoint for monkey-collector."""

import argparse
import sys

from loguru import logger


def cmd_run(args: argparse.Namespace) -> None:
    """Run data collection with App+Server architecture."""
    from server.domain.activity_coverage import ActivityCoverageTracker
    from server.domain.cost_tracker import CostTracker
    from server.infra.device.adb import AdbClient
    from server.infra.network.server import CollectionServer
    from server.infra.storage.storage import DataWriter
    from server.pipeline.collector import Collector
    from server.pipeline.explorer import SmartExplorer
    from server.pipeline.text_generator import create_text_generator

    adb = AdbClient(device_serial=args.device)
    activity_tracker = ActivityCoverageTracker()
    cost_tracker = CostTracker()
    text_gen = create_text_generator(
        mode=args.input_mode, seed=args.seed, cost_tracker=cost_tracker,
    )
    explorer = SmartExplorer(
        adb,
        config={
            "seed": args.seed,
            "action_delay_ms": args.delay,
        },
        text_generator=text_gen,
    )
    server = CollectionServer(host="0.0.0.0", port=args.port)
    writer = DataWriter(base_dir=args.output)
    collector = Collector(
        adb=adb,
        explorer=explorer,
        server=server,
        writer=writer,
        max_steps=args.steps,
        action_delay=args.delay / 1000.0,
        activity_coverage_tracker=activity_tracker,
        cost_tracker=cost_tracker,
        text_generator=text_gen,
        new_session=args.new_session,
    )

    if args.single:
        session_id = collector.run(args.app)
        if session_id:
            logger.info(f"Session saved: {args.output}/{session_id}")
    else:
        session_ids = collector.run_multi(args.app)
        logger.info(f"All sessions complete ({len(session_ids)} total)")
        for sid in session_ids:
            logger.info(f"  {args.output}/{sid}")


def cmd_sweep(args: argparse.Namespace) -> None:
    """Sweep GUI data from multiple apps across AVDs sequentially (one AVD at a time)."""
    from pathlib import Path

    from server.domain.activity_coverage import ActivityCoverageTracker
    from server.domain.cost_tracker import CostTracker
    from server.infra.device.adb import AdbClient
    from server.infra.device.apk_installer import ApkInstaller, ApkResolver
    from server.infra.device.avd import AvdHandle, AvdPool
    from server.infra.network.server import CollectionServer
    from server.infra.storage.storage import DataWriter
    from server.pipeline.app_catalog import AppCatalog, AppJob
    from server.pipeline.collector import Collector
    from server.pipeline.explorer import SmartExplorer
    from server.pipeline.sweep import Sweep
    from server.pipeline.text_generator import create_text_generator

    catalog = AppCatalog.load(args.apps_csv)
    resolver = ApkResolver(args.apks_dir)

    avd_names = [n.strip() for n in args.avds.split(",") if n.strip()]
    if not avd_names:
        logger.error("--avds must list at least one AVD name")
        sys.exit(2)

    categories = _split_or_none(args.categories)
    priorities = _split_or_none(args.priorities)

    pool = AvdPool(
        avd_names=avd_names,
        host_port_base=args.host_port_base,
        boot_timeout=args.boot_timeout,
        headless=args.headless,
    )

    def installer_factory(handle: AvdHandle) -> ApkInstaller:
        adb = AdbClient(device_serial=handle.serial)
        return ApkInstaller(adb=adb, resolver=resolver)

    def collector_factory(handle: AvdHandle, job: AppJob, base_dir: Path) -> Collector:
        adb = AdbClient(device_serial=handle.serial)
        activity_tracker = ActivityCoverageTracker()
        cost_tracker = CostTracker()
        text_gen = create_text_generator(
            mode=args.input_mode, seed=args.seed, cost_tracker=cost_tracker,
        )
        explorer = SmartExplorer(
            adb,
            config={"seed": args.seed, "action_delay_ms": args.delay},
            text_generator=text_gen,
        )
        server = CollectionServer(host="0.0.0.0", port=handle.host_port)
        writer = DataWriter(base_dir=str(base_dir))
        return Collector(
            adb=adb,
            explorer=explorer,
            server=server,
            writer=writer,
            max_steps=args.steps,
            action_delay=args.delay / 1000.0,
            activity_coverage_tracker=activity_tracker,
            cost_tracker=cost_tracker,
            text_generator=text_gen,
        )

    sweep = Sweep(
        catalog=catalog,
        avd_pool=pool,
        installer_factory=installer_factory,
        collector_factory=collector_factory,
        output_dir=args.output,
        uninstall_after=args.uninstall_after,
    )

    results = sweep.run(
        categories=categories,
        priorities=priorities,
        dry_run=args.dry_run,
        force=args.force,
    )

    if args.dry_run:
        return

    succeeded = sum(1 for r in results if r.succeeded)
    skipped = sum(1 for r in results if r.skipped)
    failed = len(results) - succeeded - skipped
    logger.info(
        f"Sweep complete: {succeeded} succeeded, {skipped} skipped, {failed} failed "
        f"(total {len(results)})"
    )


def _split_or_none(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [p.strip() for p in raw.split(",") if p.strip()]
    return items or None


def cmd_reset(args: argparse.Namespace) -> None:
    """Delete collected session data by scope (all / categories / packages / apps-csv)."""
    from server.pipeline.reset import delete_targets, resolve_targets

    scope_flags = [
        bool(args.all),
        bool(args.categories),
        bool(args.packages),
        bool(args.apps_csv),
    ]
    if args.all and any(scope_flags[1:]):
        logger.error("--all is mutually exclusive with --categories/--packages/--apps-csv")
        sys.exit(2)
    if not any(scope_flags):
        logger.error("reset requires a scope: --all, --categories, --packages, or --apps-csv")
        sys.exit(2)

    targets = resolve_targets(
        output_dir=args.output,
        all_=args.all,
        categories=_split_or_none(args.categories),
        packages=_split_or_none(args.packages),
        apps_csv=args.apps_csv,
        priorities=_split_or_none(args.priorities),
    )

    if not targets:
        logger.info("No matching directories found; nothing to delete.")
        return

    logger.info(f"Reset scope resolved to {len(targets)} path(s):")
    for p in targets:
        logger.info(f"  {p}")

    if not args.yes and not args.dry_run:
        reply = input(f"Delete {len(targets)} path(s)? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            logger.info("Aborted.")
            return

    deleted = delete_targets(targets, dry_run=args.dry_run)
    if args.dry_run:
        logger.info(f"[dry-run] Would delete {len(targets)} path(s)")
    else:
        logger.info(f"Reset complete: deleted {deleted} path(s)")


def cmd_convert(args: argparse.Namespace) -> None:
    """Convert a single session to JSONL."""
    from server.export.converter import Converter

    converter = Converter(
        output_path=args.output,
        images_dir=args.images_dir,
    )
    count = converter.convert_session(args.session, args.label)
    logger.info(f"Generated {count} examples -> {args.output}")


def cmd_page_map(args: argparse.Namespace) -> None:
    """Build page map from a saved session."""
    import os

    from server.domain.page_graph import build_graph_from_session
    from server.export.graph_visualizer import visualize_session

    graph = build_graph_from_session(args.session, threshold=args.threshold)
    graph.save(os.path.join(args.session, "page_graph.json"))
    html = visualize_session(
        args.session, output_path=args.output, open_browser=not args.no_open,
    )
    logger.info(
        f"Page map: {len(graph.nodes)} pages, "
        f"{len(graph.edges)} transitions"
    )
    if html:
        logger.info(f"Visualization: {html}")


def cmd_page_map_all(args: argparse.Namespace) -> None:
    """Build page maps for all sessions in a directory."""
    import os

    from server.domain.page_graph import build_graph_from_session
    from server.export.graph_visualizer import visualize_session

    raw_dir = args.raw_dir
    if not os.path.isdir(raw_dir):
        logger.error(f"Directory not found: {raw_dir}")
        return

    total = 0
    for name in sorted(os.listdir(raw_dir)):
        session_dir = os.path.join(raw_dir, name)
        xml_dir = os.path.join(session_dir, "xml")
        if not os.path.isdir(xml_dir):
            continue
        graph = build_graph_from_session(session_dir, threshold=args.threshold)
        if graph.nodes:
            graph.save(os.path.join(session_dir, "page_graph.json"))
            visualize_session(session_dir, open_browser=False)
            total += 1
            logger.info(
                f"  {name}: {len(graph.nodes)} pages, "
                f"{len(graph.edges)} transitions"
            )

    logger.info(f"Built page maps for {total} sessions")


def cmd_regenerate(args: argparse.Namespace) -> None:
    """Regenerate all XML variants from raw XML files."""
    from server.infra.storage.storage import regenerate_xml_variants

    logger.info(f"Regenerating XML variants under: {args.raw_dir}")
    count = regenerate_xml_variants(args.raw_dir)
    logger.info(f"Regenerated {count} files total")


def cmd_convert_all(args: argparse.Namespace) -> None:
    """Convert all sessions in a directory to JSONL."""
    from server.export.converter import Converter

    converter = Converter(
        output_path=args.output,
        images_dir=args.images_dir,
    )
    total = converter.convert_all(args.raw_dir)
    logger.info(f"Generated {total} total examples -> {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monkey-Collector: Android GUI data collector"
    )
    sub = parser.add_subparsers(dest="command")

    # run (App+Server mode)
    p = sub.add_parser("run", help="Collect GUI data (App+Server mode)")
    p.add_argument("--app", default=None, help="Target app package (optional: received from client if omitted)")
    p.add_argument("--steps", type=int, default=100, help="Max steps per session")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--delay", type=int, default=1500, help="Action delay in ms")
    p.add_argument("--port", type=int, default=12345, help="TCP server port")
    p.add_argument("--output", default="data/raw", help="Output directory")
    p.add_argument("--device", default=None, help="ADB device serial")
    p.add_argument(
        "--input-mode",
        choices=["api", "random"],
        default="api",
        help="Input text generation mode: 'api' (LLM) or 'random' (hardcoded)",
    )
    p.add_argument(
        "--single",
        action="store_true",
        default=False,
        help="Single-session mode: stop server after one session (default: multi-session)",
    )
    p.add_argument(
        "--new-session",
        action="store_true",
        default=False,
        help="Delete existing session and start fresh (default: continue existing session for same app)",
    )

    # sweep (sequential AVD collection)
    p = sub.add_parser(
        "sweep",
        help="Sweep GUI data from multiple apps across AVDs sequentially",
    )
    p.add_argument("--apps-csv", default="apps.csv", help="Path to apps.csv catalog")
    p.add_argument(
        "--apks-dir",
        default="apks",
        help="Directory containing {package_id}.apk files",
    )
    p.add_argument(
        "--avds",
        required=True,
        help="Comma-separated names of pre-created AVDs (e.g. monkey-1,monkey-2)",
    )
    p.add_argument(
        "--categories",
        default=None,
        help="Comma-separated categories to include (omit for all)",
    )
    p.add_argument(
        "--priorities",
        default=None,
        help="Comma-separated priorities to include, e.g. High,Medium (omit for all)",
    )
    p.add_argument("--output", default="data/raw", help="Output base directory")
    p.add_argument("--steps", type=int, default=100, help="Max steps per app session")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--delay", type=int, default=1500, help="Action delay in ms")
    p.add_argument(
        "--input-mode",
        choices=["api", "random"],
        default="api",
        help="Input text generation mode",
    )
    p.add_argument(
        "--host-port-base",
        type=int,
        default=6000,
        help="TCP port for first AVD; each subsequent AVD uses base+i",
    )
    p.add_argument(
        "--boot-timeout",
        type=float,
        default=180.0,
        help="Per-AVD boot timeout (seconds)",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run emulators without GUI (-no-window -no-audio -no-boot-anim). Default: show window.",
    )
    p.add_argument(
        "--uninstall-after",
        action="store_true",
        default=False,
        help="Uninstall each app after its session (default: keep)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-collect apps even if their sessions are already marked complete",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the job plan only; do not start AVDs or collect",
    )

    # reset (delete collected data)
    p = sub.add_parser(
        "reset",
        help="Delete collected session data by scope (all / categories / packages)",
    )
    p.add_argument("--output", default="data/raw", help="Data root directory")
    p.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Wipe the entire output root (exclusive with other scope flags)",
    )
    p.add_argument(
        "--categories",
        default=None,
        help="Comma-separated categories to wipe",
    )
    p.add_argument(
        "--packages",
        default=None,
        help="Comma-separated package IDs to wipe (searched across categories)",
    )
    p.add_argument(
        "--apps-csv",
        default=None,
        help="Resolve packages via apps.csv filtered by --categories/--priorities",
    )
    p.add_argument(
        "--priorities",
        default=None,
        help="Comma-separated priorities (only used with --apps-csv)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print paths that would be deleted without deleting",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip interactive confirmation",
    )

    # convert
    p = sub.add_parser("convert", help="Convert session to JSONL")
    p.add_argument("--session", required=True, help="Session directory path")
    p.add_argument("--output", required=True, help="Output JSONL path")
    p.add_argument("--images-dir", required=True, help="Images output directory")
    p.add_argument("--label", type=int, default=1, help="Session label for image naming")

    # page-map
    p = sub.add_parser("page-map", help="Build page map from session data")
    p.add_argument("--session", required=True, help="Session directory path")
    p.add_argument(
        "--threshold", type=float, default=0.85,
        help="XML fingerprint similarity threshold (0.0-1.0)",
    )
    p.add_argument("--output", default=None, help="Output HTML path")
    p.add_argument("--no-open", action="store_true", help="Do not open browser")

    # page-map-all
    p = sub.add_parser("page-map-all", help="Build page maps for all sessions")
    p.add_argument("--raw-dir", default="data/raw", help="Raw sessions directory")
    p.add_argument(
        "--threshold", type=float, default=0.85,
        help="XML fingerprint similarity threshold (0.0-1.0)",
    )
    p.add_argument("--no-open", action="store_true", help="Do not open browser")

    # regenerate
    p = sub.add_parser("regenerate", help="Regenerate XML variants from raw XML")
    p.add_argument("--raw-dir", default="data/raw", help="Raw sessions directory")

    # convert-all
    p = sub.add_parser("convert-all", help="Convert all sessions to JSONL")
    p.add_argument("--raw-dir", default="data/raw", help="Raw sessions directory")
    p.add_argument("--output", required=True, help="Output JSONL path")
    p.add_argument("--images-dir", required=True, help="Images output directory")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        cmd_run(args)
    elif args.command == "sweep":
        cmd_sweep(args)
    elif args.command == "reset":
        cmd_reset(args)
    elif args.command == "convert":
        cmd_convert(args)
    elif args.command == "convert-all":
        cmd_convert_all(args)
    elif args.command == "regenerate":
        cmd_regenerate(args)
    elif args.command == "page-map":
        cmd_page_map(args)
    elif args.command == "page-map-all":
        cmd_page_map_all(args)


if __name__ == "__main__":
    main()
