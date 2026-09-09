"""R1 -- the application image's API entrypoint, against a local Postgres.

The package's verification: run locally against a local Postgres, call health, call config.
Evidence: health returns 200; config lists the registered agents and workflows.

Two ways to run it, both against the real database on port 5532:

* In-process (default): builds the same ``egpt_retail.app.main:app`` the image serves, runs
  its lifespans (which provision the control-plane tables), and calls the two routes.
* Against a running server: ``EGPT_RETAIL_URL=http://127.0.0.1:7777`` after
  ``egpt up -f deploy/compose.yaml`` or ``egpt-retail api``.

Before either: ``docker compose -f deploy/compose.yaml up -d postgres`` (or
``./cookbook/scripts/run_pgvector.sh``). No model key is needed -- the agent is registered, not
run.

Run:
    .venv/bin/python cookbook/94_retail/R01_local_stack.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import httpx  # noqa: E402
from egpt_retail.app.components import SYNONYM_PROPOSER_ID  # noqa: E402
from egpt_retail.app.db import CONTROL_PLANE_DB_ID  # noqa: E402

print("R1 -- API entrypoint against a local Postgres")
print("=" * 78)

base_url = os.environ.get("EGPT_RETAIL_URL")
if base_url:
    client = httpx.Client(base_url=base_url, timeout=30)
    print("  against a running server at", base_url)
    health = client.get("/health")
    config = client.get("/config")
else:
    from fastapi.testclient import TestClient

    os.environ.setdefault("EGPT_RETAIL_ROLE", "api")
    # K1 made a customer slug mandatory: without one every run carries tenant_id=None and
    # memory silently does nothing, so the factory now refuses to build rather than start
    # memoryless. A real deployment always has one; this cookbook needs one too.
    os.environ.setdefault("EGPT_RETAIL_CUSTOMER_SLUG", "cookbook")
    from egpt_retail.app.main import app, settings

    print("  in-process; database:", settings.retail_db_url)
    # The `with` runs the lifespans: db provisioning happens here, against the real database.
    with TestClient(app) as client:
        health = client.get("/health")
        config = client.get("/config")

# --- 1. Health ------------------------------------------------------------------------------

print()
print("1. GET /health")
print("-" * 78)
print("  status:", health.status_code, "| body:", health.json())

# --- 2. Config lists what is registered ---------------------------------------------------------

print()
print("2. GET /config")
print("-" * 78)
body = config.json()
agents = body.get("agents") or []
workflows = body.get("workflows") or []
databases = body.get("databases") or []
print("  status   :", config.status_code)
print("  os_id    :", body.get("os_id"))
print("  databases:", databases)
print("  agents   :", [(a.get("id"), a.get("name"), a.get("model")) for a in agents])
print(
    "  workflows:",
    [w.get("id") for w in workflows],
    "(none in phase zero; the RFQ chain is R10)",
)

problems = []
if health.status_code != 200 or health.json().get("status") != "ok":
    problems.append("health did not return 200 with status ok")
if config.status_code != 200:
    problems.append(f"config returned {config.status_code}")
if SYNONYM_PROPOSER_ID not in {a.get("id") for a in agents}:
    problems.append(f"config does not list agent {SYNONYM_PROPOSER_ID!r}")
if CONTROL_PLANE_DB_ID not in databases:
    problems.append(f"config does not list database {CONTROL_PLANE_DB_ID!r}")

print()
print("=" * 78)
if problems:
    print("R1 API entrypoint does NOT meet the package evidence:")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print("Health 200; config lists the registered agent and the control-plane database.")
