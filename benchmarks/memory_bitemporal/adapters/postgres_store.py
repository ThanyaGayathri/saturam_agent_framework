"""Postgres adapter for ``BiTemporalEdgeStore`` (real SQL against schema.sql).

Implements the five storage primitives with real SQL; the invalidate-not-delete rule and the
point-in-time predicates are inherited unchanged from the base, so this backend is guaranteed
to match the reference store. Epoch-seconds ints (Python model) <-> timestamptz (Postgres) via
``to_timestamp`` / ``EXTRACT(EPOCH ...)``. The schema also ships a SQL-native ``bt_upsert_edge``
function (see schema.sql) that SM-X11 can call directly; ``upsert_via_sql_function`` exercises it.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import psycopg

from ..models import BiTemporalEdge
from ..store import BiTemporalEdgeStore

_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "schema.sql"


class PostgresEdgeStore(BiTemporalEdgeStore):
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg.connect(dsn, autocommit=True)
        self._init_schema()

    def _init_schema(self) -> None:
        sql = _SCHEMA_SQL.read_text()
        # Execute the DDL/functions (comment-only query blocks are ignored by the driver).
        with self.conn.cursor() as cur:
            cur.execute(sql)  # type: ignore[arg-type]

    def reset(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE memory_edge;")

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _row_to_edge(row: tuple) -> BiTemporalEdge:
        edge_id, subject_id, predicate, object_id, user_id, fact, t_valid, t_invalid, created_at, expired_at = row
        return BiTemporalEdge(
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            user_id=user_id,
            fact=fact,
            t_valid=int(t_valid),
            t_invalid=int(t_invalid) if t_invalid is not None else None,
            created_at=int(created_at),
            expired_at=int(expired_at) if expired_at is not None else None,
            edge_id=edge_id,
        )

    _SELECT = (
        "SELECT edge_id, subject_id, predicate, object_id, user_id, fact, "
        "EXTRACT(EPOCH FROM t_valid)::bigint, EXTRACT(EPOCH FROM t_invalid)::bigint, "
        "EXTRACT(EPOCH FROM created_at)::bigint, EXTRACT(EPOCH FROM expired_at)::bigint "
        "FROM memory_edge"
    )

    def _insert(self, edge: BiTemporalEdge) -> None:
        # to_timestamp(NULL::bigint) yields NULL, so open intervals need no CASE branch.
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_edge "
                "(edge_id, subject_id, predicate, object_id, user_id, fact, t_valid, t_invalid, created_at, expired_at) "
                "VALUES (%s,%s,%s,%s,%s,%s, to_timestamp(%s::bigint), to_timestamp(%s::bigint), "
                "to_timestamp(%s::bigint), to_timestamp(%s::bigint))",
                (
                    edge.edge_id,
                    edge.subject_id,
                    edge.predicate,
                    edge.object_id,
                    edge.user_id,
                    edge.fact,
                    edge.t_valid,
                    edge.t_invalid,
                    edge.created_at,
                    edge.expired_at,
                ),
            )

    def _current_edges(self, subject_id: str, predicate: str, user_id: str) -> List[BiTemporalEdge]:
        with self.conn.cursor() as cur:
            cur.execute(
                self._SELECT + " WHERE user_id=%s AND subject_id=%s AND predicate=%s AND expired_at IS NULL",
                (user_id, subject_id, predicate),
            )
            return [self._row_to_edge(r) for r in cur.fetchall()]

    def _mark_expired(self, edge_id: str, expired_at: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE memory_edge SET expired_at=to_timestamp(%s) WHERE edge_id=%s", (expired_at, edge_id))

    def _all(self) -> List[BiTemporalEdge]:
        with self.conn.cursor() as cur:
            cur.execute(self._SELECT)
            return [self._row_to_edge(r) for r in cur.fetchall()]

    def upsert_via_sql_function(
        self, edge_id: str, subject: str, predicate: str, obj: str, user: str, fact: Optional[str], t_valid: int
    ) -> str:
        """Exercise the SQL-native bt_upsert_edge() path (for SM-X11)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT bt_upsert_edge(%s,%s,%s,%s,%s,%s, to_timestamp(%s))",
                (edge_id, subject, predicate, obj, user, fact, t_valid),
            )
            row = cur.fetchone()
            return str(row[0]) if row else ""
