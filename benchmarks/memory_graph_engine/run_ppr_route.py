"""Route A test: is SM-R9's `gds.pageRank(sourceNodes=...)` actually available
on a self-hostable Neo4j Community + the free GDS plugin?

SM-R9's §Integration route makes the whole low-risk path conditional on a
"GDS-capable engine". That has never been checked against a real deployment.
This does three things:

1. reports the edition + GDS version actually running;
2. runs `gds.pageRank` **with `sourceNodes`** -- the personalized variant SM-R9
   names as its concrete capability requirement -- over a small graph;
3. verifies the result is genuinely *personalized* (seeding different source
   nodes must produce different rankings), so a silent fallback to unseeded
   PageRank cannot pass as success.

Also runs the same personalization over SM-R10's 5a-filtered projection, which
is the composition SM-R9 flagged as mechanically-sound-but-unmeasured.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from neo4j import GraphDatabase

URI = os.getenv("GDS_URI", "bolt://localhost:7690")
AUTH = (os.getenv("GDS_USER", "neo4j"), os.getenv("GDS_PASSWORD", "gdstestpassword"))

SEED_GRAPH = """
CREATE (a:E {name:'A'}), (b:E {name:'B'}), (c:E {name:'C'}),
       (d:E {name:'D'}), (e:E {name:'E'})
CREATE (a)-[:R {w:1.0, t_valid: datetime('2024-01-01'), t_invalid: null,      expired_at: null}]->(b)
CREATE (b)-[:R {w:1.0, t_valid: datetime('2024-01-01'), t_invalid: null,      expired_at: null}]->(c)
CREATE (c)-[:R {w:1.0, t_valid: datetime('2024-01-01'), t_invalid: null,      expired_at: null}]->(d)
CREATE (d)-[:R {w:1.0, t_valid: datetime('2024-01-01'), t_invalid: null,      expired_at: null}]->(e)
// a stale edge: superseded in June, and no longer current belief
CREATE (a)-[:R {w:1.0, t_valid: datetime('2024-01-01'),
                t_invalid: datetime('2024-06-01'),
                expired_at: datetime('2024-06-01')}]->(e)
"""


def ppr(session, graph_name: str, seed_name: str):
    rows = session.run(
        """
        MATCH (s:E {name:$seed})
        CALL gds.pageRank.stream($g, {sourceNodes:[s], dampingFactor:0.5})
        YIELD nodeId, score
        RETURN gds.util.asNode(nodeId).name AS name, score
        ORDER BY score DESC, name
        """,
        g=graph_name,
        seed=seed_name,
    ).data()
    return {r["name"]: round(r["score"], 6) for r in rows}


def main() -> int:
    out = {}
    driver = GraphDatabase.driver(URI, auth=AUTH)
    with driver.session() as s:
        out["edition"] = s.run(
            "CALL dbms.components() YIELD edition RETURN edition"
        ).single()["edition"]
        out["gds_version"] = s.run("RETURN gds.version() AS v").single()["v"]
        out["gds_procedure_count"] = s.run(
            "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'gds' RETURN count(name) AS n"
        ).single()["n"]

        s.run("MATCH (n:E) DETACH DELETE n")
        s.run(SEED_GRAPH)

        # --- unfiltered projection -------------------------------------
        s.run("CALL gds.graph.drop('full', false)")
        s.run(
            "MATCH (n:E) OPTIONAL MATCH (n)-[r:R]->(m:E) "
            "RETURN gds.graph.project('full', n, m, "
            "{relationshipProperties: r {.w}}) AS g"
        )
        from_a = ppr(s, "full", "A")
        from_e = ppr(s, "full", "E")
        out["ppr_seeded_from_A"] = from_a
        out["ppr_seeded_from_E"] = from_e
        out["personalization_is_real"] = from_a != from_e

        # --- SM-R10 5a-filtered projection ------------------------------
        # The exact predicate SM-R10 SS7 ratified, applied at projection time.
        s.run("CALL gds.graph.drop('at_T', false)")
        s.run(
            """
            MATCH (n:E)
            OPTIONAL MATCH (n)-[r:R]->(m:E)
            WHERE r.t_valid <= datetime($T)
              AND datetime($T) < coalesce(r.t_invalid, datetime('9999-12-31T00:00:00Z'))
              AND r.expired_at IS NULL
            RETURN gds.graph.project('at_T', n, m, {relationshipProperties: r {.w}}) AS g
            """,
            T="2024-09-01T00:00:00Z",
        )
        out["ppr_filtered_from_A"] = ppr(s, "at_T", "A")
        out["filtered_rel_count"] = s.run(
            "CALL gds.graph.list('at_T') YIELD relationshipCount RETURN relationshipCount AS c"
        ).single()["c"]
        out["unfiltered_rel_count"] = s.run(
            "CALL gds.graph.list('full') YIELD relationshipCount RETURN relationshipCount AS c"
        ).single()["c"]

    driver.close()

    p = Path(__file__).parent / "results" / "ppr_route_a.json"
    p.write_text(json.dumps(out, indent=2))

    print(f"Neo4j edition           : {out['edition']}")
    print(f"GDS version             : {out['gds_version']}")
    print(f"gds.* procedures        : {out['gds_procedure_count']}")
    print()
    print(f"PPR seeded from A       : {out['ppr_seeded_from_A']}")
    print(f"PPR seeded from E       : {out['ppr_seeded_from_E']}")
    print(f"personalization is real : {out['personalization_is_real']}")
    print()
    print("SM-R10 5a-filtered projection (T = 2024-09-01):")
    print(
        f"  relationships unfiltered / filtered : "
        f"{out['unfiltered_rel_count']} / {out['filtered_rel_count']}"
    )
    print(f"  PPR from A on filtered projection   : {out['ppr_filtered_from_A']}")
    print()
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
