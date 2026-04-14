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
