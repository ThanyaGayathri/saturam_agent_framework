"""Postgres + pgvector :class:`~egpt.memory.substrates.VectorSubstrate` (SM-V1 #95).

**The store and the layout are SM-R3's call, not this module's.** pgvector is the ratified default
-- one Postgres for memory and the relational/bi-temporal data, on-prem friendly, HNSW and hybrid
in the box. The layout matters more than the engine: shared-table ``tenant_id`` filtering is
rejected on measurement (recall 0.80 -> 0.56 -> 0.22 as tenants go 1 -> 10 -> 50), and declarative
partitioning by tenant is the option that *improves* with tenant count. ADR 0003 section A.1
ratifies both.

That is why this does not wrap ``egpt.integrations.vectordb.pgvector.PgVector``: that adapter
implements exactly the rejected layout -- one shared table, tenant as a JSONB metadata filter, and
no way to express partitioning -- so reusing it would ship the arrangement the benchmark ruled out.
What is reused is pgvector itself, its HNSW index, and the seam's own score normalization.

Deliberately **not** here, each with an owner: the embedder and its model, dimension and
normalization choices, and the ``HuggingfaceCustomEmbedder`` refusal for Mode A (SM-V2 #96); rank
fusion and its knobs (SM-H2 #132); metadata predicates for ``MemoryQuery.filters``, which need a
contract on the graph port too (see :mod:`egpt.memory.types`); re-syncing this projection when the
graph invalidates a fact, which has no call site until contradiction *detection* exists (SM-G5
#103); and the Qdrant escalation configuration (SM-V4 #98).
"""

import json
import os
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple
from contextlib import contextmanager

from egpt.integrations.memory.vector.pgvector import sql
from egpt.knowledge.embedder import Embedder
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryQuery, MemoryRecord, Scope, Substrate
from egpt.utils.dttm import now_epoch_s
from egpt.utils.log import log_debug
from egpt.vectordb.distance import Distance
from egpt.vectordb.score import normalize_score

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

try:
    from sqlalchemy import text
    from sqlalchemy.engine import URL, Engine, create_engine, make_url
    from sqlalchemy.exc import DataError, IntegrityError, NotSupportedError, ProgrammingError
    from sqlalchemy.ext.asyncio import create_async_engine
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Please install using `pip install sqlalchemy psycopg`")

__all__ = ["PgVectorMemorySubstrate"]

#: The one driver this substrate accepts. psycopg 3 serves **both** halves of the port from one
#: URL; psycopg2 has no async support and asyncpg no sync, so either would leave one half of every
#: method unimplementable or silently thread-hopped.
_DRIVER = "postgresql+psycopg"

#: HNSW search breadth, and **measured rather than inherited**. SM-R3 benchmarked at a constant 64
#: and reported recall 0.881 at the ratified 100k/tenant operating point -- below ADR 0003 section
#: A.3b's ratified 0.95 -- saying only that a higher ``ef`` narrows the gap "at some latency cost".
#: The sweep that ruling requires of this ticket is ``benchmarks/memory_vectordb/ef_sweep.py``: at
#: 100k/tenant it reproduces 0.889 at 64 and reaches **0.967 at 256** for a p95 of 7.1 ms, well
#: inside the 50 ms budget. So 256 is the default, and it stays a per-deployment parameter because
#: the trade it sits on moves with corpus, dimension and hardware.
_DEFAULT_EF_SEARCH = 256

#: Deterministic, server-side failures: the same statement fails the same way on every retry, so
#: the layers above should stop rather than retry. Transient and transport errors -- a dropped
#: connection, a serialization failure, an unreachable host -- are **not** here and propagate
#: unchanged, because the provider reads ``MemoryOperationError`` as "this will never work"
#: (``co_located._raise_write_failure``) and would tell a caller to give up on a network blip.
_DETERMINISTIC = (ProgrammingError, IntegrityError, DataError, NotSupportedError)


def _as_json(value: Any, field: str) -> str:
    """Serialize a nested value, in this module's error envelope rather than the stdlib's.

    Both drivers are sent JSON *text* rather than a ``dict`` (see ``sql._CASTS``), so this is on
    every write path; a bare ``TypeError`` from ``json`` would name neither the field nor the row.
    """
    try:
        return json.dumps(value)
    except TypeError as exc:
        raise MemoryOperationError(
            f"{field} is not JSON-serializable ({exc}).",
            field_name=field,
        ) from exc


def _from_json(value: Any, field: str, record_id: Any) -> Dict[str, Any]:
    """Decode a stored nested value, tolerating both drivers' return shapes.

    psycopg decodes ``jsonb`` to a ``dict`` and asyncpg hands back the raw text. Normalising here
    is what keeps the two halves' records **equal** rather than merely both non-empty -- an
    asymmetry that a test comparing one half against itself would never see.
    """
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return dict(value)
    try:
        return dict(json.loads(value))
    except (TypeError, ValueError) as exc:
        raise MemoryOperationError(
            f"{field} on record {record_id!r} is not readable JSON ({exc}). A row written outside "
            "this backend will not round-trip.",
            field_name=field,
        ) from exc


def _rows(result: Any) -> List[Dict[str, Any]]:
    """A statement's rows, or an empty list for one that returns none.

    ``SET LOCAL hnsw.ef_search`` shares a transaction with the search it configures, and asking a
    non-row-returning result for its rows raises ``ResourceClosedError`` rather than yielding
    nothing -- so the tuning statement would take down the read it exists to tune.
    """
    if not result.returns_rows:
        return []
    return [dict(row) for row in result.mappings()]


def _topics(value: Any, record_id: Any) -> Optional[List[str]]:
    """``topics`` round-trips as a JSON array, and ``NULL`` is not the same as ``[]``."""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(topic) for topic in value]
    try:
        return [str(topic) for topic in json.loads(value)]
    except (TypeError, ValueError) as exc:
        raise MemoryOperationError(
            f"topics on record {record_id!r} is not a readable JSON array ({exc}).",
            field_name="topics",
        ) from exc


def _vector_literal(embedding: Sequence[float], dim: int, record_id: str) -> str:
    """The pgvector text form, with the width checked **before** the round trip.

    Postgres would reject a wrong-width vector itself, but only after the batch has travelled, and
    with a message naming the column rather than the record. The dimension is a per-deployment
    constant that SM-C3's ``Embeddable`` could not validate against a column width, because the
    typed column is this ticket's.
    """
    if len(embedding) != dim:
        raise MemoryOperationError(
            f"Embedding for record {record_id!r} has {len(embedding)} dimensions; this store's "
            f"column is vector({dim}). Re-embedding at a new dimension is a versioned migration "
            "(SM-V2 #96), not a per-write coercion.",
            field_name="embedding",
        )
    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"


class PgVectorMemorySubstrate:
    """Memory vectors in Postgres, one partition and one HNSW index per tenant.

    Sync and async are both native: the async half drives SQLAlchemy's async engine on the same
    psycopg 3 driver, because a thread hop is a sync call wearing an ``async def``.
    """

    def __init__(
        self,
        db_url: Optional[str] = None,
        embedder: Optional[Any] = None,
        embedder_options: Optional[Dict[str, Any]] = None,
        dim: Optional[int] = None,
        schema: str = "ai",
        table: str = "memory_vectors",
        m: int = 16,
        ef_construction: int = 200,
        ef_search: int = _DEFAULT_EF_SEARCH,
        embedding_version: Optional[str] = None,
        auto_init_schema: bool = True,
    ) -> None:
        resolved_url = db_url or os.getenv("EGPT_MEMORY_PG_URL")
        if not resolved_url:
            raise MemoryProviderConfigError(
                "PgVectorMemorySubstrate needs a Postgres URL, from db_url or EGPT_MEMORY_PG_URL.",
                field_name="db_url",
            )
        self.db_url: URL = self._normalized(resolved_url)
        self.schema, self.table = sql.identifier(schema, "schema"), sql.identifier(table, "table")
        self.embedder = self._resolved_embedder(embedder, embedder_options)
        self.dim = self._resolved_dim(dim, self.embedder)
        self.ef_search = ef_search
        self.embedding_version = embedding_version
        # Validated at construction rather than at first read: `ef_search` reaches a statement as a
        # literal, and the rest reach DDL, so this is where a bad value is cheap.
        sql.ef_search_statement(ef_search)
        self._schema_statements = sql.schema_statements(self.schema, self.table, self.dim, m, ef_construction)
        self._engine: Engine = create_engine(self.db_url, pool_pre_ping=True, pool_recycle=3600)
        self._aengine: Optional["AsyncEngine"] = None
        #: Tenants whose partition this process has already created. A cache, never an authority:
        #: `IF NOT EXISTS` still runs for a tenant another process created first.
        self._partitions: Set[str] = set()
        if auto_init_schema:
            self.init_schema()

    # -- construction-time validation -----------------------------------------

    @staticmethod
    def _normalized(db_url: str) -> "URL":
        """Force the one driver that can serve both halves, refusing rather than silently swapping.

        A URL naming psycopg2 or asyncpg is a deployment's explicit choice, and quietly running on
        a different driver than the one configured is how a connection setting -- a pool size, an
        SSL mode, a statement timeout -- stops applying with nothing to show for it.

        Returns the ``URL`` **object**, never a string. ``str(URL)`` renders the password as
        ``***`` -- rendering and re-parsing it produced an engine that authenticated with three
        asterisks, which the real backend rejected and no double could have. Keeping the object
        also means the credential never sits in an attribute as plain text, since ``repr`` and
        ``str`` both mask it.
        """
        try:
            url = make_url(db_url)
        except Exception as exc:
            raise MemoryProviderConfigError(f"db_url is not a valid database URL: {exc}", field_name="db_url") from exc
        if url.drivername not in ("postgresql", _DRIVER):
            raise MemoryProviderConfigError(
                f"db_url names the {url.drivername!r} driver; this substrate needs {_DRIVER!r}, which is "
                "the only one that serves both the sync and async halves of the port from one URL. "
                "psycopg2 has no async support and asyncpg no sync.",
                field_name="db_url",
            )
        return url.set(drivername=_DRIVER)

    @staticmethod
    def _resolved_embedder(embedder: Optional[Any], options: Optional[Dict[str, Any]]) -> Embedder:
        """The injected embedder, or one built from a registry name so config alone can construct.

        Which embedder a deployment should use -- and the ADR section A.3c trap that
        ``HuggingfaceCustomEmbedder`` is a *remote* client despite being listed as self-hostable --
        is SM-V2's (#96). This only insists that there is one: a vector store with no embedder has
        nothing to index and would fail at the first write instead of at construction.
        """
        if isinstance(embedder, str):
            from egpt.registry import discovery

            embedder = discovery.construct("embedder", embedder, **(options or {}))
        elif options:
            raise MemoryProviderConfigError(
                "embedder_options applies only when 'embedder' names a registered embedder to build; "
                "an already-built instance is configured by its own constructor.",
                field_name="embedder_options",
            )
        if embedder is None:
            raise MemoryProviderConfigError(
                "PgVectorMemorySubstrate needs an embedder -- an Embedder instance, or the name of a "
                "registered one. Choosing the model is SM-V2's (#96).",
                field_name="embedder",
            )
        for method in ("get_embedding", "async_get_embedding"):
            if not callable(getattr(embedder, method, None)):
                raise MemoryProviderConfigError(
                    f"{type(embedder).__name__} has no callable {method}(); both halves of this "
                    "substrate embed, so an embedder missing either one leaves one half broken.",
                    field_name="embedder",
                )
        return embedder

    @staticmethod
    def _resolved_dim(dim: Optional[int], embedder: Embedder) -> int:
        """The column width: stated, or taken from the embedder -- and refused when they disagree.

        A mismatch is caught here rather than by Postgres on the first insert, because by then the
        table exists at the wrong width and every write fails until it is dropped.
        """
        declared = getattr(embedder, "dimensions", None)
        if dim is None:
            if not isinstance(declared, int) or declared <= 0:
                raise MemoryProviderConfigError(
                    f"dim is unset and {type(embedder).__name__}.dimensions is {declared!r}, so the "
                    "vector column has no width. Pass dim explicitly.",
                    field_name="dim",
                )
            return declared
        if isinstance(declared, int) and declared > 0 and declared != dim:
            raise MemoryProviderConfigError(
                f"dim is {dim} but {type(embedder).__name__}.dimensions is {declared}. One of them is "
                "wrong, and either way every write would be refused by the column.",
                field_name="dim",
            )
        return dim

    # -- plumbing -------------------------------------------------------------

    def _async_engine(self) -> "AsyncEngine":
        """Built on first use, not in ``__init__``: an async engine binds to the running loop."""
        if self._aengine is None:
            self._aengine = create_async_engine(self.db_url, pool_pre_ping=True, pool_recycle=3600)
        return self._aengine

    @staticmethod
    @contextmanager
    def _translated(operation: str) -> Iterator[None]:
        """The one place a driver failure becomes this module's error, or deliberately does not.

        The split follows **retryability**, not the exception's origin: a rejected statement, a
        constraint violation or a bad value fails identically on every attempt and becomes
        ``MemoryOperationError``; everything else -- a dropped connection, an unreachable host --
        propagates unchanged so the layers above can still tell a broken query from a broken link.
        """
        try:
            yield
        except _DETERMINISTIC as exc:
            raise MemoryOperationError(f"The vector backend refused {operation}: {exc}", field_name=operation) from exc

    def _statements(self, statements: Sequence[Tuple[str, Dict[str, Any]]]) -> List[List[Dict[str, Any]]]:
        """Run statements in **one** transaction, returning each one's rows.

        One transaction rather than one per statement, so the ``SET LOCAL hnsw.ef_search`` that
        precedes a search is still in force when the search runs, and cannot leak into another.
        """
        with self._translated("read/write"), self._engine.begin() as connection:
            return [_rows(connection.execute(text(body), binds)) for body, binds in statements]

    async def _astatements(self, statements: Sequence[Tuple[str, Dict[str, Any]]]) -> List[List[Dict[str, Any]]]:
        """Async twin of :meth:`_statements`, with the same one-transaction rule."""
        with self._translated("read/write"):
            async with self._async_engine().begin() as connection:
                return [_rows(await connection.execute(text(body), binds)) for body, binds in statements]

    def _write(self, statement: str, rows: List[Dict[str, Any]]) -> None:
        """Apply one statement to every row, in **one** transaction and one round trip.

        A batch is all-or-nothing here. The provider's ``write`` fans out across substrates and
        already has no distributed transaction, so it reports a partial fan-out as
        ``PartialWriteError``; a batch that could also half-apply *within* one substrate would
        leave a retry unable to make the two agree by reusing ids.
        """
        with self._translated("write"), self._engine.begin() as connection:
            connection.execute(text(statement), rows)

    async def _awrite(self, statement: str, rows: List[Dict[str, Any]]) -> None:
        """Async twin of :meth:`_write`, with the same all-or-nothing rule."""
        with self._translated("write"):
            async with self._async_engine().begin() as connection:
                await connection.execute(text(statement), rows)

    def init_schema(self) -> None:
        """Create the extension, the partitioned parent and its indexes, idempotently."""
        with self._translated("schema init"), self._engine.begin() as connection:
            for statement in self._schema_statements:
                connection.execute(text(statement))
        log_debug(f"PgVectorMemorySubstrate schema ready on {self.schema}.{self.table}")

    async def ainit_schema(self) -> None:
        """Async twin of :meth:`init_schema`."""
        with self._translated("schema init"):
            async with self._async_engine().begin() as connection:
                for statement in self._schema_statements:
                    await connection.execute(text(statement))
        log_debug(f"PgVectorMemorySubstrate schema ready on {self.schema}.{self.table}")

    def _partition_binds(self, tenant_id: str) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "partition": sql.partition_name(self.table, tenant_id),
            "parent": self.table,
            "tenant_id": tenant_id,
        }

    #: Whether a relation exists, asked by name. Used only to tell a lost creation race from a real
    #: DDL failure, which is why it is a separate transaction: the failed one is already aborted.
    _EXISTS = "SELECT to_regclass(:qualified) IS NOT NULL"

    def ensure_partition(self, tenant_id: str) -> None:
        """Create this tenant's partition, which inherits the parent's indexes.

        A **lost race is not an error**: two workers writing a new tenant's first memories both run
        this, and ``CREATE TABLE IF NOT EXISTS`` still raises ``DuplicateTable`` when the other
        transaction commits between its existence check and its create. The partition exists either
        way, which is the only thing the caller wanted. Distinguished from a real DDL failure by
        asking whether it is there now, rather than by matching on the message text.
        """
        if tenant_id in self._partitions:
            return
        binds = self._partition_binds(tenant_id)
        try:
            with self._translated("partition create"), self._engine.begin() as connection:
                ddl = connection.execute(text(sql.create_partition_statement()), binds).scalar()
                connection.execute(text(str(ddl)))
        except MemoryOperationError:
            with self._translated("partition probe"), self._engine.begin() as connection:
                present = connection.execute(
                    text(self._EXISTS), {"qualified": self._qualified_partition(binds)}
                ).scalar()
            if not present:
                raise
        self._partitions.add(tenant_id)

    async def aensure_partition(self, tenant_id: str) -> None:
        """Async twin of :meth:`ensure_partition`, with the same lost-race rule."""
        if tenant_id in self._partitions:
            return
        binds = self._partition_binds(tenant_id)
        try:
            with self._translated("partition create"):
                async with self._async_engine().begin() as connection:
                    result = await connection.execute(text(sql.create_partition_statement()), binds)
                    await connection.execute(text(str(result.scalar())))
        except MemoryOperationError:
            with self._translated("partition probe"):
                async with self._async_engine().begin() as connection:
                    probe = await connection.execute(
                        text(self._EXISTS), {"qualified": self._qualified_partition(binds)}
                    )
                    present = probe.scalar()
            if not present:
                raise
        self._partitions.add(tenant_id)

    @staticmethod
    def _qualified_partition(binds: Dict[str, Any]) -> str:
        return f"{binds['schema']}.{binds['partition']}"

    def close(self) -> None:
        """Close the sync engine, and **refuse** when an async one is still open.

        Dropping the reference is not closing: it leaves the pool's sockets open and permanently
        disables :meth:`aclose`'s own guard, so the leak becomes unreachable rather than fixed.
        Closing it here is not possible either -- an async engine's connections belong to the loop
        that opened them, so a sync call can neither await them nor drive that loop.
        """
        if self._aengine is not None:
            raise MemoryOperationError(
                "This substrate has an open async engine, and close() cannot release it: its "
                "connections belong to the loop that opened them. Call aclose() from the same async "
                "context; it closes both.",
                field_name="close",
            )
        self._engine.dispose()

    async def aclose(self) -> None:
        """Closes both halves: the sync engine is opened in ``__init__`` whichever half is used."""
        if self._aengine is not None:
            await self._aengine.dispose()
            self._aengine = None
        self._engine.dispose()

    # -- row mapping ----------------------------------------------------------

    def _reject_foreign_scope(self, scope: Scope, record: MemoryRecord) -> None:
        """A row is written under the scope it carries; a mismatch is refused, never reinterpreted.

        The mismatch has two defensible readings -- honour the call scope, or honour the record's --
        and a caller cannot tell which it got. Under the first, a fact derived from one user's
        episodes is filed tenant-wide and every user in the tenant can read it.
        """
        if record.scope != scope:
            # Slot names, never the values: this fires on a *foreign* scope by definition, so the
            # values are exactly the identifiers that must not reach a log line.
            differing = [
                slot for slot in sql.SCOPE_SLOTS if getattr(record.scope, slot, None) != getattr(scope, slot, None)
            ]
            raise MemoryOperationError(
                f"Record {record.id!r} carries a scope that does not match the write scope; "
                f"{len(differing)} slot(s) differ: {', '.join(differing)}. Batch per scope rather "
                "than leaving the choice to the backend.",
                field_name="scope",
            )

    def _row(self, scope: Scope, record: MemoryRecord, embedding: Sequence[float]) -> Dict[str, Any]:
        """One stored row: the scope columns, the bi-temporal envelope, and the vector.

        ``expired_at`` and ``superseded_by`` are stored exactly as written, because this method is
        also how a projection is *retired* -- a backend that dropped them would make retirement a
        silent no-op and serve two current beliefs for one logical record.
        """
        return {
            "tenant_id": scope.tenant_id,
            "id": record.id,
            "tier": record.tier.value,
            "content": record.content,
            "agent_id": scope.agent_id,
            "user_id": scope.user_id,
            "team_id": scope.team_id,
            "session_id": scope.session_id,
            "topics": None if record.topics is None else _as_json(list(record.topics), "topics"),
            "provenance": _as_json(record.provenance, "provenance"),
            "metadata": _as_json(record.metadata, "metadata"),
            # Stamped, not filtered on. A read narrowed to the configured version would return
            # nothing at all the moment a deployment changed models -- the silently-empty answer
            # this seam refuses everywhere else. Cutting over between versions is a re-embedding
            # migration, and it is SM-V2's (#96); this column is what makes one auditable.
            "embedding_version": self.embedding_version,
            "t_valid": record.t_valid,
            "t_invalid": record.t_invalid,
            "created_at": record.created_at if record.created_at is not None else now_epoch_s(),
            "expired_at": record.expired_at,
            "superseded_by": record.superseded_by,
            "embedding": _vector_literal(embedding, self.dim, record.id),
        }

    def _record(self, row: Dict[str, Any]) -> MemoryRecord:
        distance = row.get("distance")
        return MemoryRecord(
            id=row["id"],
            tier=MemoryTier(row["tier"]),
            content=row["content"],
            scope=Scope(
                tenant_id=row["tenant_id"],
                agent_id=row["agent_id"],
                user_id=row["user_id"],
                team_id=row["team_id"],
                session_id=row["session_id"],
            ),
            # The port's contract is a similarity in [0.0, 1.0], higher better, because the provider
            # *adds* the legs' scores: pgvector's `<=>` is a cosine **distance** where lower is
            # better, so handing it over raw would invert this leg's contribution to the ranking.
            # `scan` does not rank, and reports no score rather than a fabricated one.
            score=None if distance is None else normalize_score(float(distance), Distance.cosine),
            substrate=Substrate.VECTOR,
            topics=_topics(row.get("topics"), row.get("id")),
            provenance=_from_json(row.get("provenance"), "provenance", row.get("id")),
            t_valid=row.get("t_valid"),
            t_invalid=row.get("t_invalid"),
            created_at=row.get("created_at"),
            expired_at=row.get("expired_at"),
            superseded_by=row.get("superseded_by"),
            metadata=_from_json(row.get("metadata"), "metadata", row.get("id")),
        )

    @staticmethod
    def _ranked(rows: List[Dict[str, Any]], records: List[MemoryRecord]) -> List[MemoryRecord]:
        """Order the returned page deterministically, closest first.

        The server ordered by the bare distance operator so the HNSW index could answer the
        ``ORDER BY ... LIMIT`` -- a secondary sort key there can drop the plan to a sequential scan,
        which is the whole failure SM-R3 Finding 3 describes. Ties inside the page are broken here,
        on ``created_at`` then content then id: **not** on a UUID, which would make the order of two
        equally-similar memories depend on which random id they were minted with.
        """
        paired = sorted(
            zip(rows, records),
            key=lambda pair: (
                float(pair[0]["distance"]) if pair[0].get("distance") is not None else 0.0,
                pair[1].created_at or 0,
                pair[1].content,
                pair[1].id,
            ),
        )
        return [record for _, record in paired]

    # -- writes ---------------------------------------------------------------

    def _checked(self, scope: Scope, records: Sequence[MemoryRecord]) -> List[MemoryRecord]:
        """Refuse the batch before embedding any of it -- a rejected record must cost no API call."""
        checked = list(records)
        for record in checked:
            self._reject_foreign_scope(scope, record)
        return checked

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        if not records:
            return
        checked = self._checked(scope, records)
        embeddings = [self.embedder.get_embedding(record.content) for record in checked]
        self.ensure_partition(scope.tenant_id)
        rows = [self._row(scope, record, embedding) for record, embedding in zip(checked, embeddings)]
        self._write(sql.upsert_statement(self.schema, self.table), rows)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        if not records:
            return
        checked = self._checked(scope, records)
        # Sequential, like the sync half. Embedding a batch concurrently is a real speed-up and a
        # different decision -- one this port cannot make for every embedder, since an embedder is
        # free to be a single local model with no concurrency at all -- so batching belongs with
        # the embedder pipeline (SM-V2 #96) rather than being taken here on one half only.
        embeddings = [await self.embedder.async_get_embedding(record.content) for record in checked]
        await self.aensure_partition(scope.tenant_id)
        rows = [self._row(scope, record, embedding) for record, embedding in zip(checked, embeddings)]
        await self._awrite(sql.upsert_statement(self.schema, self.table), rows)

    # -- reads ----------------------------------------------------------------

    @staticmethod
    def _searchable(query: MemoryQuery, tier: Optional[MemoryTier]) -> Optional[List[str]]:
        """The tier set to search, or ``None`` when the query cannot match anything.

        An empty ``text`` has no embedding worth taking: the nearest neighbours of whatever a model
        returns for the empty string are noise ranked as if they were answers. An empty resolved
        tier set is a coherent request with an empty answer (``MemoryQuery.resolved_tiers``), and
        ``limit=0`` asks for no rows -- all three are answered without a round trip.
        """
        if not query.limit or not query.text.strip():
            return None
        tiers = [tier.value] if tier is not None else [t.value for t in query.resolved_tiers()]
        return tiers or None

    def _search_call(
        self, scope: Scope, query: MemoryQuery, tiers: List[str], embedding: Sequence[float]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """The ``SET LOCAL`` and the query, as one transaction's worth of statements.

        ``ef_search`` is per-deployment rather than the benchmark's constant 64: ADR section A.3b
        ratifies ``Recall@10 >= 0.95`` *conditionally*, on a sweep at the deployed per-tenant size
        reporting recall and p95 together, because SM-R3 shows only that a higher ``ef`` narrows
        the gap at some latency cost -- not that it closes.
        """
        statement, binds = sql.search_statement(self.schema, self.table, scope)
        binds.update(
            {
                "tiers": tiers,
                "limit": query.limit,
                "query_embedding": _vector_literal(embedding, self.dim, "<query>"),
            }
        )
        return [(sql.ef_search_statement(self.ef_search), {}), (statement, binds)]

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        tiers = self._searchable(query, tier)
        if tiers is None:
            return []
        embedding = self.embedder.get_embedding(query.text)
        rows = self._statements(self._search_call(scope, query, tiers, embedding))[-1]
        return self._ranked(rows, [self._record(row) for row in rows])

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        tiers = self._searchable(query, tier)
        if tiers is None:
            return []
        embedding = await self.embedder.async_get_embedding(query.text)
        rows = (await self._astatements(self._search_call(scope, query, tiers, embedding)))[-1]
        return self._ranked(rows, [self._record(row) for row in rows])

    @staticmethod
    def _scan_call(
        schema: str, table: str, scope: Scope, tier: MemoryTier, limit: Optional[int]
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """The scan statement, or ``None`` for ``limit=0`` -- a bound of zero rows, not "unlimited".

        ``None`` means unlimited and ``0`` means empty; conflating them turns a caller's "take
        nothing this pass" into a full-tier walk.
        """
        if limit == 0:
            return None
        statement, binds = sql.scan_statement(schema, table, scope, limit)
        binds["tier"] = tier.value
        return statement, binds

    def scan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        call = self._scan_call(self.schema, self.table, scope, tier, limit)
        if call is None:
            return []
        return [self._record(row) for row in self._statements([call])[0]]

    async def ascan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        call = self._scan_call(self.schema, self.table, scope, tier, limit)
        if call is None:
            return []
        return [self._record(row) for row in (await self._astatements([call]))[0]]
