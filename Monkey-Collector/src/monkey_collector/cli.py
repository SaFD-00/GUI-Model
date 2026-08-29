"""CLI entrypoint for monkey-collector.

Host-pull rebuild pending: every device-push subcommand (run, reset,
sync-installed, convert, convert-all, page-map, page-map-all, regenerate) was
torn down along with pipeline/, tcp_server.py, storage.py, xml/, and export/.
No subcommands are registered yet.
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monkey-Collector: Android GUI data collector"
    )
    parser.add_subparsers(dest="command")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
