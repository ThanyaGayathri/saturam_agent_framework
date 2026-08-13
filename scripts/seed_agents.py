#!/usr/bin/env python3
"""Seed template component definitions into a tenant (EGPT-2.8, aicoe-tracker#60).

Loads every ``*.json`` definition bundle in a directory into one tenant's registry. Safe to
re-run: a component that is absent is created, one whose definition changed gets a new version,
and one that already matches is left completely alone -- no row written and no audit record.

    ./scripts/seed_agents.py --db-file ./egpt.db --tenant-id acme
    ./scripts/seed_agents.py --db-url postgresql+psycopg://ai:ai@localhost:5532/ai \\
        --tenant-id acme --templates-dir ./my-templates

``component_id`` is a *global* primary key, not a per-tenant one, so the same bundle cannot be
seeded verbatim into two tenants on one database. Give the second tenant its own ids:

    ./scripts/seed_agents.py --db-file ./egpt.db --tenant-id globex --id-prefix globex-

Exit codes: 0 -- everything created, versioned or already up to date. 1 -- one or more
components failed. 2 -- usage error, or the framework/driver could not be imported.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

# Column widths for the human-readable report. `action` is the widest word ("metadata_updated").
_ACTION_WIDTH = 16
_TYPE_WIDTH = 9
_ID_WIDTH = 32


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--db-url", help="SQLAlchemy URL, e.g. postgresql+psycopg://ai:ai@localhost:5532/ai")
    target.add_argument("--db-file", help="Path to a SQLite file")

    parser.add_argument(
        "--tenant-id",
        required=True,
        help=(
            "Tenant the definitions are written to. Required on purpose: an omitted tenant "
            "writes untenanted rows that only an untenanted reader can see."
        ),
    )
    parser.add_argument("--db-id", help="Id to register the target database under, and to point template db blocks at")
    parser.add_argument("--templates-dir", help="Directory of *.json bundles (default: the packaged templates)")
    parser.add_argument("--template", nargs="+", help="Specific bundle files, instead of a directory")
    parser.add_argument("--stage", choices=("draft", "published"), default="published", help="Stage to write at")
    parser.add_argument(
        "--on-conflict",
        choices=("error", "skip", "new_version"),
        default="new_version",
        help=(
            "What to do when a component id already exists. Defaults to new_version because "
            "seeding is meant to be re-run; use error when importing a bundle you did not author."
        ),
    )
    parser.add_argument(
        "--on-deleted",
        choices=("error", "skip"),
        default="error",
        help="What to do when an id is held by a soft-deleted component (there is no undelete)",
    )
    parser.add_argument("--id-prefix", help="Prefix every seeded component id")
    parser.add_argument(
        "--drop-db",
        action="store_true",
        help="Strip each definition's db block, so hydration attaches the runtime's own database",
    )
    parser.add_argument("--actor", help="Principal recorded on the audit rows")
    parser.add_argument("--note", help="Reason recorded on the audit rows")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and validate everything, write nothing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show the full traceback on failure")
    return parser


def build_db(args: argparse.Namespace) -> Any:
    """Construct the target database.

    This script is a composition root, so it is one of the few places allowed to import
    concrete integrations directly (ADR 0001, rule 4).
    """
    db_id = args.db_id or "os-db"

    if args.db_file:
        from egpt.integrations.db.sqlite import SqliteDb

        return SqliteDb(db_file=args.db_file, id=db_id)

    url = args.db_url
    if url.startswith("sqlite"):
        from egpt.integrations.db.sqlite import SqliteDb

        return SqliteDb(db_url=url, id=db_id)

    from egpt.integrations.db.postgres import PostgresDb

    return PostgresDb(db_url=url, id=db_id)


def render(results: Sequence[Any]) -> List[str]:
    lines = []
    for result in results:
        version = f"v{result.version}" if result.version is not None else "-"
        line = (
            f"{result.action:<{_ACTION_WIDTH}}"
            f"{result.component_type:<{_TYPE_WIDTH}}"
            f"{result.component_id:<{_ID_WIDTH}}"
            f"{version}"
        )
        if result.reason:
            line = f"{line}   {result.reason}"
        lines.append(line.rstrip())
    return lines


def summarize(results: Sequence[Any]) -> str:
    counts = {"created": 0, "new_version": 0, "published": 0, "unchanged": 0, "metadata_updated": 0, "skipped": 0}
    for result in results:
        counts[result.action] = counts.get(result.action, 0) + 1
    return (
        f"{len(results)} components: "
        f"{counts['created']} created, "
        f"{counts['new_version']} new version, "
        f"{counts['published']} published, "
        f"{counts['unchanged']} unchanged, "
        f"{counts['metadata_updated']} metadata updated, "
        f"{counts['skipped']} skipped"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        from egpt.components.io import seed_from_directory
        from egpt.registry import Registry
        from egpt.registry.io import ComponentIOError
        from egpt.registry.policy import resolve_policy
        from egpt.registry.schema import ConfigValidationError
    except ImportError as exc:
        print(f"Could not import the framework: {exc}", file=sys.stderr)
        print("Install it with: pip install -e libs/agno", file=sys.stderr)
        return 2

    if args.as_json:
        # The framework logs progress at INFO on stdout, which is useful to a human and fatal to
        # `seed_agents.py --json | jq`: it lands on the same stream as the payload.
        #
        # Every logger, not just "egpt". The framework has three (`egpt`, `egpt-team`,
        # `egpt-workflow`) and hydrating a team or a workflow -- which importing a bundle
        # does -- writes to the siblings. Quieting one of them left the other two interleaving
        # INFO lines into the JSON, and no subprocess test could catch it because a fresh
        # interpreter has not switched the siblings on yet.
        #
        # After the import, not before: importing egpt configures these loggers, so an earlier
        # call is overwritten.
        from egpt.utils.log import set_log_level_to_warning

        for source_type in (None, "team", "workflow"):
            set_log_level_to_warning(source_type)

    try:
        db = build_db(args)
    except ImportError as exc:
        print(f"Could not import the database driver: {exc}", file=sys.stderr)
        print("Install the matching extra, e.g. pip install 'egpt[postgres]'", file=sys.stderr)
        return 2

    # Registering the db is what lets a template's `db: {"id": ...}` reference resolve. Without
    # it every db-bearing template fails validation with an unresolvable_ref, which is a
    # baffling thing to hit on a first run.
    registry = Registry()
    registry.add_db(db)
    policy = resolve_policy(None, registry)

    try:
        results = seed_from_directory(
            db,
            args.templates_dir,
            tenant_id=args.tenant_id,
            registry=registry,
            policy=policy,
            templates=args.template,
            stage=args.stage,
            on_conflict=args.on_conflict,
            on_deleted=args.on_deleted,
            id_prefix=args.id_prefix,
            db_id=args.db_id,
            drop_db=args.drop_db,
            actor=args.actor,
            audit_note=args.note,
            dry_run=args.dry_run,
        )
    except ConfigValidationError as exc:
        print(f"A definition failed validation ({exc.component_type}):", file=sys.stderr)
        for error in exc.errors:
            print(f"  {error.path or '<config>'}: {error.message} [{error.code}]", file=sys.stderr)
        return 1
    except ComponentIOError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not propagate
        if args.verbose:
            raise
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        print("Re-run with -v for the full traceback.", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for line in render(results):
            print(line)
        print()
        print(summarize(results))
        if args.dry_run:
            print("(dry run -- nothing was written)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
