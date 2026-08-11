"""Measure which production capabilities Neo4j **Community** actually denies.

Review finding #1: the recommendation said "Neo4j Community" without deciding the
production tier or the graph multi-tenancy isolation model. Community's limits are
usually cited from marketing pages; here they are probed against a running
instance so the tier decision rests on what the engine actually refuses.

Each probe records the *verbatim* server error, because the error text is what
tells you whether a limit is a licence gate or a missing feature.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

URI = os.getenv("BT_NEO4J_URI", "bolt://localhost:7688")
AUTH = (
    os.getenv("BT_NEO4J_USER", "neo4j"),
    os.getenv("BT_NEO4J_PASSWORD", "bitemporal"),
)

# (label, cypher, why it matters for a multi-tenant memory tier)
PROBES = [
    (
        "database_per_tenant",
        "CREATE DATABASE tenanta",
        "Hard isolation: one database per tenant. If denied, tenants share one graph.",
    ),
    (
        "rbac_role_creation",
        "CREATE ROLE tenant_reader",
        "Per-tenant least privilege at the engine. If denied, authz is app-side only.",
    ),
    (
        "rbac_show_roles",
        "SHOW ROLES",
        "Any role model at all.",
    ),
    (
        "property_level_security",
        "DENY READ {fact} ON GRAPH neo4j NODES Entity TO tenant_reader",
        "Field-level protection of memory content.",
    ),
]


def main() -> int:
    out: dict = {}
    driver = GraphDatabase.driver(URI, auth=AUTH)
    with driver.session(database="system") as s:
        out["edition"] = (
            s.run(
                "CALL dbms.components() YIELD edition, versions RETURN edition, versions"
            )
            .single()
            .data()
        )
        out["databases"] = [
            r["name"] for r in s.run("SHOW DATABASES YIELD name RETURN name")
        ]

        results = {}
        for label, cypher, why in PROBES:
            try:
                s.run(cypher).consume()
                results[label] = {"allowed": True, "why": why, "error": None}
            except Neo4jError as e:
                results[label] = {
                    "allowed": False,
                    "why": why,
                    "error": (e.message or str(e)).strip().splitlines()[0],
                }
        out["probes"] = results
    driver.close()

    p = Path(__file__).parent / "results" / "tier_limits.json"
    p.write_text(json.dumps(out, indent=2))

    print(f"edition   : {out['edition']['edition']} {out['edition']['versions']}")
    print(f"databases : {out['databases']}  (Community allows only these two)")
    print()
    for label, r in out["probes"].items():
        status = "ALLOWED" if r["allowed"] else "DENIED "
        print(f"  [{status}] {label}")
        if r["error"]:
            print(f"            {r['error']}")
    print()
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
