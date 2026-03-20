"""CLI entry point for the collection pipeline."""

import argparse
import sys

from loguru import logger


def main() -> None:
    parser = argparse.ArgumentParser(description="Monkey-Collector GUI data collection pipeline")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run: single app
    run_parser = subparsers.add_parser("run", help="Collect data from a single app")
    run_parser.add_argument("--app", required=True, help="Target app package name")
    run_parser.add_argument("--events", type=int, default=100, help="Number of Smart Explorer steps")
    run_parser.add_argument("--seed", type=int, default=None, help="Random seed for Smart Explorer")
    run_parser.add_argument("--action-delay", type=int, default=None, help="Delay between actions (ms)")
    run_parser.add_argument("--config", default="configs/collection/default.yaml")

    # batch: multiple apps
    batch_parser = subparsers.add_parser("batch", help="Collect data from multiple apps")
    batch_parser.add_argument("--apps-config", default="configs/collection/apps.yaml")
    batch_parser.add_argument("--config", default="configs/collection/default.yaml")

    # annotate: run annotation pipeline
    annotate_parser = subparsers.add_parser("annotate", help="Run annotation pipeline")
    annotate_parser.add_argument("--session", help="Session ID to annotate (all if omitted)")
    annotate_parser.add_argument("--config", default="configs/collection/default.yaml")

    # pipeline: collect + annotate
    pipeline_parser = subparsers.add_parser("pipeline", help="Run full pipeline (collect + annotate)")
    pipeline_parser.add_argument("--apps-config", default="configs/collection/apps.yaml")
    pipeline_parser.add_argument("--config", default="configs/collection/default.yaml")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logger.info(f"Running command: {args.command}")

    if args.command == "run":
        from collection.orchestrator import CollectionOrchestrator, AppConfig

        orch = CollectionOrchestrator(args.config)

        # Override smart_monkey config from CLI args
        if args.seed is not None:
            orch.smart_explorer_cfg["seed"] = args.seed
        if args.action_delay is not None:
            orch.smart_explorer_cfg["action_delay_ms"] = args.action_delay

        app = AppConfig(package=args.app, name=args.app, max_events=args.events)
        result = orch.run_session(app)
        logger.info(f"Result: {result}")

    elif args.command == "batch":
        from collection.orchestrator import CollectionOrchestrator
        orch = CollectionOrchestrator(args.config)
        results = orch.run_batch(args.apps_config)
        for r in results:
            logger.info(f"  {r}")

    elif args.command == "annotate":
        from collection.format.converter import FormatConverter
        converter = FormatConverter(args.config)
        if args.session:
            converter.process_session(args.session)
        else:
            converter.process_all()

    elif args.command == "pipeline":
        from collection.orchestrator import CollectionOrchestrator
        from collection.format.converter import FormatConverter

        orch = CollectionOrchestrator(args.config)
        results = orch.run_batch(args.apps_config)

        converter = FormatConverter(args.config)
        for r in results:
            if r.success:
                converter.process_session(r.session_id)


if __name__ == "__main__":
    main()
