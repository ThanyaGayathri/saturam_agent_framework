"""Neo4j adapter for ``BiTemporalEdgeStore`` (real Cypher against the graph tier).

Implements the five storage primitives with Cypher; the invalidate-not-delete rule and the
point-in-time predicates are inherited from the base, so this backend matches the reference
store. Epoch-seconds ints <-> Neo4j ``datetime`` via ``datetime({epochSeconds: $ts})`` and
``.epochSeconds`` on read.
"""

from __future__ import annotations

from typing import Any, List, Optional

from neo4j import GraphDatabase

from ..models import BiTemporalEdge
from ..store import BiTemporalEdgeStore


class Neo4jEdgeStore(BiTemporalEdgeStore):
    def __init__(self, uri: str, user: str, password: str) -> None:
        # Silence "unknown property key" notifications for optional/null props (e.g. fact).
        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        # The driver connects lazily, so constructing it succeeds even with no server. run.py's
        # reachability probe would then "pass" and the benchmark would crash mid-run instead of
        # skipping the backend -- force the connection here so unreachable means unreachable.
        self.driver.verify_connectivity()

    def reset(self) -> None:
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")

    def close(self) -> None:
        self.driver.close()

    @staticmethod
    def _rec_to_edge(r: dict) -> BiTemporalEdge:
        def epoch(v: Any) -> Optional[int]:
            # v is a neo4j DateTime (or None); to_native() -> aware datetime.
            return int(v.to_native().timestamp()) if v is not None else None

        return BiTemporalEdge(
            subject_id=r["subject_id"],
            predicate=r["predicate"],
            object_id=r["object_id"],
            user_id=r["user_id"],
            fact=r.get("fact"),
            t_valid=epoch(r["t_valid"]),
            t_invalid=epoch(r["t_invalid"]),
            created_at=epoch(r["created_at"]),
            expired_at=epoch(r["expired_at"]),
            edge_id=r["edge_id"],
        )

    _RETURN = (
        "e.edge_id AS edge_id, s.id AS subject_id, e.predicate AS predicate, o.id AS object_id, "
        "e.user_id AS user_id, e.fact AS fact, e.t_valid AS t_valid, e.t_invalid AS t_invalid, "
        "e.created_at AS created_at, e.expired_at AS expired_at"
    )

    def _insert(self, edge: BiTemporalEdge) -> None:
        with self.driver.session() as s:
            s.run(
                "MERGE (a:Entity {id:$subject}) MERGE (b:Entity {id:$object}) "
                "CREATE (a)-[e:REL {edge_id:$edge_id, user_id:$user, predicate:$predicate, fact:$fact, "
                "t_valid: datetime({epochSeconds:$t_valid}), "
                "t_invalid: CASE WHEN $t_invalid IS NULL THEN null ELSE datetime({epochSeconds:$t_invalid}) END, "
                "created_at: datetime({epochSeconds:$created_at}), "
                "expired_at: CASE WHEN $expired_at IS NULL THEN null ELSE datetime({epochSeconds:$expired_at}) END}]->(b)",
                subject=edge.subject_id,
                object=edge.object_id,
                edge_id=edge.edge_id,
                user=edge.user_id,
                predicate=edge.predicate,
                fact=edge.fact,
                t_valid=edge.t_valid,
                t_invalid=edge.t_invalid,
                created_at=edge.created_at,
                expired_at=edge.expired_at,
            )

    def _current_edges(self, subject_id: str, predicate: str, user_id: str) -> List[BiTemporalEdge]:
        with self.driver.session() as s:
            res = s.run(
                "MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity) "
                "WHERE e.expired_at IS NULL RETURN " + self._RETURN,
                subject=subject_id,
                user=user_id,
                predicate=predicate,
            )
            return [self._rec_to_edge(r.data()) for r in res]

    def _mark_expired(self, edge_id: str, expired_at: int) -> None:
        with self.driver.session() as s:
            s.run(
                "MATCH ()-[e:REL {edge_id:$edge_id}]->() SET e.expired_at = datetime({epochSeconds:$t})",
                edge_id=edge_id,
                t=expired_at,
            )

    def _all(self) -> List[BiTemporalEdge]:
        with self.driver.session() as s:
            res = s.run("MATCH (s:Entity)-[e:REL]->(o:Entity) RETURN " + self._RETURN)
            return [self._rec_to_edge(r.data()) for r in res]
