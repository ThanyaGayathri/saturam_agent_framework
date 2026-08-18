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

The embedder, its model, its dimension and the Mode A air-gap refusal are **not decided here**:
they come from :mod:`egpt.memory.embedding` (SM-V2 #96), which this store holds as ``pipeline``.
Both the write and the read path embed through it, so a deployment cannot index with one model and
query with another.

Deliberately **not** here, each with an owner: rank
fusion and its knobs (SM-H2 #132); metadata predicates for ``MemoryQuery.filters``, which need a
contract on the graph port too (see :mod:`egpt.memory.types`); re-syncing this projection when the
graph invalidates a fact, which has no call site until contradiction *detection* exists (SM-G5
#103); and the Qdrant escalation configuration (SM-V4 #98).
"""

import json
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from egpt.integrations.memory.vector.pgvector import sql
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.embedding import resolve_memory_embedder
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryQuery, MemoryRecord, Scope, Substrate
from egpt.providers.model_provider import ProviderMode
from egpt.utils.dttm import now_epoch_s
from egpt.utils.log import log_debug, log_warning
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
    """Decode a stored nested value into the mapping ``MemoryRecord`` expects.

    psycopg decodes ``jsonb`` for us, so a column this backend wrote arrives as a ``dict`` and the
    ``json.loads`` path below is for a row written by something else -- a migration, a bulk loader,
    an operator's ``UPDATE`` -- which may hold text.

    **Only a ``dict`` is passed through.** psycopg decodes a jsonb *array* to a ``list``, and the
    first version accepted that alongside ``dict``, so ``dict(["a", "b"])`` raised a bare
    ``ValueError`` out of the middle of a read -- the one unshaped failure left in the module.
    Letting a list fall through to ``json.loads`` is what turns it into the named refusal below.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(json.loads(value))
    except (TypeError, ValueError) as exc:
        raise MemoryOperationError(
            f"{field} on record {record_id!r} did not decode to a JSON object ({exc}). This column "
            "holds a mapping; a row written outside this backend will not round-trip.",
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


def _similar(row: Dict[str, Any]) -> bool:
    """Whether a hit is a match at all -- cosine distance strictly under 1.0.

    A distance of 1.0 is orthogonality and above it is opposition, both of which normalise to a
    similarity of 0.0.

    **A row with no distance is refused, not passed through.** Only ranked reads reach here, and
    every one of their rows carries the operator's result; a ``scan`` row does not, and letting one
    through was worse than either alternative -- ``_ranked`` sorted a missing distance as ``0.0``,
    which is a *perfect* match, so an unranked row would have led the page. Dropping it silently
    would be the other half of the same mistake, so this says which contract was broken.
    """
    distance = row.get("distance")
    if distance is None:
        raise MemoryOperationError(
            "A ranked row carries no distance. Only search results are ranked; scan rows are "
            "returned in insertion order and must not reach the relevance filter.",
            field_name="distance",
        )
    return float(distance) < 1.0


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

    **Migration note (SM-V2 #96).** Before this ticket, ``embedder`` was passed straight to a
    resolver with no offline-capability check, so any injected instance -- a Bedrock wrapper, a
    fine-tuned in-house client, anything not in :data:`~egpt.memory.embedding.OFFLINE_CAPABLE` --
    worked regardless of deployment mode. ``mode`` is new, and it **defaults to**
    ``ProviderMode.LOCAL`` (Mode A, air-gapped), which now enforces ADR 0003 A.3c at construction:
    an embedder that is neither a recognised offline-capable key nor an instance declaring
    ``offline_capable = True`` is refused, not silently accepted. A deployment upgrading from a
    pre-SM-V2 version with such an embedder already injected gets a
    :class:`~egpt.memory.config.MemoryProviderConfigError` where it previously got nothing --
    correct for Mode A, but a behaviour change with no runtime warning. Fix by either declaring
    ``offline_capable = True`` on the embedder class (asserting it is genuinely offline), or by
    passing ``mode=ProviderMode.FRONTIER`` explicitly if the embedder is meant to reach a provider.
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
        mode: ProviderMode = ProviderMode.LOCAL,
    ) -> None:
        resolved_url = db_url or os.getenv("EGPT_MEMORY_PG_URL")
        if not resolved_url:
            raise MemoryProviderConfigError(
                "PgVectorMemorySubstrate needs a Postgres URL, from db_url or EGPT_MEMORY_PG_URL.",
                field_name="db_url",
            )
        self.db_url: URL = self._normalized(resolved_url)
        # Both names arrive from deployment config, so a bad one is a deployment fault and reaches
        # the caller as this seam's config error rather than as a bare `ValueError` from a statement
        # builder. `table_identifier` rather than `identifier` for the second: a 63-byte table name
        # is a legal relation but not a legal *base* for one, and it is the derived names that fail
        # -- at construction for the two indexes, and at the first write of each new tenant for a
        # partition, which is far from the setting that caused it.
        # The pipeline decides the model, its dimension and the version tag; this store only holds
        # the column they imply (SM-V2 #96). It is also where Mode A's air-gap refusal lives, so
        # resolving it here is what makes `mode` more than a stored attribute.
        self.mode = mode
        self.pipeline = resolve_memory_embedder(mode, embedder, embedder_options, dim, dimensions_field="dim")
        #: The raw embedder, unchanged, so anything reading it sees what it saw before the pipeline
        #: was interposed. Writes and queries go through `self.pipeline`, which batches.
        self.embedder = self.pipeline.embedder
        self.ef_search = ef_search
        # Derived from the pipeline rather than defaulting to None, so every row is stamped with the
        # model that actually produced it and a re-embedding migration has a real value to key its
        # cutover on (SM-R4 section 7, D8). An explicit argument still wins: a deployment mid-
        # migration needs to pin the tag it writes under.
        #
        # `is not None`, not `or`: those differ on exactly the caller who is most deliberate about
        # this field. A migration tool passing `""` to clear the tag is the one case where the
        # falsy test silently substitutes the derived value -- writing rows stamped with the current
        # model under a tool that asked for no stamp at all, which is the opposite of what it
        # requested and invisible until the cutover query returns the wrong set.
        self.embedding_version = embedding_version if embedding_version is not None else self.pipeline.embedding_version
        # Every setting is checked here rather than at first use, and all of them inside one
        # wrapper: `ef_search` reaches a statement as a literal and the rest reach DDL, so this is
        # where a bad value is cheap -- and a refusal that arrives as a bare `ValueError` is one a
        # caller narrowing to `MemoryProviderConfigError` never sees.
        self._index_options = (m, ef_construction)
        with self._settings_checked():
            self.schema = sql.identifier(schema, "schema")
            self.table = sql.table_identifier(table)
            #: One dimension authority. Taken from the pipeline rather than re-derived beside it --
            #: a column width restated next to the model that determines it drifts the first time
            #: the model changes.
            self.dim = self.pipeline.dimensions
            sql.ef_search_statement(ef_search)
            self._schema_statements = sql.schema_statements(self.schema, self.table, self.dim, m, ef_construction)
            self._live_schema = sql.live_schema_statements(self.schema, self.table)
        self._engine: Engine = create_engine(self.db_url, pool_pre_ping=True, pool_recycle=3600)
        self._aengine: Optional["AsyncEngine"] = None
        #: Tenants whose partition this process has already created. A cache, never an authority:
        #: `IF NOT EXISTS` still runs for a tenant another process created first.
        self._partitions: Set[str] = set()
        if auto_init_schema:
            self.init_schema()

    # -- construction-time validation -----------------------------------------

    @staticmethod
    @contextmanager
    def _settings_checked() -> Iterator[None]:
        """Re-raise any setting this backend rejects as a **configuration** fault.

        Every construction-time check runs inside this, not just the two identifiers the first
        version wrapped. ``MemoryProviderConfigError`` is a ``ValueError`` subclass and not the
        reverse, so a caller narrowing to it -- to read ``field_name``, which only it carries --
        silently missed ``ef_search``, ``dim``, ``m`` and ``ef_construction``, each of which left
        here as a bare ``ValueError``. Through ``build_memory_provider`` that was invisible, since
        ``resolve_substrate`` re-wraps whatever a constructor raises; direct injection through
        ``options['vector_substrate']`` is a supported path and got the raw error.

        The field comes from :class:`sql.SettingError`, so a validator added to that module later
        is covered without being listed here -- which is the failure this replaced, at one remove.
        """
        try:
            yield
        except sql.SettingError as exc:
            raise MemoryProviderConfigError(str(exc), field_name=exc.field) from exc

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

    # -- plumbing -------------------------------------------------------------

    def _async_engine(self) -> "AsyncEngine":
        """Built on first use, not in ``__init__``: an async engine binds to the running loop.

        Unlocked, and that is load-bearing rather than an omission: there is no ``await`` between
        the check and the assignment, so no other coroutine on this loop can interleave. A lock
        would be the wrong shape anyway -- an ``asyncio.Lock`` binds to a loop as surely as the
        engine does, so it cannot protect the case it looks like it protects. **Two threads driving
        two loops through one substrate would race here**, each building an engine and the second
        overwriting the first's pool; that arrangement needs a substrate per loop, since a
        connection from one loop's pool is unusable on the other regardless.
        """
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

    def _reject_schema_drift(self, read_back: List[List[Dict[str, Any]]]) -> None:
        """Compare what the DDL asked for against what the table actually is.

        ``CREATE TABLE IF NOT EXISTS`` does not reconcile an existing table with the definition
        just submitted, so a deployment that changes its embedder and restarts came up **clean**
        and then failed on every write with Postgres' own ``expected 8 dimensions, not 768`` --
        raised from a write, naming a column, three layers below the setting that caused it. This
        is the same "refuse at construction, not at first use" rule the rest of ``__init__``
        follows; the width just happens to live in the database rather than in an argument.

        **The dimension is refused and the index options are only reported**, which is a real
        distinction rather than a hedge: a stale ``vector(n)`` makes every write fail, while a
        stale ``m`` or ``ef_construction`` still answers correctly at a different recall/latency
        point. Refusing the second would brick a running deployment over a tuning parameter.
        Rebuilding an index is a maintenance operation this backend deliberately does not perform
        -- it would lock the table for the duration -- so the honest thing is to say the setting is
        not live rather than to imply it was applied.
        """
        stored = read_back[0][0]["dim"] if read_back[0] else None
        # -1 is Postgres' "no typmod": a `vector` column with no declared width, which nothing here
        # creates but a hand-rolled table might.
        if stored is not None and stored != -1 and stored != self.dim:
            raise MemoryProviderConfigError(
                f"{self.schema}.{self.table} holds vector({stored}) and this substrate is configured "
                f"for {self.dim}. CREATE TABLE IF NOT EXISTS leaves an existing table as it is, so "
                "starting up would succeed and every write would then fail. Moving to a new "
                "dimension is a re-embedding migration (SM-V2 #96), not a restart.",
                field_name="dim",
            )
        wanted = {f"m={self._index_options[0]}", f"ef_construction={self._index_options[1]}"}
        for row in read_back[1]:
            live = set(row["options"] or ())
            if live and live != wanted:
                log_warning(
                    f"{row['name']} was built with {sorted(live)} and this substrate is configured "
                    f"for {sorted(wanted)}. The existing index is used as it stands -- these "
                    "settings apply only when the index is first created, and rebuilding one is a "
                    "locking operation this backend does not perform."
                )

    def init_schema(self) -> None:
        """Create the extension, the partitioned parent and its indexes, idempotently."""
        with self._translated("schema init"), self._engine.begin() as connection:
            for statement in self._schema_statements:
                connection.execute(text(statement))
            read_back = [_rows(connection.execute(text(body), binds)) for body, binds in self._live_schema]
        self._reject_schema_drift(read_back)
        log_debug(f"PgVectorMemorySubstrate schema ready on {self.schema}.{self.table}")

    async def ainit_schema(self) -> None:
        """Async twin of :meth:`init_schema`."""
        with self._translated("schema init"):
            async with self._async_engine().begin() as connection:
                for statement in self._schema_statements:
                    await connection.execute(text(statement))
                read_back = [_rows(await connection.execute(text(body), binds)) for body, binds in self._live_schema]
        self._reject_schema_drift(read_back)
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

    @staticmethod
    def _partition_ddl(value: Any) -> str:
        """The composed ``CREATE TABLE ... PARTITION OF`` text, checked before it is executed.

        Defence, and named as such: ``format()`` returns NULL only when its *format string* is NULL,
        and that argument is a literal in this module, so no test can reach this. What it buys is
        that a future edit making any bind nullable fails as a named refusal rather than as
        ``syntax error at or near "None"`` from a statement nobody wrote. One helper rather than the
        same two lines in each twin, so the two cannot come to disagree about it.
        """
        if not isinstance(value, str) or not value:
            raise MemoryOperationError(
                f"Composing the partition DDL returned {value!r} instead of a statement. This is a "
                "server-side format() over non-null identifiers, so it should be unreachable.",
                field_name="ensure_partition",
            )
        return value

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
                connection.execute(text(self._partition_ddl(ddl)))
        except MemoryOperationError:
            # Wider than `DuplicateTable`, deliberately: the recoverable case is defined by the
            # *state*, not by which error announced it. Another process committing the same
            # partition between this transaction's IF NOT EXISTS and its CREATE is the only way the
            # DDL can fail and the caller still get what it asked for, so the question asked is
            # "is it there now" rather than "did the message say duplicate" -- message matching
            # breaks on a locale, a server version, or a driver that rewords. Anything else
            # re-raises, and a failure that leaves no partition behind always does.
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
                    await connection.execute(text(self._partition_ddl(result.scalar())))
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
        """Close the sync engine, or -- with an async engine open -- **release nothing and refuse**.

        Closing the async half here is not possible: its connections belong to the loop that opened
        them, so a sync call can neither await them nor drive that loop. Dropping the reference is
        not closing either; it leaves the pool's sockets open and permanently disables
        :meth:`aclose`'s own guard, so the leak becomes unreachable rather than fixed.

        **The refusal is all-or-nothing, and the sync pool is deliberately left open with it.**
        Disposing that half on the way out would make a raising method leave the substrate
        half-dead: a caller that catches this -- and it is catchable, being the same error class as
        any other per-request refusal -- would hold an object whose sync half now fails on every
        call, for a teardown it did not get. Nothing is released, the message says so, and
        :meth:`aclose` is the one call that closes both. Pinned by
        ``test_close_refuses_without_disposing_either_half``.
        """
        if self._aengine is not None:
            raise MemoryOperationError(
                "This substrate has an open async engine, and close() cannot release it: its "
                "connections belong to the loop that opened them. **Neither pool has been closed** "
                "-- this call released nothing. Call aclose() from the same async context; it "
                "closes both.",
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

        **Zero similarity is dropped here rather than in the ``WHERE`` clause.** It is not a match:
        it would occupy a page slot, contribute nothing to the sum the provider fuses on, and put an
        unrelated memory in a prompt. As a SQL predicate it cost a second distance computation for
        every row the index visited (5.23 ms p50 against 3.15 ms); here it costs one comparison per
        *returned* row. The two produce the same set because the scan is ordered by that distance,
        so everything the cut removes already ranks below everything it keeps -- a page cannot come
        back short of a row this filter would have kept.
        """
        paired = sorted(
            (pair for pair in zip(rows, records) if _similar(pair[0])),
            key=lambda pair: (
                float(pair[0]["distance"]),
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
        embeddings = self.pipeline.embed_texts([record.content for record in checked])
        self.ensure_partition(scope.tenant_id)
        rows = [self._row(scope, record, embedding) for record, embedding in zip(checked, embeddings)]
        self._write(sql.upsert_statement(self.schema, self.table), rows)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        if not records:
            return
        checked = self._checked(scope, records)
        embeddings = await self.pipeline.aembed_texts([record.content for record in checked])
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
        embedding = self.pipeline.embed_query(query.text)
        rows = self._statements(self._search_call(scope, query, tiers, embedding))[-1]
        return self._ranked(rows, [self._record(row) for row in rows])

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        tiers = self._searchable(query, tier)
        if tiers is None:
            return []
        embedding = await self.pipeline.aembed_query(query.text)
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
