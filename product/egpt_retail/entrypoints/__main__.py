"""``egpt-retail <command>`` -- the console script behind the image's ``ENTRYPOINT``.

``argparse``, not Typer: no new runtime dependency in the image, and ``egptctl`` (which owns Typer)
is a different program that talks HTTP to a running OS and deliberately does not import ``egpt``.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

__all__ = ["build_parser", "main"]

COMMANDS = ("api", "pipelines", "consolidator", "migrate")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="egpt-retail",
        description="Enterprise GPT retail runtime. One image; the argument picks the process.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    api = sub.add_parser(
        "api", help="Serve the API (EgptOS) without the scheduler poller."
    )
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=7777)

    pipelines = sub.add_parser(
        "pipelines", help="Serve the same app with the scheduler poller running."
    )
    pipelines.add_argument("--host", default="0.0.0.0")
    pipelines.add_argument("--port", type=int, default=7777)

    consolidator = sub.add_parser(
        "consolidator", help="Drain memory scopes for the configured customer."
    )
    consolidator.add_argument(
        "--once", action="store_true", help="Run one tick and exit instead of looping."
    )
    consolidator.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="Seconds between ticks in loop mode.",
    )

    sub.add_parser(
        "migrate",
        help="Run control-plane schema migrations to the latest version and exit.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "api":
        from egpt_retail.entrypoints.api import main as run_api

        return run_api(host=args.host, port=args.port)
    if args.command == "pipelines":
        from egpt_retail.entrypoints.pipelines import main as run_pipelines

        return run_pipelines(host=args.host, port=args.port)
    if args.command == "consolidator":
        from egpt_retail.entrypoints.consolidator import main as run_consolidator

        return run_consolidator(once=args.once, poll_interval=args.poll_interval)
    if args.command == "migrate":
        from egpt_retail.entrypoints.migrate import main as run_migrate

        return run_migrate()
    raise SystemExit(
        f"unknown command {args.command!r}"
    )  # pragma: no cover -- argparse refuses first


if __name__ == "__main__":
    sys.exit(main())
