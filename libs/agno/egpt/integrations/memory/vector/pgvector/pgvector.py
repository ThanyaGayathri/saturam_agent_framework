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
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from egpt.integrations.memory.vector.pgvector import sql
from egpt.knowledge.embedder import Embedder
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    RetrievalCandidates,
    Scope,
    Substrate,
    require_scope,
)
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

#: The Postgres text-search configuration the lexical leg indexes and queries with (SM-V3 #97).
#: ``english`` rather than ``simple`` because stemming is what makes a lexical leg answer a
#: paraphrase at all, and the exact-identifier case the ticket is written around survives it: the
#: default parser emits a hyphenated token *and* its parts, so ``ORD-99417-XZ`` is findable whole
#: and by fragment. Per-deployment, because the right answer is the corpus' language.
_DEFAULT_TEXT_SEARCH_CONFIG = "english"

#: Saturation constant turning ``ts_rank_cd``'s unbounded output into the ``[0.0, 1.0]`` similarity
#: the port requires, as ``r / (r + k)``.
#:
#: **Strictly increasing in ``r``, which is the load-bearing property** -- it cannot reorder the
#: list it is applied to, so the ranks a rank-based fusion reads and the magnitudes a weighted one
#: reads describe the same ordering. ``0.1`` matches the existing convention in
#: ``PgVector.hybrid_search`` and puts a typical short-document ``ts_rank_cd`` near the middle of
#: the range. It is **not** a fusion weight: what a fused ranking does with these magnitudes,
#: including whether they need recalibrating away from a real BM25 leg's distribution, is
#: SM-H2's (#132).
_RANK_SATURATION = 0.1

#: Isolation level for a read whose several statements must describe **one** corpus (SM-V3 #97).
#:
#: Postgres defaults to READ COMMITTED, under which each *statement* takes a fresh snapshot even
#: inside one transaction -- so a row committed between the vector leg and the lexical leg is
#: invisible to the first and visible to the second. Measured, driving ``search_candidates`` with a
#: concurrent commit landing between them: one leg returned one row, the other two. The port
#: requires every leg to answer the same question about which rows exist, because legs that disagree
#: do not produce a ranking discrepancy, they produce a leak.
#:
#: Free here, and that is worth stating because REPEATABLE READ usually is not: its serialization
#: failures are raised on **write** conflicts, and nothing that reaches this level writes. A
#: read-only snapshot simply holds, so the cost is the snapshot's lifetime -- microseconds, for two
#: indexed statements.
_ONE_SNAPSHOT = "REPEATABLE READ"

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


#: The text-search configuration inside a stored ``to_tsvector(...)`` index expression.
#:
#: Postgres renders the literal in its own normalized form -- ``to_tsvector('english'::regconfig,
#: content)`` for the ``to_tsvector('english', content)`` this module submitted -- so the config is
#: parsed out and compared by **name**. Comparing the rendered text instead warned on every clean
#: startup, which is worse than not checking: an always-on warning is one nobody reads when it
#: finally means something.
_INDEXED_TS_CONFIG = re.compile(r"to_tsvector\(\s*'([^']*)'")


def _indexed_ts_config(expression: Optional[str]) -> Optional[str]:
    """The configuration a GIN index's expression uses, or ``None`` when it is not a text index.

    ``None`` for anything that is not a ``to_tsvector`` expression, because a deployment is free to
    have other GIN indexes on this table -- one on a ``jsonb`` column, say -- and warning that a
    ``metadata`` index does not use the configured text-search config would be noise about
    something working exactly as intended.
    """
    if not expression:
        return None
    found = _INDEXED_TS_CONFIG.search(expression)
    return found.group(1) if found else None


def _lexical_similarity(rank: float) -> float:
    """``ts_rank_cd``'s unbounded output as the ``[0.0, 1.0]`` similarity the port requires.

    ``r / (r + k)`` is **strictly increasing**, so it cannot reorder the list it is applied to --
    see :data:`_RANK_SATURATION`. A negative rank would break that (it maps below zero and, at
    ``r = -k``, divides by zero), and ``ts_rank_cd`` never returns one, so the clamp costs a
    comparison and removes the question.
    """
    bounded = max(0.0, rank)
    return bounded / (bounded + _RANK_SATURATION)


def _score(row: Dict[str, Any]) -> Optional[float]:
    """The port's similarity for whichever leg's raw measure this row carries.

    Exactly one of the two is ever selected -- ``distance`` by the vector statement, ``rank`` by the
    lexical one -- so this is a dispatch, not a merge. ``scan`` selects ``distance`` as ``NULL`` and
    lands on ``None``, which is the honest answer for an unranked read rather than the ``0.0`` that
    would read as a perfect match.
    """
    rank = row.get("rank")
    if rank is not None:
        return _lexical_similarity(float(rank))
    distance = row.get("distance")
    return None if distance is None else normalize_score(float(distance), Distance.cosine)


def _tiebreak(record: MemoryRecord) -> Tuple[int, str, str]:
    """How **either** leg orders records its primary measure scores equally.

    One function rather than the same tuple in each ranker, because two legs that break ties
    differently hand the fusion stage two orderings for one corpus -- and a rank-based fusion reads
    position, so the disagreement becomes a scoring difference rather than a cosmetic one.

    Never on a UUID: record ids are minted with ``uuid4``, so ordering on one makes two
    equally-matching memories rank differently per process. ``id`` is last only as the final
    total-order guarantee, after two content-derived keys have almost always decided it.
    """
    return (record.created_at or 0, record.content, record.id)


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
        text_search_config: str = _DEFAULT_TEXT_SEARCH_CONFIG,
        auto_init_schema: bool = True,
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
        self.embedder = self._resolved_embedder(embedder, embedder_options)
        self.ef_search = ef_search
        self.embedding_version = embedding_version
        # Every setting is checked here rather than at first use, and all of them inside one
        # wrapper: `ef_search` reaches a statement as a literal and the rest reach DDL, so this is
        # where a bad value is cheap -- and a refusal that arrives as a bare `ValueError` is one a
        # caller narrowing to `MemoryProviderConfigError` never sees.
        self._index_options = (m, ef_construction)
        with self._settings_checked():
            self.schema = sql.identifier(schema, "schema")
            self.table = sql.table_identifier(table)
            self.dim = self._resolved_dim(dim, self.embedder)
            self.text_search_config = sql.text_search_config(text_search_config)
            sql.ef_search_statement(ef_search)
            self._schema_statements = sql.schema_statements(
                self.schema, self.table, self.dim, m, ef_construction, self.text_search_config
            )
            self._live_schema = sql.live_schema_statements(self.schema, self.table)
            self._ts_config_probe = sql.text_search_config_statement(self.text_search_config)
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

    def _statements(
        self, statements: Sequence[Tuple[str, Dict[str, Any]]], isolation: Optional[str] = None
    ) -> List[List[Dict[str, Any]]]:
        """Run statements in **one** transaction, returning each one's rows.

        One transaction rather than one per statement, so the ``SET LOCAL hnsw.ef_search`` that
        precedes a search is still in force when the search runs, and cannot leak into another.

        ``isolation`` pins the snapshot when more than one statement here has to describe the *same*
        corpus -- see :data:`_ONE_SNAPSHOT`. It is a parameter rather than a blanket setting because
        only the multi-leg read needs it: a single row-returning statement is atomic against its own
        snapshot whatever the level, so raising it there would buy nothing and hold a snapshot open
        for longer than the read needs.
        """
        with self._translated("read/write"), self._read_transaction(isolation) as connection:
            return [_rows(connection.execute(text(body), binds)) for body, binds in statements]

    @contextmanager
    def _read_transaction(self, isolation: Optional[str]) -> Iterator[Any]:
        """One transaction, at the default isolation or an explicit one."""
        if isolation is None:
            with self._engine.begin() as connection:
                yield connection
            return
        with self._engine.connect().execution_options(isolation_level=isolation) as connection:
            with connection.begin():
                yield connection

    async def _astatements(
        self, statements: Sequence[Tuple[str, Dict[str, Any]]], isolation: Optional[str] = None
    ) -> List[List[Dict[str, Any]]]:
        """Async twin of :meth:`_statements`, with the same one-transaction and snapshot rules."""
        with self._translated("read/write"):
            if isolation is None:
                async with self._async_engine().begin() as connection:
                    return [_rows(await connection.execute(text(body), binds)) for body, binds in statements]
            async with self._async_engine().connect() as connection:
                await connection.execution_options(isolation_level=isolation)
                async with connection.begin():
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
        # Reported, not refused, for the reason the docstring gives: a stale text-search config
        # still answers correctly, because `to_tsvector` is evaluated at query time either way. What
        # it stops doing is *matching the index expression*, so the GIN index is skipped and every
        # lexical read becomes a sequential scan -- a latency cliff with no error anywhere, which is
        # exactly the kind of silence a readback exists to break.
        for row in read_back[2]:
            indexed = _indexed_ts_config(row["expression"])
            if indexed is not None and indexed != self.text_search_config:
                log_warning(
                    f"{row['name']} indexes text_search_config={indexed!r} and this substrate is "
                    f"configured for {self.text_search_config!r}. Lexical reads stay correct but "
                    "fall back to a sequential scan, because a functional index is only used when "
                    "the query repeats its expression exactly. Rebuilding it is a locking "
                    "operation this backend does not perform."
                )

    def _reject_unknown_text_search_config(self, probe: List[Dict[str, Any]]) -> None:
        """Refuse a text-search configuration Postgres does not resolve, **before** the DDL uses it.

        Asked as its own question rather than left to ``CREATE INDEX`` because the two failures are
        different kinds. This one is a deployment **setting** fault and says so, naming the option;
        the index creation it would otherwise break reports an ``undefined_object`` from the middle
        of schema init, mentioning a text-search configuration but not which argument supplied it --
        and arriving as a ``MemoryOperationError``, which a caller narrowing to
        ``MemoryProviderConfigError`` for ``field_name`` never sees.
        """
        if not probe or not probe[0].get("present"):
            raise MemoryProviderConfigError(
                f"text_search_config={self.text_search_config!r} is not a text-search configuration "
                "this server resolves. 'english' and 'simple' ship with Postgres; a custom one must "
                "exist and be reachable on the connection's search_path before it can be indexed.",
                field_name="text_search_config",
            )

    def init_schema(self) -> None:
        """Create the extension, the partitioned parent and its indexes, idempotently."""
        probe_body, probe_binds = self._ts_config_probe
        with self._translated("schema init"), self._engine.begin() as connection:
            self._reject_unknown_text_search_config(_rows(connection.execute(text(probe_body), probe_binds)))
            for statement in self._schema_statements:
                connection.execute(text(statement))
            read_back = [_rows(connection.execute(text(body), binds)) for body, binds in self._live_schema]
        self._reject_schema_drift(read_back)
        log_debug(f"PgVectorMemorySubstrate schema ready on {self.schema}.{self.table}")

    async def ainit_schema(self) -> None:
        """Async twin of :meth:`init_schema`."""
        probe_body, probe_binds = self._ts_config_probe
        with self._translated("schema init"):
            async with self._async_engine().begin() as connection:
                self._reject_unknown_text_search_config(_rows(await connection.execute(text(probe_body), probe_binds)))
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
            # `_score` dispatches on which leg produced the row; `scan` does not rank, and reports
            # no score rather than a fabricated one.
            score=_score(row),
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
            key=lambda pair: (float(pair[0]["distance"]), *_tiebreak(pair[1])),
        )
        return [record for _, record in paired]

    @staticmethod
    def _lexically_ranked(rows: List[Dict[str, Any]], records: List[MemoryRecord]) -> List[MemoryRecord]:
        """Order the lexical page deterministically, highest rank first (SM-V3 #97).

        The server already ordered by ``ts_rank_cd`` and applied the ``LIMIT``; this settles ties
        *inside* the returned page through the same :func:`_tiebreak` the vector leg uses, so the
        two lists a fusion stage receives cannot disagree about the order of two records neither
        leg distinguishes.

        **There is no zero-rank cut here, unlike** :meth:`_ranked`. The lexical statement's ``@@``
        predicate has already removed every non-match -- it has to, because that predicate is what
        makes the GIN index usable -- so the rows that arrive are matches by construction and a
        second filter would only be able to remove ones the server said matched.
        """
        paired = sorted(zip(rows, records), key=lambda pair: (-float(pair[0]["rank"]), *_tiebreak(pair[1])))
        return [record for _, record in paired]

    # -- writes ---------------------------------------------------------------

    def _checked(self, scope: Scope, records: Sequence[MemoryRecord]) -> List[MemoryRecord]:
        """Refuse the batch before embedding any of it -- a rejected record must cost no API call."""
        checked = list(records)
        for record in checked:
            self._reject_foreign_scope(scope, record)
        return checked

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        if not records:
            return
        checked = self._checked(scope, records)
        embeddings = [self.embedder.get_embedding(record.content) for record in checked]
        self.ensure_partition(scope.tenant_id)
        rows = [self._row(scope, record, embedding) for record, embedding in zip(checked, embeddings)]
        self._write(sql.upsert_statement(self.schema, self.table), rows)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
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
        require_scope(scope, method="search")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return []
        embedding = self.embedder.get_embedding(query.text)
        rows = self._statements(self._search_call(scope, query, tiers, embedding))[-1]
        return self._ranked(rows, [self._record(row) for row in rows])

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return []
        embedding = await self.embedder.async_get_embedding(query.text)
        rows = (await self._astatements(self._search_call(scope, query, tiers, embedding)))[-1]
        return self._ranked(rows, [self._record(row) for row in rows])

    # -- hybrid candidates (SM-V3 #97) ----------------------------------------

    def _lexical_call(self, scope: Scope, query: MemoryQuery, tiers: List[str]) -> Tuple[str, Dict[str, Any]]:
        """The lexical statement and its binds. No ``SET LOCAL`` precedes it -- ``hnsw.ef_search``
        tunes the vector index and means nothing to GIN."""
        statement, binds = sql.lexical_search_statement(self.schema, self.table, scope, self.text_search_config)
        binds.update({"tiers": tiers, "limit": query.limit, "query_text": query.text})
        return statement, binds

    def _lexical(self, rows: List[Dict[str, Any]]) -> List[MemoryRecord]:
        return self._lexically_ranked(rows, [self._record(row) for row in rows])

    def _candidate_calls(
        self, scope: Scope, query: MemoryQuery, tiers: List[str], embedding: Sequence[float]
    ) -> List[Tuple[Optional[str], Tuple[str, Dict[str, Any]]]]:
        """Every statement one candidate read issues, each **tagged with the leg it answers for**.

        Tagged rather than indexed. Reading the vector page as ``results[-2]`` counted back from the
        end, which is correct only while :meth:`_search_call` emits exactly two statements. Its first
        is ``SET LOCAL hnsw.ef_search``, which returns no rows and which ``_rows`` maps to ``[]`` --
        so a third statement added there would not mis-*rank* the vector leg, it would make it come
        back **empty**, with nothing raising and no way for a caller to tell that from a corpus with
        no matches.

        The unpacking below is the guard that makes the failure loud: ``_search_call`` growing a
        statement raises here, at the line that has to change, instead of silently emptying a leg.
        """
        tuning, vector = self._search_call(scope, query, tiers, embedding)
        return [(None, tuning), ("vector", vector), ("lexical", self._lexical_call(scope, query, tiers))]

    def _candidates(
        self,
        calls: Sequence[Tuple[Optional[str], Tuple[str, Dict[str, Any]]]],
        results: Sequence[List[Dict[str, Any]]],
    ) -> RetrievalCandidates:
        """Assemble the two lists from labelled results, so neither half indexes by position.

        Shared by both halves rather than written twice: the sync and async candidate reads drifting
        is precisely the defect this ticket's own tests exist to catch.
        """
        pages = {leg: page for (leg, _), page in zip(calls, results) if leg is not None}
        return RetrievalCandidates(
            vector=self._ranked(pages["vector"], [self._record(row) for row in pages["vector"]]),
            lexical=self._lexical(pages["lexical"]),
        )

    def search_candidates(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> RetrievalCandidates:
        """Both ranked legs, unfused -- :class:`~egpt.memory.substrates.HybridSearchSubstrate`.

        The legs are issued as **separate statements in one transaction**, not as a single query
        with two ranking expressions. A combined statement would have to pick one ``ORDER BY``, and
        whichever it picked would make the other leg's ``LIMIT`` mean "the top N by the wrong
        measure" -- which is how a hybrid query silently becomes a filtered version of one leg.

        **One transaction is not enough to make the legs agree, so the snapshot is pinned.** Postgres
        defaults to READ COMMITTED, under which every *statement* takes a fresh snapshot even inside
        one transaction -- so a row committed between the two legs is invisible to the first and
        visible to the second. Measured, driving this method with a concurrent commit landing between
        them: the vector leg returned one row and the lexical leg two, from a single call. The port
        requires every leg to answer the same question about which rows exist, because legs that
        disagree do not produce a ranking discrepancy, they produce a leak.

        ``query.limit`` bounds each leg independently; over-fetching to give fusion more depth is
        SM-H2's (#132) policy, not this store's.
        """
        require_scope(scope, method="search_candidates")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return RetrievalCandidates()
        embedding = self.embedder.get_embedding(query.text)
        calls = self._candidate_calls(scope, query, tiers, embedding)
        results = self._statements([call for _, call in calls], isolation=_ONE_SNAPSHOT)
        return self._candidates(calls, results)

    async def asearch_candidates(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> RetrievalCandidates:
        """Async twin of :meth:`search_candidates`, making the same decisions in the same order --
        including the pinned snapshot, which is the half a sync-only test cannot observe."""
        require_scope(scope, method="asearch_candidates")
        tiers = self._searchable(query, tier)
        if tiers is None:
            return RetrievalCandidates()
        embedding = await self.embedder.async_get_embedding(query.text)
        calls = self._candidate_calls(scope, query, tiers, embedding)
        results = await self._astatements([call for _, call in calls], isolation=_ONE_SNAPSHOT)
        return self._candidates(calls, results)

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
        require_scope(scope, method="scan")
        call = self._scan_call(self.schema, self.table, scope, tier, limit)
        if call is None:
            return []
        return [self._record(row) for row in self._statements([call])[0]]

    async def ascan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        call = self._scan_call(self.schema, self.table, scope, tier, limit)
        if call is None:
            return []
        return [self._record(row) for row in (await self._astatements([call]))[0]]
