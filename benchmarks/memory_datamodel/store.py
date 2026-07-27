"""Plug-and-play store seam for the SM-R12 memory model.

The spec is "framework-first": call sites depend only on the abstract :class:`MemoryStore`,
and a concrete backend is chosen by construction/config -- never hardcoded. Two adapters ship
here to prove the seam:

- :class:`PgMemoryStore`  -- the real backend (PostgreSQL + pgvector, the SM-R3 default).
- :class:`InMemoryMemoryStore` -- a trivial dict backend proving a second adapter drops in
  with zero changes to the round-trip test.

Every public method has a sync and an async variant (repo convention). Row<->model mapping is
explicit per tier because the physical tables intentionally differ from the record models
(episodic flattens ``context``; fact/procedural drop the constant ``tier``; working memory has
no table). SM-C3 reuses this mapping when it promotes the schema into core.
"""

import abc
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from .model import (
    EpisodicRecord,
    MemoryLink,
    ProceduralRecord,
    SemanticEntity,
    SemanticFact,
    SemanticRelation,
)

T = TypeVar("T")

# tier model -> (table, ordered column list). Columns mirror ddl/schema.sql exactly.
_TABLES: Dict[type, Tuple[str, List[str]]] = {
    EpisodicRecord: (
        "memory_episodic",
        [
            "tenant_id",
            "id",
            "user_id",
            "agent_id",
            "team_id",
            "session_id",
            "content",
            "occurred_at",
            "location",
            "cause",
            "embedding",
            "embedding_dim",
            "embedding_version",
            "importance",
            "strength",
            "last_accessed_at",
            "created_at",
            "updated_at",
            "t_valid",
            "t_invalid",
            "expired_at",
            "superseded_by",
            "entity_ids",
            "graph_node_id",
            "metadata",
        ],
    ),
    SemanticFact: (
        "memory_semantic_fact",
        [
            "tenant_id",
            "id",
            "user_id",
            "agent_id",
            "team_id",
            "session_id",
            "content",
            "subject",
            "predicate",
            "object",
            "confidence",
            "source_episode_ids",
            "entity_ids",
            "embedding",
            "embedding_dim",
            "embedding_version",
            "importance",
            "strength",
            "last_accessed_at",
            "created_at",
            "updated_at",
            "t_valid",
            "t_invalid",
            "expired_at",
            "superseded_by",
            "metadata",
        ],
    ),
    SemanticEntity: (
        "memory_entity",
        [
            "tenant_id",
            "id",
            "entity_type",
            "name",
            "description",
            "properties",
            "user_id",
            "agent_id",
            "team_id",
            "session_id",
            "embedding",
            "embedding_dim",
            "embedding_version",
            "created_at",
            "updated_at",
            "metadata",
        ],
    ),
    SemanticRelation: (
        "memory_relation",
        [
            "tenant_id",
            "id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "confidence",
            "source_episode_ids",
            "user_id",
            "agent_id",
            "team_id",
            "session_id",
            "created_at",
            "updated_at",
            "t_valid",
            "t_invalid",
            "expired_at",
            "superseded_by",
            "metadata",
        ],
    ),
    ProceduralRecord: (
        "memory_procedural",
        [
            "tenant_id",
            "id",
            "kind",
            "title",
            "content",
            "trigger",
            "tags",
            "success_count",
            "user_id",
            "agent_id",
            "team_id",
            "session_id",
            "embedding",
            "embedding_dim",
            "embedding_version",
            "created_at",
            "updated_at",
            "metadata",
        ],
    ),
    MemoryLink: (
        "memory_link",
        [
            "tenant_id",
            "id",
            "from_id",
            "from_tier",
            "to_id",
            "to_tier",
            "link_type",
            "user_id",
            "agent_id",
            "team_id",
            "session_id",
            "created_at",
            "updated_at",
            "t_valid",
            "t_invalid",
            "expired_at",
            "superseded_by",
            "metadata",
        ],
    ),
}


def _to_row(rec: Any) -> Dict[str, Any]:
    """Flatten a record model into its table's column dict (mirrors ddl/schema.sql)."""
    d = rec.model_dump()
    d.pop("tier", None)  # constant per table -> not stored
    if isinstance(rec, EpisodicRecord):
        ctx = d.pop("context", {}) or {}
        d["occurred_at"] = ctx.get("occurred_at")
        d["location"] = ctx.get("location")
        d["cause"] = ctx.get("cause")
    if isinstance(rec, SemanticFact):
        d.pop("kind", None)  # constant 'fact' for this table
    _, cols = _TABLES[type(rec)]
    return {c: d.get(c) for c in cols}


def _from_row(model: Type[T], row: Dict[str, Any]) -> T:
    """Rebuild a record model from a table row dict."""
    data = dict(row)
    emb = data.get("embedding")
    if emb is not None and not isinstance(emb, list):
        data["embedding"] = [float(x) for x in emb]  # pgvector returns a numpy array
    if model is EpisodicRecord:
        data["context"] = {
            "occurred_at": data.pop("occurred_at", None),
            "location": data.pop("location", None),
            "cause": data.pop("cause", None),
        }
    return model(**data)  # type: ignore[call-arg]


class MemoryStore(abc.ABC):
    """Abstract store. Call sites program against this, not a concrete backend."""

    @abc.abstractmethod
    def write(self, rec: Any) -> None: ...

    @abc.abstractmethod
    def read(self, model: Type[T], tenant_id: str, id: str) -> Optional[T]: ...

    @abc.abstractmethod
    async def awrite(self, rec: Any) -> None: ...

    @abc.abstractmethod
    async def aread(self, model: Type[T], tenant_id: str, id: str) -> Optional[T]: ...


class PgMemoryStore(MemoryStore):
    """PostgreSQL + pgvector adapter (SM-R3 default backend)."""

    def __init__(self, conninfo: str) -> None:
        self.conninfo = conninfo

    @staticmethod
    def _insert_sql(rec: Any) -> Tuple[str, List[Any]]:
        from psycopg.types.json import Jsonb

        table, cols = _TABLES[type(rec)]
        row = _to_row(rec)
        placeholders = ", ".join("%s" for _ in cols)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT (tenant_id, id) DO UPDATE SET "
            + ", ".join(
                f"{c} = EXCLUDED.{c}" for c in cols if c not in ("tenant_id", "id")
            )
        )
        # jsonb columns (metadata, properties) need explicit wrapping in psycopg3.
        vals = [Jsonb(row[c]) if isinstance(row[c], dict) else row[c] for c in cols]
        return sql, vals

    @staticmethod
    def _select_sql(model: Type[Any]) -> Tuple[str, List[str]]:
        table, cols = _TABLES[model]
        return (
            f"SELECT {', '.join(cols)} FROM {table} WHERE tenant_id = %s AND id = %s",
            cols,
        )

    # --- sync ---
    def write(self, rec: Any) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        sql, vals = self._insert_sql(rec)
        with psycopg.connect(self.conninfo) as conn:
            register_vector(conn)
            conn.execute(sql, vals)
            conn.commit()

    def read(self, model: Type[T], tenant_id: str, id: str) -> Optional[T]:
        import psycopg
        from pgvector.psycopg import register_vector

        sql, cols = self._select_sql(model)
        with psycopg.connect(self.conninfo) as conn:
            register_vector(conn)
            cur = conn.execute(sql, [tenant_id, id])
            row = cur.fetchone()
        if row is None:
            return None
        return _from_row(model, dict(zip(cols, row)))

    # --- async ---
    async def awrite(self, rec: Any) -> None:
        import psycopg
        from pgvector.psycopg import register_vector_async

        sql, vals = self._insert_sql(rec)
        async with await psycopg.AsyncConnection.connect(self.conninfo) as conn:
            await register_vector_async(conn)
            await conn.execute(sql, vals)
            await conn.commit()

    async def aread(self, model: Type[T], tenant_id: str, id: str) -> Optional[T]:
        import psycopg
        from pgvector.psycopg import register_vector_async

        sql, cols = self._select_sql(model)
        async with await psycopg.AsyncConnection.connect(self.conninfo) as conn:
            await register_vector_async(conn)
            cur = await conn.execute(sql, [tenant_id, id])
            row = await cur.fetchone()
        if row is None:
            return None
        return _from_row(model, dict(zip(cols, row)))


class InMemoryMemoryStore(MemoryStore):
    """Second adapter: proves the seam. Same interface, zero call-site changes."""

    def __init__(self) -> None:
        self._data: Dict[Tuple[str, str, str], Any] = {}

    def _key(self, rec_or_model: Any, tenant_id: str, id: str) -> Tuple[str, str, str]:
        table = _TABLES[
            rec_or_model if isinstance(rec_or_model, type) else type(rec_or_model)
        ][0]
        return (table, tenant_id, id)

    def write(self, rec: Any) -> None:
        self._data[self._key(rec, rec.tenant_id, rec.id)] = rec.model_copy(deep=True)

    def read(self, model: Type[T], tenant_id: str, id: str) -> Optional[T]:
        return self._data.get(self._key(model, tenant_id, id))

    async def awrite(self, rec: Any) -> None:
        self.write(rec)

    async def aread(self, model: Type[T], tenant_id: str, id: str) -> Optional[T]:
        return self.read(model, tenant_id, id)
