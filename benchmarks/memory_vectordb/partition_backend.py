"""pgvector declarative-partitioning tenancy backend for the SM-R3 benchmark.

Review #4: `DECISION.md` recommends "indexed `tenant_id` + declarative partitioning" as the
production multi-tenancy layout, but v1 never benchmarked it. This module measures it.

It is intentionally *not* built on the egpt `PgVector` adapter (which creates one flat
table) — declarative partitioning is a schema shape the adapter doesn't express. This is
benchmark-only code (raw SQL via psycopg); it does not touch core. It creates:

    CREATE TABLE parent (...) PARTITION BY LIST (tenant_id);
    CREATE TABLE parent_t<T> PARTITION OF parent FOR VALUES IN (T);   -- per tenant
    CREATE INDEX ... USING hnsw (embedding vector_cosine_ops);        -- per partition

A `WHERE tenant_id = T` query is partition-pruned to that tenant's partition and its own
smaller HNSW index — the "one logical table, per-tenant index" layout the ADR recommends.

Vectors come from the shared `DatasetEmbedder` (real dataset vectors), so this backend
indexes the exact same vectors as the egpt-interface backends.
"""

from __future__ import annotations

import time
from typing import List

import numpy as np
import psycopg

from .realdata import DatasetEmbedder, Record


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v.tolist()) + "]"


class PgvectorPartitioned:
    """pgvector, one table partitioned by tenant_id, HNSW index per partition."""

    def __init__(
        self,
        db_url_psycopg: str,
        table: str,
        dim: int,
        ef_search: int,
        tenant_ids: List[int],
    ):
        self.dsn = db_url_psycopg
        self.table = table
        self.dim = dim
        self.ef_search = ef_search
        self.tenant_ids = tenant_ids
        self._sconn = None  # persistent connection reused for the search phase

    def _conn(self):
        return psycopg.connect(self.dsn, autocommit=True)

    def _search_conn(self):
        # Reuse one connection across queries (matches the egpt stores' pooled connection)
        # so partition latency isn't inflated by per-query connection setup.
        if self._sconn is None or self._sconn.closed:
            self._sconn = psycopg.connect(self.dsn, autocommit=True)
            self._sconn.execute(f"SET hnsw.ef_search = {self.ef_search}")
        return self._sconn

    def drop(self) -> None:
        with self._conn() as c:
            c.execute(f"DROP TABLE IF EXISTS {self.table} CASCADE")

    def create(self) -> None:
        with self._conn() as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS vector")
            c.execute(f"DROP TABLE IF EXISTS {self.table} CASCADE")
            c.execute(
                f"CREATE TABLE {self.table} "
                f"(id text, tenant_id int, embedding vector({self.dim})) "
                f"PARTITION BY LIST (tenant_id)"
            )
            for t in self.tenant_ids:
                c.execute(
                    f"CREATE TABLE {self.table}_t{t} PARTITION OF {self.table} FOR VALUES IN ({t})"
                )

    def insert(self, records: List[Record], embedder: DatasetEmbedder) -> float:
        """Bulk-load via COPY; return wall-clock seconds (index built after load)."""
        start = time.perf_counter()
        with self._conn() as c, c.cursor() as cur:
            with cur.copy(
                f"COPY {self.table} (id, tenant_id, embedding) FROM STDIN"
            ) as cp:
                for r in records:
                    cp.write_row(
                        (r.id, r.tenant_id, _vec_literal(embedder.train[r.train_index]))
                    )
        # Build HNSW on the parent -> propagates to every partition. Serial build to avoid
        # the container's tiny /dev/shm (parallel maintenance workers use DSM).
        with self._conn() as c:
            c.execute("SET max_parallel_maintenance_workers = 0")
            c.execute("SET maintenance_work_mem = '512MB'")
            c.execute(
                f"CREATE INDEX ON {self.table} USING hnsw (embedding vector_cosine_ops) "
                f"WITH (m = 16, ef_construction = 200)"
            )
        return time.perf_counter() - start

    def search_ids(self, qvec: np.ndarray, tenant_id: int, k: int) -> List[str]:
        lit = _vec_literal(qvec)
        cur = self._search_conn().cursor()
        cur.execute(
            f"SELECT id FROM {self.table} WHERE tenant_id = %s "
            f"ORDER BY embedding <=> %s::vector LIMIT %s",
            (tenant_id, lit, k),
        )
        rows = [row[0] for row in cur.fetchall()]
        cur.close()
        return rows

    def close(self) -> None:
        if self._sconn is not None and not self._sconn.closed:
            self._sconn.close()

    def exists(self) -> bool:
        with self._conn() as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (self.table,))
            return cur.fetchone()[0] is not None
