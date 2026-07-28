"""SQL-native adapter -- the path SM-G4/G5/X11 actually ship (SM-R10).

``PostgresEdgeStore`` implements storage primitives and *inherits* the Python invalidation rule
and query semantics from ``BiTemporalEdgeStore``. That means a passing score there validates the
**Python** rule plus a storage round-trip -- it says nothing about the SQL that downstream
consumes.

This adapter closes that gap. It overrides every semantic method so the run is driven entirely by
SQL:

* writes  -> ``bt_upsert_edge()`` / ``bt_retract_edge()`` (schema.sql SS2), not ``store.py``
* reads   -> the literal 5a / 5b predicates from SPEC SS7, not Python interval filtering

Scored against the same probes as the reference store, it is an **equivalence test** between the
Python reference and the shipped SQL. Nothing in the base class's rule is reachable from here.
"""

from __future__ import annotations

from typing import List

from ..models import BiTemporalEdge, EdgeOutcome
from .postgres_store import PostgresEdgeStore

# SPEC SS7 / 5a -- "true at world-time :T" per current belief.
_WHERE_5A = (
    " WHERE user_id=%s AND subject_id=%s AND predicate=%s"
    "   AND t_valid <= to_timestamp(%s::bigint)"
    "   AND to_timestamp(%s::bigint) < COALESCE(t_invalid, 'infinity'::timestamptz)"
    "   AND expired_at IS NULL"
)

# SPEC SS7 / 5b -- "as believed at transaction-time :TT, about world-time :T".
_WHERE_5B = (
    " WHERE user_id=%s AND subject_id=%s AND predicate=%s"
    "   AND t_valid    <= to_timestamp(%s::bigint)"
    "   AND to_timestamp(%s::bigint)  < COALESCE(t_invalid,  'infinity'::timestamptz)"
    "   AND created_at <= to_timestamp(%s::bigint)"
    "   AND to_timestamp(%s::bigint) < COALESCE(expired_at, 'infinity'::timestamptz)"
)


class SqlNativeEdgeStore(PostgresEdgeStore):
    """Postgres store driven by the SQL-native rule + the SPEC SS7 predicates."""

    def upsert_edge(self, edge: BiTemporalEdge) -> EdgeOutcome:
        """R1/R2/NOOP decided by ``bt_upsert_edge()`` in the database, not by ``store.py``."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT bt_upsert_edge(%s,%s,%s,%s,%s,%s, to_timestamp(%s::bigint), to_timestamp(%s::bigint))",
                (
                    edge.edge_id,
                    edge.subject_id,
                    edge.predicate,
                    edge.object_id,
                    edge.user_id,
                    edge.fact,
                    edge.t_valid,
                    edge.created_at,
                ),
            )
            row = cur.fetchone()
            return EdgeOutcome(str(row[0])) if row else EdgeOutcome.INSERT

    def retract_edge(self, edge_id: str, expired_at: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT bt_retract_edge(%s, to_timestamp(%s::bigint))", (edge_id, expired_at))

    def query_all_valid_at(self, subject_id: str, predicate: str, user_id: str, t: int) -> List[BiTemporalEdge]:
        with self.conn.cursor() as cur:
            cur.execute(self._SELECT + _WHERE_5A, (user_id, subject_id, predicate, t, t))
            return [self._row_to_edge(r) for r in cur.fetchall()]

    def query_bitemporal(self, subject_id: str, predicate: str, user_id: str, t: int, tt: int) -> List[BiTemporalEdge]:
        with self.conn.cursor() as cur:
            cur.execute(self._SELECT + _WHERE_5B, (user_id, subject_id, predicate, t, t, tt, tt))
            return [self._row_to_edge(r) for r in cur.fetchall()]
