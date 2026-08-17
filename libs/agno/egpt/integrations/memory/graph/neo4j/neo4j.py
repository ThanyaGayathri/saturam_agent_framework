"""Neo4j-backed :class:`~egpt.memory.substrates.GraphSubstrate` (SM-G1 #99).

**The engine is SM-R2's call, not this module's.** It measured the Graphiti library at 19/30 on
SM-R10's own point-in-time probes -- 6.6 points above a store with no bi-temporal model at all --
and traced the gap to three structural write-path defects, so the library is rejected and the
engine underneath it is kept. Zep is hosted SaaS and fails the self-host axis; Cognee has no
transaction axis, leaving 8 of 30 probes inexpressible; ``lightrag`` drops filters silently, so a
point-in-time predicate cannot even be sent. What is reused is Neo4j's storage, Cypher and indexes.
**Every temporal semantic here is ours** -- SM-R10's four timestamps, expire-and-reinsert, and the
5a predicate, which measured 30/30 on this engine.

Tenant partitioning is a **property filter on every row**, which SM-R2 section 5.4 measured as the
only option Neo4j Community offers: ``CREATE DATABASE`` is denied, so database-per-tenant needs
Enterprise. That makes a forgotten predicate a cross-tenant leak with no engine-level second line
of defence, which is why no statement is written by hand here -- :mod:`.cypher` builds all of them
from one scope helper, and a unit test asserts the predicate over every statement the module can
emit.

Deliberately **not** here, each with an owner: entity and relation extraction (SM-G2 #100 /
SM-G3 #101), contradiction *detection* (SM-G5 #103 -- this ships the primitive, not the decision to
call it), valid-time extraction from content (SM-G4 #102), append-only enforcement via APOC trigger
or scheduled audit (SM-G4 #102, options in SM-R10 section 6.1), multi-hop traversal and graph
enumeration (SM-G9 #107), PPR over a GDS projection (SM-H4 #134), episode-to-entity mention links
(SM-G6 #104), and entity resolution (SM-G8 #106).
"""

import json
import os
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Tuple
from uuid import uuid4

from egpt.integrations.memory.graph.neo4j import cypher
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    EntityWrite,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    Scope,
    Substrate,
    TemporalBounds,
)
from egpt.utils.dttm import now_epoch_s
from egpt.utils.log import log_debug

if TYPE_CHECKING:
    from neo4j import AsyncDriver

try:
    from neo4j import AsyncGraphDatabase, GraphDatabase
    from neo4j.exceptions import DriverError, Neo4jError, ServiceUnavailable, SessionExpired, TransientError
except ImportError:
    raise ImportError("`neo4j` not installed. Please install using `pip install neo4j`")

__all__ = ["Neo4jGraphSubstrate"]


#: What ``_auth`` becomes once both drivers hold their own copy. A tuple rather than ``None`` so a
#: later read fails as an auth error rather than a ``TypeError`` three frames into the driver.
_SPENT_CREDENTIAL = ("", "")


def _as_json(value: Optional[Dict[str, Any]], field: str) -> str:
    """Serialize a nested value, in this module's error envelope rather than the stdlib's.

    A Neo4j property holds primitives and arrays only, so these two travel as JSON. Every other
    input failure here raises ``MemoryOperationError``; a bare ``TypeError`` from ``json`` would be
    the one that does not, and it names neither the field nor the record.
    """
    try:
        return json.dumps(value or {})
    except TypeError as exc:
        raise MemoryOperationError(
            f"{field} is not JSON-serializable ({exc}). A graph property holds primitives and "
            "arrays, so nested values are stored as JSON.",
            field_name=field,
        ) from exc


def _tiers(query: MemoryQuery) -> List[str]:
    """The tier set as the statement binds it.

    Applied in Cypher rather than after the fact, because each statement applies ``LIMIT``
    server-side: filtering afterwards lets a tight limit cut rows the tier filter would have kept,
    and the short page looks like a genuine miss.
    """
    return [tier.value for tier in query.resolved_tiers()]


def _terms(text: str) -> List[str]:
    """Distinct lowercase words, which is what the Jaccard score in :mod:`.cypher` expects.

    Deduplicated here rather than in Cypher because the score's denominator is a set union, and a
    repeated query term would inflate it and shrink every score.
    """
    return sorted({word for word in text.lower().split() if word})


class Neo4jGraphSubstrate:
    """A temporal knowledge graph over Neo4j, scoped per tenant on every statement.

    Sync and async are both native: the async half drives ``AsyncGraphDatabase`` rather than
    hopping a thread, because a thread hop is a sync call wearing an ``async def``.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        auto_init_schema: bool = True,
    ) -> None:
        resolved_uri: str = uri or os.getenv("NEO4J_URI") or "bolt://localhost:7687"
        username = username or os.getenv("NEO4J_USERNAME")
        password = password or os.getenv("NEO4J_PASSWORD")
        if not username or not password:
            # A configuration error, not a per-request refusal: `MemoryOperationError` is documented
            # as what a *correctly configured* provider raises for one call.
            raise MemoryProviderConfigError(
                "Neo4jGraphSubstrate needs a username and password, from arguments or NEO4J_USERNAME / NEO4J_PASSWORD.",
                field_name="password",
            )
        self.uri, self.database = resolved_uri, database or "neo4j"
        self._auth = (username, password)
        self._driver = GraphDatabase.driver(self.uri, auth=self._auth)
        self._adriver: Optional["AsyncDriver"] = None
        # Eager, unlike `PgVector`'s deferred `create_schema`. The statements are idempotent, so a
        # repeat costs latency and not correctness -- and the uniqueness constraints are what make
        # `MERGE` safe, so a backend that wrote before creating them would be unprotected for
        # exactly as long as it took to notice. `auto_init_schema=False` is the opt-out. Deferring
        # to first use becomes worth revisiting if an async construction path is ever added, since
        # these are blocking calls.
        if auto_init_schema:
            self.init_schema()

    # -- plumbing -------------------------------------------------------------

    def _async_driver(self) -> "AsyncDriver":
        """Built on first use, not in ``__init__``: an async driver binds to the running loop.

        Building it is also the last thing that needs the credential, so this is where it stops
        being an attribute: a substrate reaches logs and crash dumps through ``vars()`` and
        ``repr()`` far more often than anyone intends, and both drivers hold their own copy.
        """
        if self._adriver is None:
            self._adriver = AsyncGraphDatabase.driver(self.uri, auth=self._auth)
            self._forget_credential()
        return self._adriver

    def _forget_credential(self) -> None:
        """Drop the plaintext credential once nothing left to build needs it.

        One function rather than an assignment at each site, because the first version cleared it
        in ``_async_driver`` only -- so a substrate used purely through the sync API kept the
        password for its whole lifetime, which is most substrates. Called from there **and** from
        both close paths, so no route leaves it behind.
        """
        self._auth = _SPENT_CREDENTIAL

    @staticmethod
    @contextmanager
    def _translated(operation: str) -> Iterator[None]:
        """The one place a driver failure becomes this module's error, or deliberately does not.

        **Not a blanket wrap, because the distinction it would erase is load-bearing.** The provider
        reads ``MemoryOperationError`` as "this will never work" and prefers it as the cause of a
        failed fan-out (``co_located._raise_write_failure``); anything else it reads as "retry me".
        Translating a ``ServiceUnavailable`` into a refusal would tell a caller to give up on a
        network blip, and the same batch would report differently depending on which failure it hit.

        So the split follows retryability, not the exception's origin:

        * deterministic server-side errors -- bad Cypher, a constraint violation, a rejected auth --
          fail identically on every retry, and become ``MemoryOperationError``;
        * transient and transport errors propagate **unchanged**, so the layers above can still tell
          a broken query from an unreachable database.

        Applied at every driver interaction rather than the two the review named: a translation that
        covers four of six call sites is the same gap wearing a smaller number.
        """
        try:
            yield
        except (TransientError, ServiceUnavailable, SessionExpired):
            raise
        except (Neo4jError, DriverError) as exc:
            raise MemoryOperationError(f"The graph backend refused {operation}: {exc}", field_name=operation) from exc

    def _run(self, statements: Sequence[Tuple[str, Dict[str, Any]]], **params: Any) -> List[List[Dict[str, Any]]]:
        """Run statements in **one** transaction, returning each one's rows.

        One transaction rather than one per statement, so a multi-shape write cannot leave the
        expire half applied and the reinsert half not -- the state SM-R10 section 6 calls
        unreconstructable.
        """
        with self._translated("read/write"), self._driver.session(database=self.database) as session:
            with session.begin_transaction() as tx:
                results = [
                    [dict(record) for record in tx.run(text, **{**bound, **params})] for text, bound in statements
                ]
                tx.commit()
        return results

    # `_run` has no explicit rollback: `with session.begin_transaction()` rolls back on an
    # exception, which `_arun` cannot use because the async driver's transaction is not a context
    # manager. The two are equivalent, and are spelled differently only for that reason -- worth
    # stating so a reader does not "fix" the asymmetry on one side and create a real one.
    async def _arun(
        self, statements: Sequence[Tuple[str, Dict[str, Any]]], **params: Any
    ) -> List[List[Dict[str, Any]]]:
        """Async twin of :meth:`_run`, with the same one-transaction rule."""
        with self._translated("read/write"):
            async with self._async_driver().session(database=self.database) as session:
                tx = await session.begin_transaction()
                try:
                    results = []
                    for text, bound in statements:
                        result = await tx.run(text, **{**bound, **params})
                        results.append([dict(record) async for record in result])
                    await tx.commit()
                except BaseException:
                    await tx.rollback()
                    raise
        return results

    def init_schema(self) -> None:
        """Create the constraints and indexes, idempotently.

        Results are consumed here for the same reason :meth:`ainit_schema` gives, and the reason is
        no weaker on this half: leaving them to the session's drain-on-close borrows a guarantee
        from the driver's teardown. Adding it to the async twin alone left the sync path relying on
        exactly what its own docstring said not to rely on.
        """
        with self._translated("schema init"), self._driver.session(database=self.database) as session:
            for statement in cypher.SCHEMA_STATEMENTS:
                session.run(statement).consume()
        log_debug(f"Neo4jGraphSubstrate schema ready on {self.uri}/{self.database}")

    async def ainit_schema(self) -> None:
        """Async twin of :meth:`init_schema`.

        Each result is consumed rather than left to the session's drain-on-close. The schema does
        land either way -- verified against the engine -- but "the statement ran because closing the
        session happened to flush it" is a guarantee borrowed from the driver's teardown, and the
        first statement to need its result would have no way to ask for one.
        """
        with self._translated("schema init"):
            async with self._async_driver().session(database=self.database) as session:
                for statement in cypher.SCHEMA_STATEMENTS:
                    result = await session.run(statement)
                    await result.consume()
        log_debug(f"Neo4jGraphSubstrate schema ready on {self.uri}/{self.database}")

    def close(self) -> None:
        """Close the sync driver, and **refuse** when an async one is still open.

        Dropping the reference is not closing: it leaves the pool's sockets and background tasks
        open -- a live ``ResourceWarning: unclosed AsyncBoltDriver`` -- and permanently disables
        :meth:`aclose`'s own guard, so the leak becomes unreachable rather than fixed.

        Closing it here is not possible either, and the reason is structural rather than a missing
        ``await``: an async driver binds to the event loop that created it, so once that loop has
        finished ``asyncio.run`` on a fresh one raises ``Event loop is closed`` mid-teardown. That
        leaves refusing, which is what this module does everywhere else it cannot honour a call.
        """
        if self._adriver is not None:
            raise MemoryOperationError(
                "This substrate has an open async driver, and close() cannot release it: an async "
                "driver is bound to the loop that created it, so a sync call can neither await it "
                "nor drive that loop. Call aclose() from the same async context; it closes both.",
                field_name="close",
            )
        with self._translated("close"):
            self._driver.close()
        self._forget_credential()

    async def aclose(self) -> None:
        """Closes both halves: the sync driver is opened in ``__init__`` whichever half is used."""
        with self._translated("close"):
            if self._adriver is not None:
                await self._adriver.close()
                self._adriver = None
            self._driver.close()
        self._forget_credential()

    # -- row mapping ----------------------------------------------------------

    @staticmethod
    def _reject_foreign_scope(scope: Scope, row_scope: Scope) -> None:
        """A row is written under the scope it carries; a mismatch is refused, never reinterpreted.

        The mismatch has two defensible readings -- honour the call scope, or honour the record's --
        and a caller cannot tell which it got. Under the first, a fact derived from one user's
        episodes is filed tenant-wide and every user in the tenant can read it.
        """
        if row_scope != scope:
            # Slot names, never the values. This fires on a *foreign* scope by definition, so the
            # values are exactly the identifiers that must not reach a log line or an API response
            # -- the convention `co_located._assert_scope_isolation` already states and follows.
            differing = [
                slot for slot in cypher.SCOPE_SLOTS if getattr(row_scope, slot, None) != getattr(scope, slot, None)
            ]
            raise MemoryOperationError(
                f"Record scope does not match the write scope; {len(differing)} slot(s) differ: "
                f"{', '.join(differing)}. Batch per scope rather than leaving the choice to the backend.",
                field_name="scope",
            )

    def _row(self, scope: Scope, record_id: str, tier: MemoryTier, content: str, **fields: Any) -> Dict[str, Any]:
        """One stored row: scope columns, the bi-temporal envelope, and the surrogate key.

        ``row_id`` is unique and ``id`` is not: under SCD-2 one logical id has many revisions, so a
        uniqueness constraint on ``id`` would reject the bounded copy that invalidation inserts.
        Nested values are JSON, because a Neo4j property holds primitives and arrays only.
        """
        return {
            **cypher.scope_props(scope),
            # One of **four** writers of the live/`expired_at` pairing, counted rather than
            # guessed: here (a new row is live), `_pair_live_marker` (a row written back retired
            # is not), `cypher.expire_statements` (removes both together) and
            # `cypher.insert_bounded_copy_statements` (the successor is live again). The invariant
            # they must jointly maintain is asserted in
            # `test_the_live_marker_and_expired_at_never_disagree`, which is what makes four
            # writers safe. See `cypher.LIVE` for why the marker exists at all.
            cypher.LIVE: True,
            "row_id": str(uuid4()),
            "id": record_id,
            "tier": tier.value,
            "content": content,
            "topics": fields.get("topics"),
            "t_valid": fields.get("t_valid"),
            "t_invalid": fields.get("t_invalid"),
            "created_at": fields.get("created_at"),
            "expired_at": fields.get("expired_at"),
            "superseded_by": fields.get("superseded_by"),
            "provenance": _as_json(fields.get("provenance"), "provenance"),
            "metadata": _as_json(fields.get("metadata"), "metadata"),
        }

    @staticmethod
    def _pair_live_marker(row: Dict[str, Any]) -> Dict[str, Any]:
        """Keep ``live`` and ``expired_at`` consistent on a row about to be written.

        ``upsert_facts`` is how a fact is *retired*: the caller writes the row back with
        ``expired_at`` set. Such a row is not current belief, so it must not carry the marker --
        otherwise it stays visible to every read and holds the live slot the constraint protects.
        """
        if row.get("expired_at") is not None:
            row.pop(cypher.LIVE, None)
        return row

    @staticmethod
    def _from_json(value: Optional[str], field: str, record_id: Any) -> Dict[str, Any]:
        """Decode a stored nested value, in this module's envelope rather than the stdlib's.

        The mirror of :func:`_as_json`. A row whose JSON does not parse is out-of-band -- written by
        something other than this backend -- and the read path had the one unshaped failure left in
        the module after the write path was covered.
        """
        try:
            return dict(json.loads(value or "{}"))
        except (TypeError, ValueError) as exc:
            raise MemoryOperationError(
                f"{field} on record {record_id!r} is not readable JSON ({exc}). A graph property "
                "holds primitives and arrays, so this backend stores nested values as JSON; a row "
                "written outside it will not round-trip.",
                field_name=field,
            ) from exc

    def _record(self, row: Dict[str, Any], scope: Scope) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            tier=MemoryTier(row["tier"]),
            content=row["content"],
            scope=scope,
            score=row.get("score"),
            substrate=Substrate.GRAPH,
            topics=list(row["topics"]) if row.get("topics") is not None else None,
            provenance=self._from_json(row.get("provenance"), "provenance", row.get("id")),
            t_valid=row.get("t_valid"),
            t_invalid=row.get("t_invalid"),
            created_at=row.get("created_at"),
            expired_at=row.get("expired_at"),
            superseded_by=row.get("superseded_by"),
            metadata=self._from_json(row.get("metadata"), "metadata", row.get("id")),
        )

    # -- writes ---------------------------------------------------------------

    def _episode_rows(self, scope: Scope, events: Sequence[MemoryEvent]) -> List[Dict[str, Any]]:
        rows = []
        for event in events:
            self._reject_foreign_scope(scope, event.scope)
            if not event.id:
                raise MemoryOperationError(
                    "MemoryEvent.id is required here: it is the cross-substrate dedup key, and "
                    "upsert is by id. The provider stamps one before the call.",
                    field_name="id",
                )
            rows.append(
                self._row(
                    scope,
                    event.id,
                    event.tier,
                    event.content,
                    topics=event.topics,
                    t_valid=event.occurred_at,
                    created_at=now_epoch_s(),
                    metadata=event.metadata,
                )
            )
        return rows

    def upsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        if not events:
            return
        self._run([cypher.upsert_episodes_statement(scope)], rows=self._episode_rows(scope, events))

    async def aupsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        if not events:
            return
        await self._arun([cypher.upsert_episodes_statement(scope)], rows=self._episode_rows(scope, events))

    def _fact_batches(self, scope: Scope, facts: Sequence[FactWrite]) -> Dict[str, List[Dict[str, Any]]]:
        """Split facts by shape: a full triple is a relation, anything else is a node."""
        batches: Dict[str, List[Dict[str, Any]]] = {"triple": [], "node": []}
        for fact in facts:
            record = fact.record
            self._reject_foreign_scope(scope, record.scope)
            provenance = {**record.provenance}
            if fact.source_episode_ids:
                provenance.setdefault("source_episode_ids", list(fact.source_episode_ids))
            row = self._row(
                scope,
                record.id,
                record.tier,
                record.content,
                topics=record.topics,
                t_valid=record.t_valid,
                t_invalid=record.t_invalid,
                # `expired_at` and `superseded_by` are stored exactly as written: this method is how
                # a fact is retired, and a backend that dropped them would make retirement a silent
                # no-op with two current beliefs for one logical fact.
                created_at=record.created_at if record.created_at is not None else now_epoch_s(),
                expired_at=record.expired_at,
                superseded_by=record.superseded_by,
                provenance=provenance,
                metadata={**record.metadata, "confidence": fact.confidence, "derived_by": fact.derived_by},
            )
            self._pair_live_marker(row)
            if fact.is_triple():
                row["predicate"] = fact.predicate
                batches["triple"].append(
                    {
                        "scope_key": row["scope_key"],
                        "subject": fact.subject,
                        "object": fact.object,
                        "entity": cypher.scope_props(scope),
                        "props": row,
                        "id": row["id"],
                    }
                )
            else:
                batches["node"].append(row)
        return batches

    def _fact_statements(self, scope: Scope, facts: Sequence[FactWrite]) -> List[Tuple[str, Dict[str, Any]]]:
        batches = self._fact_batches(scope, facts)
        shapes = cypher.upsert_facts_statements(scope)
        return [(text, {**bound, "rows": batches[shape]}) for shape, (text, bound) in shapes.items() if batches[shape]]

    def upsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        if not facts:
            return
        self._run(self._fact_statements(scope, facts))

    async def aupsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        if not facts:
            return
        await self._arun(self._fact_statements(scope, facts))

    def _entity_rows(self, scope: Scope, entities: Sequence[EntityWrite]) -> List[Dict[str, Any]]:
        """One row per entity node (SM-G2 #100), split into merge keys, unioned lists, and ``props``.

        The split is what the statement needs: ``name`` and ``scope_key`` address the node,
        ``created_at`` is set only on creation, the two lists are unioned into whatever the node
        already holds, and everything in ``props`` is last-write.

        Deliberately **not** :meth:`_row`. That builds a bi-temporal, tiered, SCD-2 row with a
        surrogate key and a live marker, and an entity node is none of those (ADR 0003 section 1) --
        reusing it would put four timestamps and a ``tier`` on a node whose validity is not a claim
        anything can contradict.
        """
        now = now_epoch_s()
        # Invariant across the loop -- `scope` is the call scope, not the entity's -- so computed
        # once rather than once per entity. Read-only below (`**scope_props` spreads a copy), so
        # sharing the one dict across rows is safe.
        scope_props = cypher.scope_props(scope)
        # `scope_key` alone excluded: unlike `_row`'s `SET n = row` (a full overwrite, where every
        # property including `scope_key` must be present), this shape's `SET n += row.props` is
        # additive on top of what `MERGE (n:{ENTITY} {scope_key: row.scope_key, name: row.name})`
        # already bound -- so repeating it in `props` writes the same value MERGE already set.
        # Review finding on PR #89, round 2. The individual slots (`tenant_id`, etc.) are not
        # redundant the same way: MERGE binds only `scope_key`, not them.
        entity_props = {k: v for k, v in scope_props.items() if k != "scope_key"}
        rows = []
        for entity in entities:
            self._reject_foreign_scope(scope, entity.scope)
            rows.append(
                {
                    "scope_key": scope_props["scope_key"],
                    "name": entity.name,
                    "created_at": now,
                    "source_episode_ids": list(entity.source_episode_ids),
                    # The entity's own surface form is never its own alias: it is already the merge
                    # key, and carrying it in both places makes a candidate look like it was seen
                    # under two spellings when it was seen under one.
                    "aliases": [alias for alias in entity.aliases if alias != entity.name],
                    "props": {
                        **entity_props,
                        "key": entity.key,
                        "entity_type": entity.entity_type,
                        "description": entity.description,
                        "properties": _as_json(entity.properties, "properties"),
                        "embedding": entity.embedding,
                        "embedding_version": entity.embedding_version,
                        "confidence": entity.confidence,
                        "derived_by": entity.derived_by,
                        "updated_at": now,
                    },
                }
            )
        return rows

    def upsert_entities(self, scope: Scope, entities: Sequence[EntityWrite]) -> None:
        if not entities:
            return
        self._run([cypher.upsert_entities_statement(scope)], rows=self._entity_rows(scope, entities))

    async def aupsert_entities(self, scope: Scope, entities: Sequence[EntityWrite]) -> None:
        if not entities:
            return
        await self._arun([cypher.upsert_entities_statement(scope)], rows=self._entity_rows(scope, entities))

    # -- reads ----------------------------------------------------------------

    def _merge_hits(self, rows: List[List[Dict[str, Any]]], scope: Scope, query: MemoryQuery) -> List[MemoryRecord]:
        """Merge the per-shape legs, re-rank, and apply ``limit`` to the merged set.

        Each statement applied ``limit`` to its own shape, so the union can hold up to three times
        it; ranking across shapes and cutting again is what makes ``limit`` mean what it says.
        """
        hits = [self._record(row, scope) for leg in rows for row in leg]
        hits.sort(key=lambda r: (-(r.score or 0.0), r.created_at or 0, r.content, r.id))
        return hits[: query.limit]

    @staticmethod
    def _requested(query: MemoryQuery, tier: Optional[MemoryTier]) -> MemoryQuery:
        """Narrow to a single tier when asked; otherwise the query's own set stands.

        The tier set is then resolved through ``MemoryQuery.resolved_tiers`` rather than re-derived
        here, because when one leg honoured the tier filter and the other did not, the leakage
        surfaced only after the merge and was hard to trace back.
        """
        return query if tier is None else replace(query, tiers=[tier])

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        query = self._requested(query, tier)
        if not query.limit or not _terms(query.text):
            return []
        rows = self._run(
            cypher.traverse_statements(scope), terms=_terms(query.text), limit=query.limit, tiers=_tiers(query)
        )
        return self._merge_hits(rows, scope, query)

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        query = self._requested(query, tier)
        if not query.limit or not _terms(query.text):
            return []
        rows = await self._arun(
            cypher.traverse_statements(scope), terms=_terms(query.text), limit=query.limit, tiers=_tiers(query)
        )
        return self._merge_hits(rows, scope, query)

    @staticmethod
    def _fold_bounds(rows: List[List[Dict[str, Any]]]) -> Dict[str, TemporalBounds]:
        """The **tightest** bound across every revision of an id, live or expired.

        Reporting only the live revision would miss the bound entirely in the case this method
        exists for: under SCD-2 the successor carrying ``t_invalid`` holds the same content as the
        row it replaced, so it scores identically and falls off the ranked page.
        """
        found: Dict[str, TemporalBounds] = {}
        for row in (row for leg in rows for row in leg):
            seen = found.get(row["id"])
            if seen is None:
                found[row["id"]] = TemporalBounds(
                    row["t_valid"], row["t_invalid"], row["expired_at"], row["superseded_by"]
                )
                continue
            # `filter(None, ...)` would drop 0 as well as None -- epoch 0 is a legitimate bound, and
            # dropping the tighter one leaves a false fact current.
            candidates = [b for b in (seen.t_invalid, row["t_invalid"]) if b is not None]
            # `expired_at=None` wins: the live revision is the one that decides current belief.
            expired = None if (seen.expired_at is None or row["expired_at"] is None) else seen.expired_at
            found[row["id"]] = TemporalBounds(
                seen.t_valid,
                min(candidates) if candidates else None,
                expired,
                seen.superseded_by or row["superseded_by"],
            )
        return found

    def bounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]:
        if not ids:
            return {}
        return self._fold_bounds(self._run(cypher.bounds_statements(scope), ids=list(ids)))

    async def abounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]:
        if not ids:
            return {}
        return self._fold_bounds(await self._arun(cypher.bounds_statements(scope), ids=list(ids)))

    # -- invalidation ---------------------------------------------------------

    @staticmethod
    def _bound(rows: List[List[Dict[str, Any]]], edge_id: str, t_invalid: int) -> Optional[Dict[str, Any]]:
        """Check the incoming bound against the live row, and keep the tighter of the two.

        A ``t_invalid`` at or before the row's ``t_valid`` is refused: valid time is half-open, so
        such a bound would leave the fact valid at no instant -- written, receipted, and then
        invisible forever with no error anywhere.
        """
        live = next((row for leg in rows for row in leg), None)
        if live is None:
            return None
        if live["t_valid"] is not None and t_invalid <= live["t_valid"]:
            raise ValueError(
                f"t_invalid ({t_invalid}) is not after t_valid ({live['t_valid']}) for {edge_id!r}, so "
                "the row would be valid at no instant. Valid time is half-open: t_valid <= at < t_invalid."
            )
        tightest = t_invalid if live["t_invalid"] is None else min(live["t_invalid"], t_invalid)
        return {"t_invalid": tightest, "live_row_id": live["row_id"]}

    @staticmethod
    def _reject_episode(edge_id: str, probe: List[Dict[str, Any]]) -> None:
        """Refuse an ``invalidate`` aimed at an episode, naming the lifecycle that owns it.

        ADR 0003 section B.6 splits the two: semantic contradiction on a graph **edge** goes through
        this SCD-2 primitive, while an episodic **row** is retired by SM-R6 re-segmentation or SM-R5
        decay archival writing ``expired_at`` on the record. Silently obliging would put episode
        archival on the edge path the ADR separates it from.
        """
        if probe:
            raise MemoryOperationError(
                f"{edge_id!r} is an episode, and invalidate() governs semantic contradiction on graph "
                "edges only (ADR 0003 section B.6). An episodic row is retired through its own "
                "lifecycle -- SM-R6 re-segmentation or SM-R5 decay archival -- by writing expired_at "
                "on the record, not through this primitive.",
                field_name="edge_id",
            )

    def _invalidation_params(
        self, rows: List[List[Dict[str, Any]]], edge_id: str, t_invalid: int, at: int
    ) -> Optional[Dict[str, Any]]:
        """The full parameter set for the write half, or ``None`` when there is nothing to bound."""
        bound = self._bound(rows, edge_id, t_invalid)
        if bound is None:
            # Not an error: the id may belong to another scope, or never have existed. Logged rather
            # than raised because a caller cannot tell those apart and neither can this method --
            # asking would mean reading outside the call scope, which is the one thing it must not do.
            log_debug(f"invalidate({edge_id!r}) found no live row in scope {self.database!r}; nothing to bound")
            return None
        return {"edge_id": edge_id, "at": at, "row_id": str(uuid4()), **bound}

    def invalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """Expire-and-reinsert, in **one** transaction: the read the bound is computed from and the
        write that applies it cannot be separated by another writer.

        A second writer that still slips through -- Neo4j reads do not lock -- loses on the
        ``(scope_key, id, live)`` uniqueness constraint rather than producing a second current
        belief. It fails loudly; it does not corrupt.

        Scoped to fact shapes: an episode id is refused, not obliged (see :meth:`_reject_episode`).
        An id in another scope is a no-op, logged at debug.
        """
        recorded_at = now_epoch_s() if at is None else at
        with self._translated("invalidate"), self._driver.session(database=self.database) as session:
            with session.begin_transaction() as tx:
                probe_text, probe_binds = cypher.episode_probe_statement(scope)
                self._reject_episode(edge_id, [dict(r) for r in tx.run(probe_text, **probe_binds, edge_id=edge_id)])
                rows = [
                    [dict(record) for record in tx.run(text, **binds, edge_id=edge_id)]
                    for text, binds in cypher.live_row_statements(scope)
                ]
                params = self._invalidation_params(rows, edge_id, t_invalid, recorded_at)
                if params is None:
                    # Explicit, matching `ainvalidate`. A clean exit from the context manager
                    # *commits*, so the two halves would diverge the moment a write appeared above
                    # this check -- the sync one keeping it, the async one discarding it. Nothing is
                    # written here today, which is exactly why it is free to fix now.
                    #
                    # `__exit__` still runs after this `return` and checks the transaction's state
                    # before committing, so the rollback stands. That is driver behaviour rather
                    # than something this code can assert inline: it is pinned by
                    # `test_the_no_op_invalidation_branch_rolls_back_on_both_halves`, which asserts
                    # on the transaction calls, and exercised end-to-end by
                    # `test_an_invalidation_under_a_foreign_scope_does_nothing`.
                    tx.rollback()
                    return
                for text, binds in (*cypher.expire_statements(scope), *cypher.insert_bounded_copy_statements(scope)):
                    tx.run(text, **{**binds, **params})
                tx.commit()

    async def ainvalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """Async twin of :meth:`invalidate`, with the same one-transaction rule."""
        recorded_at = now_epoch_s() if at is None else at
        with self._translated("invalidate"):
            async with self._async_driver().session(database=self.database) as session:
                tx = await session.begin_transaction()
                try:
                    probe_text, probe_binds = cypher.episode_probe_statement(scope)
                    probe = await tx.run(probe_text, **probe_binds, edge_id=edge_id)
                    self._reject_episode(edge_id, [dict(record) async for record in probe])
                    rows = []
                    for text, binds in cypher.live_row_statements(scope):
                        result = await tx.run(text, **binds, edge_id=edge_id)
                        rows.append([dict(record) async for record in result])
                    params = self._invalidation_params(rows, edge_id, t_invalid, recorded_at)
                    if params is None:
                        await tx.rollback()
                        return
                    for text, binds in (
                        *cypher.expire_statements(scope),
                        *cypher.insert_bounded_copy_statements(scope),
                    ):
                        await tx.run(text, **{**binds, **params})
                    await tx.commit()
                except BaseException:
                    await tx.rollback()
                    raise
