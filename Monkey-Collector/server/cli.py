"""CLI entrypoint for monkey-collector."""

import argparse
import sys

from loguru import logger


def cmd_run(args: argparse.Namespace) -> None:
    """Run data collection with App+Server architecture."""
    from server.adb import AdbClient
    from server.explorer import SmartExplorer
    from server.server import CollectionServer
    from server.storage import DataWriter
    from server.collector import Collector

    adb = AdbClient(device_serial=args.device)
    explorer = SmartExplorer(
        adb,
        config={
            "seed": args.seed,
            "action_delay_ms": args.delay,
        },
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
    )

    session_id = collector.run(args.app)
    logger.info(f"Session saved: {args.output}/{session_id}")


def cmd_convert(args: argparse.Namespace) -> None:
    """Convert a single session to JSONL."""
    from server.converter import Converter

    converter = Converter(
        output_path=args.output,
        images_dir=args.images_dir,
    )
    count = converter.convert_session(args.session, args.label)
    logger.info(f"Generated {count} examples -> {args.output}")


def cmd_convert_all(args: argparse.Namespace) -> None:
    """Convert all sessions in a directory to JSONL."""
    from server.converter import Converter

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
    p.add_argument("--app", required=True, help="Target app package name")
    p.add_argument("--steps", type=int, default=100, help="Max steps per session")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--delay", type=int, default=1000, help="Action delay in ms")
    p.add_argument("--port", type=int, default=12345, help="TCP server port")
    p.add_argument("--output", default="data/raw", help="Output directory")
    p.add_argument("--device", default=None, help="ADB device serial")

    # convert
    p = sub.add_parser("convert", help="Convert session to JSONL")
    p.add_argument("--session", required=True, help="Session directory path")
    p.add_argument("--output", required=True, help="Output JSONL path")
    p.add_argument("--images-dir", required=True, help="Images output directory")
    p.add_argument("--label", type=int, default=1, help="Session label for image naming")

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


if __name__ == "__main__":
    main()
