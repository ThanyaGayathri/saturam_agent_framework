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
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from egpt.integrations.memory.graph.neo4j import cypher
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
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

try:
    from neo4j import AsyncGraphDatabase, GraphDatabase
except ImportError:
    raise ImportError("`neo4j` not installed. Please install using `pip install neo4j`")

__all__ = ["Neo4jGraphSubstrate"]


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
            raise MemoryOperationError(
                "Neo4jGraphSubstrate needs a username and password, from arguments or NEO4J_USERNAME / NEO4J_PASSWORD.",
                field_name="password",
            )
        self.uri, self.database = resolved_uri, database or "neo4j"
        self._auth = (username, password)
        self._driver = GraphDatabase.driver(self.uri, auth=self._auth)
        self._adriver: Optional[Any] = None
        if auto_init_schema:
            self.init_schema()

    # -- plumbing -------------------------------------------------------------

    def _async_driver(self) -> Any:
        """Built on first use, not in ``__init__``: an async driver binds to the running loop."""
        if self._adriver is None:
            self._adriver = AsyncGraphDatabase.driver(self.uri, auth=self._auth)
        return self._adriver

    def _run(self, statements: Sequence[Tuple[str, Dict[str, Any]]], **params: Any) -> List[List[Dict[str, Any]]]:
        """Run statements in **one** transaction, returning each one's rows.

        One transaction rather than one per statement, so a multi-shape write cannot leave the
        expire half applied and the reinsert half not -- the state SM-R10 section 6 calls
        unreconstructable.
        """
        with self._driver.session(database=self.database) as session:
            with session.begin_transaction() as tx:
                results = [
                    [dict(record) for record in tx.run(text, **{**bound, **params})] for text, bound in statements
                ]
                tx.commit()
        return results

    async def _arun(
        self, statements: Sequence[Tuple[str, Dict[str, Any]]], **params: Any
    ) -> List[List[Dict[str, Any]]]:
        """Async twin of :meth:`_run`, with the same one-transaction rule."""
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
        """Create the constraints and indexes, idempotently."""
        with self._driver.session(database=self.database) as session:
            for statement in cypher.SCHEMA_STATEMENTS:
                session.run(statement)
        log_debug(f"Neo4jGraphSubstrate schema ready on {self.uri}/{self.database}")

    async def ainit_schema(self) -> None:
        """Async twin of :meth:`init_schema`."""
        async with self._async_driver().session(database=self.database) as session:
            for statement in cypher.SCHEMA_STATEMENTS:
                await session.run(statement)

    def close(self) -> None:
        self._driver.close()

    async def aclose(self) -> None:
        """Closes both halves: the sync driver is opened in ``__init__`` whichever half is used."""
        if self._adriver is not None:
            await self._adriver.close()
            self._adriver = None
        self._driver.close()

    # -- row mapping ----------------------------------------------------------

    @staticmethod
    def _reject_foreign_scope(scope: Scope, row_scope: Scope) -> None:
        """A row is written under the scope it carries; a mismatch is refused, never reinterpreted.

        The mismatch has two defensible readings -- honour the call scope, or honour the record's --
        and a caller cannot tell which it got. Under the first, a fact derived from one user's
        episodes is filed tenant-wide and every user in the tenant can read it.
        """
        if row_scope != scope:
            raise MemoryOperationError(
                f"Record scope {row_scope.key()!r} does not match the write scope {scope.key()!r}. "
                "Batch per scope rather than leaving the choice to the backend.",
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
            "provenance": json.dumps(fields.get("provenance") or {}),
            "metadata": json.dumps(fields.get("metadata") or {}),
        }

    def _record(self, row: Dict[str, Any], scope: Scope) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            tier=MemoryTier(row["tier"]),
            content=row["content"],
            scope=scope,
            score=row.get("score"),
            substrate=Substrate.GRAPH,
            topics=list(row["topics"]) if row.get("topics") is not None else None,
            provenance=json.loads(row.get("provenance") or "{}"),
            t_valid=row.get("t_valid"),
            t_invalid=row.get("t_invalid"),
            created_at=row.get("created_at"),
            expired_at=row.get("expired_at"),
            superseded_by=row.get("superseded_by"),
            metadata=json.loads(row.get("metadata") or "{}"),
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

    # -- reads ----------------------------------------------------------------

    def _merge_hits(self, rows: List[List[Dict[str, Any]]], scope: Scope, query: MemoryQuery) -> List[MemoryRecord]:
        """Merge the per-shape legs, re-rank, and apply ``limit`` to the merged set.

        Each statement applied ``limit`` to its own shape, so the union can hold up to three times
        it; ranking across shapes and cutting again is what makes ``limit`` mean what it says.
        """
        wanted = {tier.value for tier in query.resolved_tiers()}
        hits = [self._record(row, scope) for leg in rows for row in leg if row["tier"] in wanted]
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
        rows = self._run(cypher.traverse_statements(scope), terms=_terms(query.text), limit=query.limit)
        return self._merge_hits(rows, scope, query)

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        query = self._requested(query, tier)
        if not query.limit or not _terms(query.text):
            return []
        rows = await self._arun(cypher.traverse_statements(scope), terms=_terms(query.text), limit=query.limit)
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

    def invalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        recorded_at = now_epoch_s() if at is None else at
        bound = self._bound(self._run(cypher.live_row_statements(scope), edge_id=edge_id), edge_id, t_invalid)
        if bound is None:
            return
        self._run(
            [*cypher.expire_statements(scope), *cypher.insert_bounded_copy_statements(scope)],
            edge_id=edge_id,
            at=recorded_at,
            row_id=str(uuid4()),
            **bound,
        )

    async def ainvalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        recorded_at = now_epoch_s() if at is None else at
        bound = self._bound(await self._arun(cypher.live_row_statements(scope), edge_id=edge_id), edge_id, t_invalid)
        if bound is None:
            return
        await self._arun(
            [*cypher.expire_statements(scope), *cypher.insert_bounded_copy_statements(scope)],
            edge_id=edge_id,
            at=recorded_at,
            row_id=str(uuid4()),
            **bound,
        )
