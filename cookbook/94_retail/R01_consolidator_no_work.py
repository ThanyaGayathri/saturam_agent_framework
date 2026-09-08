"""R1 -- the consolidator entrypoint with nothing to consolidate, both shapes.

The package's evidence: "the consolidator logs a clean no-work exit rather than an error". No
existing code produces that line -- the worker is silent on an empty scope list -- so it is the
entrypoint's, and this script proves both shapes it can take, plus the single-instance refusal:

1. **Slug unset.** No scope is configured; the entrypoint says so and exits 0 without building
   a provider. Needs nothing -- not even the database.
2. **Slug set, nothing due.** A scope exists, a vector-only provider is built against the real
   Postgres (the pgvector table self-provisions), one tick runs, the summary reports zero work,
   exit 0. Needs Postgres on 5532. No model or embedder key: nothing is written.
3. **A second instance while the lock is held** exits 3 and names the customer.

Run:
    docker compose -f deploy/compose.yaml up -d postgres
    .venv/bin/python cookbook/94_retail/R01_consolidator_no_work.py
"""

import io
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt_retail.app.db import psycopg_dsn  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.entrypoints.consolidator import (  # noqa: E402
    EXIT_LOCK_HELD,
    _AdvisoryLock,
    main,
)

print("R1 -- consolidator, nothing to consolidate")
print("=" * 78)


def run(**env: str):
    """Run one `consolidator --once` with the given environment, capturing its log lines."""
    for key in ("EGPT_RETAIL_CUSTOMER_SLUG",):
        os.environ.pop(key, None)
    os.environ.update(env)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    code = main(once=True)
    return code, [
        line
        for line in stream.getvalue().splitlines()
        if line.startswith("consolidator:")
    ]


# --- 1. Slug unset ------------------------------------------------------------------------------

print()
print("1. EGPT_RETAIL_CUSTOMER_SLUG unset")
print("-" * 78)
code_unset, lines_unset = run()
for line in lines_unset:
    print("  ", line)
print("   exit:", code_unset)

# --- 2. Slug set, nothing due -------------------------------------------------------------------

print()
print(
    "2. EGPT_RETAIL_CUSTOMER_SLUG=acme, nothing to drain (real Postgres, vector-only provider)"
)
print("-" * 78)
code_set, lines_set = run(EGPT_RETAIL_CUSTOMER_SLUG="acme")
for line in lines_set:
    print("  ", line)
print("   exit:", code_set)

# --- 3. Second instance refused while the lock is held ---------------------------------------

print()
print("3. A second instance while the lock is held")
print("-" * 78)
settings = RetailSettings()
holder = _AdvisoryLock(
    psycopg_dsn(settings.retail_db_url), "egpt-retail:consolidator:acme"
)
held = holder.acquire()
print("   this script holds the lock:", held)
code_locked, lines_locked = run(EGPT_RETAIL_CUSTOMER_SLUG="acme")
holder.release()
for line in lines_locked:
    print("  ", line)
print("   exit:", code_locked)

# Assert, do not merely print.
problems = []
if code_unset != 0 or not any("no scopes configured" in line for line in lines_unset):
    problems.append("slug unset did not produce the no-work line with exit 0")
if any("tick complete" in line for line in lines_unset):
    problems.append(
        "slug unset ran a tick, which means a provider was built for nothing"
    )
if code_set != 0:
    problems.append(f"slug set exited {code_set}")
summary = next((line for line in lines_set if "tick complete" in line), "")
if "1 scope(s) checked, 0 with work" not in summary:
    problems.append(f"slug set did not report one scope with no work: {summary!r}")
if not held or code_locked != EXIT_LOCK_HELD:
    problems.append(
        f"second instance did not exit {EXIT_LOCK_HELD} while the lock was held (got {code_locked})"
    )

print()
print("=" * 78)
if problems:
    print("Consolidator does NOT meet the package evidence:")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print(
    "Clean no-work exit in both shapes; a second instance is refused while the lock is held."
)
