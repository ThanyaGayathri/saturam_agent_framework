"""Cypher-native adapter -- the graph path SM-G4/G5 actually ship (SM-R10).

Counterpart to ``sql_native_store``: ``Neo4jEdgeStore`` inherits the Python rule from
``BiTemporalEdgeStore``, so its score validates storage round-trip, not the Cypher in
[graph_schema.cypher](../graph_schema.cypher). Here the whole run is driven by Cypher:

* writes -> the R1 supersede / NOOP-guard / back-date / R2 statements from graph_schema.cypher
* reads  -> the literal 5a / 5b predicates from SPEC SS7

The open-interval sentinel mirrors the documented Cypher form
(``coalesce(e.t_invalid, datetime('9999-12-31T00:00:00Z'))``) rather than Python's ``+inf``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models import BiTemporalEdge, EdgeOutcome
from .neo4j_store import Neo4jEdgeStore

_MAX_DT = "datetime('9999-12-31T00:00:00Z')"

_NOOP_GUARD = (
    "MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->"
    "(o:Entity {id:$object}) "
    "WHERE e.expired_at IS NULL "
    "  AND e.t_valid <= datetime({epochSeconds:$t_valid}) "
    "  AND datetime({epochSeconds:$t_valid}) < coalesce(e.t_invalid, " + _MAX_DT + ") "
    "RETURN e LIMIT 1"
)

# R1 SUPERSEDE: expire each conflicting current fact whose window contains $t_valid, then
# re-create it as a bounded version (SCD-2). Nothing is deleted or mutated in place.
_SUPERSEDE = (
    "MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity) "
    "WHERE e.expired_at IS NULL AND o.id <> $object "
    "  AND e.t_valid <= datetime({epochSeconds:$t_valid}) "
    "  AND datetime({epochSeconds:$t_valid}) < coalesce(e.t_invalid, " + _MAX_DT + ") "
    "SET e.expired_at = datetime({epochSeconds:$created_at}) "
    "WITH s, o, e "
    "CREATE (s)-[:REL {edge_id: randomUUID(), user_id: e.user_id, predicate: e.predicate, "
    "  fact: e.fact, t_valid: e.t_valid, t_invalid: datetime({epochSeconds:$t_valid}), "
    "  created_at: datetime({epochSeconds:$created_at}), expired_at: null}]->(o) "
    "RETURN count(*) AS n"
)

# Back-dated insert: the earliest later conflicting fact bounds the incoming edge.
_BOUND = (
    "MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity) "
    "WHERE e.expired_at IS NULL AND o.id <> $object "
    "  AND e.t_valid > datetime({epochSeconds:$t_valid}) "
    "RETURN min(e.t_valid) AS bound"
)

_INSERT = (
    "MERGE (a:Entity {id:$subject}) MERGE (b:Entity {id:$object}) "
    "CREATE (a)-[:REL {edge_id:$edge_id, user_id:$user, predicate:$predicate, fact:$fact, "
    "  t_valid: datetime({epochSeconds:$t_valid}), t_invalid: $bound, "
    "  created_at: datetime({epochSeconds:$created_at}), expired_at: null}]->(b)"
)

# R2 RETRACT (transaction axis only).
_RETRACT = (
    "MATCH ()-[e:REL {edge_id:$edge_id}]->() WHERE e.expired_at IS NULL SET e.expired_at = datetime({epochSeconds:$t})"
)


def _where_5a(alias: str = "e") -> str:
    return (
        f"WHERE {alias}.t_valid <= datetime({{epochSeconds:$t}}) "
        f"  AND datetime({{epochSeconds:$t}}) < coalesce({alias}.t_invalid, {_MAX_DT}) "
        f"  AND {alias}.expired_at IS NULL "
    )


def _where_5b(alias: str = "e") -> str:
    return (
        f"WHERE {alias}.t_valid <= datetime({{epochSeconds:$t}}) "
        f"  AND datetime({{epochSeconds:$t}}) < coalesce({alias}.t_invalid, {_MAX_DT}) "
        f"  AND {alias}.created_at <= datetime({{epochSeconds:$tt}}) "
        f"  AND datetime({{epochSeconds:$tt}}) < coalesce({alias}.expired_at, {_MAX_DT}) "
    )


class CypherNativeEdgeStore(Neo4jEdgeStore):
    """Neo4j store driven by the Cypher-native rule + the SPEC SS7 predicates."""

    def upsert_edge(self, edge: BiTemporalEdge) -> EdgeOutcome:
        params: Dict[str, Any] = {
            "subject": edge.subject_id,
            "object": edge.object_id,
            "user": edge.user_id,
            "predicate": edge.predicate,
            "fact": edge.fact,
            "edge_id": edge.edge_id,
            "t_valid": edge.t_valid,
            "created_at": edge.created_at,
        }
        with self.driver.session() as s:
            if s.run(_NOOP_GUARD, **params).single() is not None:
                return EdgeOutcome.NOOP

            rec = s.run(_SUPERSEDE, **params).single()
            superseded = int(rec["n"]) if rec and rec["n"] is not None else 0

            bound_rec = s.run(_BOUND, **params).single()
            bound: Optional[Any] = bound_rec["bound"] if bound_rec else None

            s.run(_INSERT, bound=bound, **params)
        return EdgeOutcome.SUPERSEDE if superseded else EdgeOutcome.INSERT

    def retract_edge(self, edge_id: str, expired_at: int) -> None:
        with self.driver.session() as s:
            s.run(_RETRACT, edge_id=edge_id, t=expired_at)

    def query_all_valid_at(self, subject_id: str, predicate: str, user_id: str, t: int) -> List[BiTemporalEdge]:
        with self.driver.session() as s:
            res = s.run(
                "MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity) "
                + _where_5a()
                + "RETURN "
                + self._RETURN,
                subject=subject_id,
                user=user_id,
                predicate=predicate,
                t=t,
            )
            return [self._rec_to_edge(r.data()) for r in res]

    def query_bitemporal(self, subject_id: str, predicate: str, user_id: str, t: int, tt: int) -> List[BiTemporalEdge]:
        with self.driver.session() as s:
            res = s.run(
                "MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity) "
                + _where_5b()
                + "RETURN "
                + self._RETURN,
                subject=subject_id,
                user=user_id,
                predicate=predicate,
                t=t,
                tt=tt,
            )
            return [self._rec_to_edge(r.data()) for r in res]
