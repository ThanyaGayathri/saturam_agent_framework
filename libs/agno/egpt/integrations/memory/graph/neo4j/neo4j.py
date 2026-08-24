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
or scheduled audit (SM-G4 #102, options in SM-R10 section 6.1). Bounded-hop BFS traversal from
anchor entities, and the bi-temporal window pushed into every leg of it, shipped with SM-G9 (#107);
graph *enumeration* -- what an unfiltered walk means over a temporal graph -- remains open and
unowned, named at each refusal site (``egpt.memory.graph.episodic``/``semantic``) rather than here.
Episode-to-entity mention links arrived with SM-G6 (#104); the entity names they link
are supplied on the event, not extracted here. **Entity resolution's mechanism, not its policy**
(SM-G8 #106): ``match_entities`` and ``merge_entities`` are the primitives -- lookup and fold --
this backend can support; *which* candidates should merge and at what threshold is
``egpt.memory.graph.resolve.EntityResolutionPipeline``'s decision, not this module's.

**PPR over a GDS projection (SM-H4 #134) is here, gated off by default.** Personalized PageRank
is a fifth, optional leg of :meth:`~Neo4jGraphSubstrate.traverse`/:meth:`~Neo4jGraphSubstrate.atraverse`,
behind ``multihop_ppr_enabled`` (default ``False``) -- Route A, confirmed by SM-R2's live-engine
probe (``benchmarks/memory_graph_engine/run_ppr_route.py``) against the free tier of the GDS plugin
on Neo4j Community. **Deliberately not built here:** SM-H2's rank fusion (this leg merges into
``traverse``'s own dedup-keep-higher-score rule, the same as every other leg -- a distinct
fusion strategy is SM-H2's #132); SM-H1's orchestrator wiring (``retrieval/orchestrator.py`` does
not exist yet -- #131 is open); a re-projection cache/cadence (each call projects fresh under a
call-unique catalog name and drops it after -- SM-R2 section "GDS graph projections are static
snapshots" flags the cost as unmeasured at scale, not this ticket's to resolve); a k-hop/fan-out
bound on the projection itself, unlike the sibling BFS leg's ``max_hops``/``max_anchor_entities``
(round-2 review, Major-tracking -- filed as
`aicoe-tracker#199 <https://github.com/Saturam-Inc/aicoe-tracker/issues/199>`_, since the flag
defaults off and no tenant pays this cost without opting in); IDF-style edge
weighting (no such property exists anywhere in this graph tier yet -- the projection is unweighted);
and the full statistical ranking-quality study SM-R9's spike assigned this ticket (recall/all-hops
gains under a bi-temporal projection, at real per-tenant scale) -- that is a benchmark-scale
measurement effort in its own right, not a "feat" PR's deliverable, and needs its own tracking
ticket. What *is* built and tested here: the mechanism is correct against a real GDS-backed engine
(not a benchmark double), the flag defaults off, and the projection uses SM-R10 section 7's 5a
predicate verbatim -- see :func:`~egpt.integrations.memory.graph.neo4j.cypher.ppr_statements`.
"""

import json
import os
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

from egpt.integrations.memory.graph.neo4j import cypher
from egpt.memory.errors import MemoryProviderConfigError
from egpt.memory.graph.episodic import entity_node_id_for, mention_link_id, mention_rows
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    EntityMerge,
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
from egpt.utils.log import log_debug, log_warning

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

    #: Class-level defaults, not only ``__init__`` assignments: several statement-shape tests in
    #: this module build a substrate via ``Neo4jGraphSubstrate.__new__(...)``, deliberately skipping
    #: ``__init__`` so they need no live driver. Attribute lookup falls back to these when the
    #: instance never set its own, so ``traverse`` still has bounds to pass rather than an
    #: ``AttributeError`` unrelated to whatever the test is actually checking.
    max_traverse_hops: int = cypher.DEFAULT_MAX_HOPS
    max_traverse_anchor_entities: int = cypher.DEFAULT_MAX_ANCHOR_ENTITIES
    #: Per-tenant flag (SM-H4 #134): construction-time, like ``max_traverse_hops`` above, and off
    #: by default per SM-R9's ratified switch threshold (ADR 0003: "PPR off by default, per-tenant
    #: flag"). "Per-tenant" here means per `MemoryProviderConfig` -- SM-C5 (#93), the per-tenant
    #: config *resolver*, has not shipped, so today one substrate instance is one flag value; a
    #: deployment wanting this on for one tenant and off for another already gets that by giving
    #: each tenant its own `graph={"backend": "neo4j", "multihop_ppr_enabled": True, ...}` config
    #: (`MemoryProviderConfig.graph`'s settings dict is passed straight through to this
    #: constructor as kwargs, per `egpt.memory.config.resolve_substrate`) -- SM-C5, when it lands,
    #: resolves *which* config a call uses, not how this flag reaches the substrate once it has one.
    multihop_ppr_enabled: bool = False
    multihop_ppr_alpha: float = cypher.DEFAULT_PPR_ALPHA
    multihop_ppr_seed_top_m: int = cypher.DEFAULT_PPR_SEED_TOP_M

    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        auto_init_schema: bool = True,
        max_traverse_hops: int = cypher.DEFAULT_MAX_HOPS,
        max_traverse_anchor_entities: int = cypher.DEFAULT_MAX_ANCHOR_ENTITIES,
        multihop_ppr_enabled: bool = False,
        multihop_ppr_alpha: float = cypher.DEFAULT_PPR_ALPHA,
        multihop_ppr_seed_top_m: int = cypher.DEFAULT_PPR_SEED_TOP_M,
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
        # Construction-time bounds, not per-call ones -- `GraphSubstrate.traverse` takes no such
        # fields, and closing them here rather than trusting every caller to pass one is SM-G9's
        # (#107) "bounded hops" acceptance criterion, extended to the anchor axis after round-1
        # review found the hop bound alone does not cap the leg's cost (a common query word matches
        # every entity whose name contains it). Validated lazily by `cypher.traverse_statements` on
        # first use rather than here, matching this constructor's existing style of deferring
        # per-call validation to the call -- so `max_traverse_hops=0` (or a negative/non-int value,
        # for either parameter) is accepted silently here and only raises `MemoryOperationError` on
        # the first `traverse`/`atraverse` call, naming `traverse_statements` as the site rather
        # than this constructor (round-3 review).
        self.max_traverse_hops = max_traverse_hops
        self.max_traverse_anchor_entities = max_traverse_anchor_entities
        self.multihop_ppr_enabled = multihop_ppr_enabled
        # `multihop_ppr_alpha`/`multihop_ppr_seed_top_m` are deferred like `max_traverse_hops`
        # above, but *less* deferred than that comment's own pattern might suggest (round-2 review,
        # Nit -- checked directly, not assumed from `max_traverse_hops`'s shape): `_ppr_rows`/
        # `_appr_rows` build every statement, `rank` included, via `_ppr_statements_for` *before*
        # checking whether `seeds` came back empty, and `cypher.ppr_statements` validates both
        # fields while building `rank`. So a misconfigured `alpha`/`seed_top_m` raises
        # `MemoryOperationError` on the first `traverse`/`atraverse` call whose query actually
        # reaches this leg -- non-empty `query.text` and a positive `limit`, `traverse`'s own guard
        # for every leg -- not only once that query's anchor set happens to be non-empty. Accepted
        # silently here at construction, and by any call too trivial to reach the leg at all.
        self.multihop_ppr_alpha = multihop_ppr_alpha
        self.multihop_ppr_seed_top_m = multihop_ppr_seed_top_m
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
        # `scope` is the call scope every caller passes -- the fallback for a row shape that
        # carries no scope columns of its own (none exist today; every `_RETURN`/`_RECALL_RETURN`/
        # `_RECALL_FACTS_RETURN` row does, see below).
        provenance = self._from_json(row.get("provenance"), "provenance", row.get("id"))
        record_scope = scope
        if "tenant_id" in row:
            # **Every row this backend returns carries its own write scope, and it is
            # reconstructed here rather than left as the call scope (SM-G9 #107 round-1 review).**
            # `traverse` used to be the one reader that fell through to the call-scope fallback
            # above -- correct for `MemoryRecord.scope`'s isolation post-condition (which only
            # checks the call scope was honoured), but wrong for anything keying on scope
            # *identity*. `Neo4jGraphSubstrate._merge_hits` dedups the merged legs on
            # `(record.scope.key(), record.id)` precisely to let two different users' rows share
            # one id under a tenant-wide read without colliding (SM-C1's per-scope id space) -- and
            # the call scope is one constant value across the whole call, so keying on it added
            # nothing: two colliding ids from two different writers still collapsed to one row,
            # exactly the bug the (scope, id) key was supposed to close. `_RETURN` was extended
            # with the same `SCOPE_SLOTS` columns `_RECALL_RETURN`/`_RECALL_FACTS_RETURN` already
            # select, so the same reconstruction now runs for every row shape, unconditionally.
            #
            # This is also why `entity_ids` (below) has always had to derive from the write scope,
            # not the call scope: under a tenant-wide call (`Scope(tenant_id="acme")`), the call
            # scope produced an id like `acme/*/*/*/*/entity/berlin` against a node actually merged
            # at `acme/*/ada/*/*/entity/berlin` -- pointing at nothing (round-1 review, PR #90).
            record_scope = Scope(
                tenant_id=row["tenant_id"],
                agent_id=row.get("agent_id"),
                user_id=row.get("user_id"),
                team_id=row.get("team_id"),
                session_id=row.get("session_id"),
            )
        if "mentions" in row:
            provenance["entity_ids"] = [entity_node_id_for(record_scope, name) for name in row["mentions"]]
        elif "subject" in row:
            # `elif`, not a second `if`: today's two row shapes are mutually exclusive --
            # `_RECALL_RETURN` never selects `subject` and `_RECALL_FACTS_RETURN` never selects
            # `mentions` -- but a row carrying both would silently run this branch's own provenance
            # write over the one above's, undetected, if this were a second `if` (round-2 review,
            # PR #102).
            # `recall_facts` rows carry the edge's SPO read from the edge and its two endpoint
            # nodes -- not reconstructed by splitting `content`, which `GraphSubstrate.recall_facts`
            # documents is ambiguous for an entity name containing a space.
            provenance["subject"] = row["subject"]
            provenance["predicate"] = row["predicate"]
            provenance["object"] = row["object"]
        return MemoryRecord(
            id=row["id"],
            tier=MemoryTier(row["tier"]),
            content=row["content"],
            scope=record_scope,
            score=row.get("score"),
            substrate=Substrate.GRAPH,
            topics=list(row["topics"]) if row.get("topics") is not None else None,
            provenance=provenance,
            t_valid=row.get("t_valid"),
            t_invalid=row.get("t_invalid"),
            created_at=row.get("created_at"),
            expired_at=row.get("expired_at"),
            superseded_by=row.get("superseded_by"),
            metadata=self._from_json(row.get("metadata"), "metadata", row.get("id")),
        )

    # -- writes ---------------------------------------------------------------

    @staticmethod
    def _require_event_id(event: MemoryEvent) -> str:
        """The episode id, or a refusal naming it.

        One helper rather than a check in the row builder, because the mention rows address the
        episode by the same id: a guard that only the node write ran would let a batch with a
        missing id build a mention edge keyed on the empty string, which is one edge shared by
        every such episode.
        """
        if not event.id:
            raise MemoryOperationError(
                "MemoryEvent.id is required here: it is the cross-substrate dedup key, and "
                "upsert is by id. The provider stamps one before the call.",
                field_name="id",
            )
        return event.id

    def _episode_rows(self, scope: Scope, events: Sequence[MemoryEvent], created_at: int) -> List[Dict[str, Any]]:
        rows = []
        for event in events:
            self._reject_foreign_scope(scope, event.scope)
            rows.append(
                self._row(
                    scope,
                    self._require_event_id(event),
                    event.tier,
                    event.content,
                    topics=event.topics,
                    t_valid=event.occurred_at,
                    created_at=created_at,
                    metadata=event.metadata,
                )
            )
        return rows

    def _mention_rows(self, scope: Scope, events: Sequence[MemoryEvent], created_at: int) -> List[Dict[str, Any]]:
        """The ``MENTIONS`` edges for a batch, in the shape :func:`cypher.link_mentions_statement`
        binds. What each episode mentions is decided by ``memory.graph.episodic.mention_rows`` and
        not here, so a second backend links the same set.
        """
        rows = [row for event in events for row in mention_rows(scope, self._require_event_id(event), event.entities)]
        if not rows:
            return []
        entity = cypher.scope_props(scope)
        return [
            {
                "scope_key": row["scope_key"],
                "episode_id": row["episode_id"],
                "name": row["name"],
                "entity": entity,
                "props": {**row, **entity, "created_at": created_at},
            }
            for row in rows
        ]

    def _episode_statements(self, scope: Scope, events: Sequence[MemoryEvent]) -> List[Tuple[str, Dict[str, Any]]]:
        """The node write and its mention edges, as one ordered unit.

        Three statements in **one** transaction (:meth:`_run`), and the order is load-bearing:
        ``link`` matches the live episode the first statement just merged, and ``clear`` has to run
        before it or a re-upsert would union the old mention set with the new one instead of
        replacing it. The link statement is omitted when the batch mentions nothing, because
        ``UNWIND []`` ends the pipeline and would make it a round trip that cannot match.
        """
        # One shared timestamp, not one per call: the node and its mention edges are written in
        # the same transaction, so they should carry the same `created_at` rather than one that
        # happens to be a microsecond ahead of the other (round-2 review, PR #90).
        now = now_epoch_s()
        rows = self._episode_rows(scope, events, now)
        mentions = self._mention_rows(scope, events, now)
        statements = [
            (text, {**bound, "rows": rows})
            for text, bound in (cypher.upsert_episodes_statement(scope), cypher.clear_mentions_statement(scope))
        ]
        if mentions:
            text, bound = cypher.link_mentions_statement(scope)
            statements.append((text, {**bound, "mentions": mentions}))
        return statements

    def upsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        if not events:
            return
        self._run(self._episode_statements(scope, events))

    async def aupsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        if not events:
            return
        await self._arun(self._episode_statements(scope, events))

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
                        # Omitted entirely, not set to `None`, when the entity carries no embedding.
                        # `SET n += row.props` merges by key: a key present with a `null` value
                        # **deletes** that property in Neo4j 5.x, while an absent key leaves
                        # whatever the node already has untouched. A candidate written once with an
                        # embedding and updated later with `embedding=None` (a description-only
                        # re-write, or `_canonical`'s output before SM-G8's `resolve.py` review
                        # fix) would otherwise erase the node's vector on every such write, silently
                        # taking it out of every future embedding-similarity match.
                        **(
                            {"embedding": entity.embedding, "embedding_version": entity.embedding_version}
                            if entity.embedding is not None
                            else {}
                        ),
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

    # -- entity resolution (SM-G8 #106) ----------------------------------------

    @staticmethod
    def _entity_from_row(row: Dict[str, Any], scope: Scope) -> EntityWrite:
        """Rebuild an :class:`EntityWrite` from a matched node's row.

        No ``embedding``: :meth:`match_entities` never returns it, because nothing downstream of a
        match needs to re-embed the entity it found -- carrying a 768-float list through every
        matched row would be the one field the caller cannot use, on every row it gets back.
        """
        return EntityWrite(
            name=row["name"],
            entity_type=row["entity_type"],
            scope=scope,
            description=row.get("description"),
            aliases=list(row.get("aliases") or []),
            properties=Neo4jGraphSubstrate._from_json(row.get("properties"), "properties", row["name"]),
            source_episode_ids=list(row.get("source_episode_ids") or []),
            confidence=row.get("confidence", 1.0),
            derived_by=row.get("derived_by"),
        )

    def _matched(self, rows: List[Dict[str, Any]], scope: Scope, limit: int) -> List[Tuple[EntityWrite, float]]:
        """Union the key and embedding legs, keep the best score per name, rank, and cap at ``limit``."""
        best: Dict[str, float] = {}
        by_name: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            name = row["name"]
            score = float(row["score"])
            if name not in best or score > best[name]:
                best[name] = score
                by_name[name] = row
        ordered = sorted(by_name, key=lambda name: (-best[name], name))
        return [(self._entity_from_row(by_name[name], scope), best[name]) for name in ordered[:limit]]

    @staticmethod
    def _require_embedded(candidate: EntityWrite, method: str) -> None:
        if candidate.embedding is None:
            raise MemoryOperationError(
                f"{method} was given {candidate.name!r} with no embedding. The similarity leg has "
                "nothing to compare against, and skipping it silently would report a resolution "
                "pass that checked nothing as one that checked everything.",
                field_name="embedding",
            )

    @staticmethod
    def _match_entities_statements(
        scope: Scope, candidate: EntityWrite, limit: int, min_score: float
    ) -> List[Tuple[str, Dict[str, Any]]]:
        # Narrows for mypy; the caller has already refused a `None` embedding via `_require_embedded`.
        assert candidate.embedding is not None
        key_text, key_params = cypher.match_entities_by_key_statement(scope)
        vector_text, vector_params = cypher.match_entities_by_embedding_statement(
            scope, k=limit + cypher.EMBEDDING_OVERFETCH, limit=limit
        )
        return [
            (key_text, {**key_params, "name": candidate.name, "key": candidate.key}),
            (
                vector_text,
                {
                    **vector_params,
                    "name": candidate.name,
                    "embedding": list(candidate.embedding),
                    "min_score": min_score,
                },
            ),
        ]

    def match_entities(
        self, scope: Scope, candidate: EntityWrite, limit: int = 5, min_score: float = 0.92
    ) -> List[Tuple[EntityWrite, float]]:
        self._reject_foreign_scope(scope, candidate.scope)
        self._require_embedded(candidate, "match_entities")
        key_rows, vector_rows = self._run(self._match_entities_statements(scope, candidate, limit, min_score))
        return self._matched([*key_rows, *vector_rows], scope, limit)

    async def amatch_entities(
        self, scope: Scope, candidate: EntityWrite, limit: int = 5, min_score: float = 0.92
    ) -> List[Tuple[EntityWrite, float]]:
        self._reject_foreign_scope(scope, candidate.scope)
        self._require_embedded(candidate, "amatch_entities")
        key_rows, vector_rows = await self._arun(self._match_entities_statements(scope, candidate, limit, min_score))
        return self._matched([*key_rows, *vector_rows], scope, limit)

    @staticmethod
    def _reject_overlapping_merges(merges: Sequence[EntityMerge]) -> None:
        """Refuse a batch where one merge's canonical is another's duplicate, or the same
        duplicate name appears in two merges.

        Each :class:`EntityMerge` alone already refuses a canonical naming itself as a duplicate
        (``EntityMerge.__post_init__``), but that check has no visibility across merges in the same
        batch. Without the first guard, merge B naming merge A's ``canonical.name`` as a duplicate
        would have A's just-upserted node ``DETACH DELETE``d by B's write, in the same call --
        silent graph corruption rather than a caller error. Without the second (review finding on
        PR #96, round 3), the same duplicate name in two merges is a silent last-write-wins on
        ``canonical_by_duplicate`` -- one merge's intent for that name is discarded with no error
        and no log. ``EntityResolutionPipeline`` never constructs a batch wider than one merge (see
        :meth:`EntityResolutionPipeline.run`), so neither is reached through the pipeline;
        ``merge_entities`` is a public port verb any caller can reach directly.
        """
        canonical_names = {merge.canonical.name for merge in merges}
        duplicate_names = {name for merge in merges for name in merge.duplicate_names}
        overlap = canonical_names & duplicate_names
        if overlap:
            raise MemoryOperationError(
                f"merge_entities was given a batch where {sorted(overlap)} names both a canonical "
                "entity in one merge and a duplicate in another. Resolving that requires deciding "
                "an order between the two merges, which this batch does not state -- split it into "
                "two calls, ordered.",
                field_name="merges",
            )
        seen_duplicates: Set[str] = set()
        for merge in merges:
            for name in merge.duplicate_names:
                if name in seen_duplicates:
                    raise MemoryOperationError(
                        f"merge_entities was given a batch where {name!r} is a duplicate in more "
                        "than one merge. Each name can fold into exactly one canonical per batch; "
                        "split it into two calls, ordered.",
                        field_name="merges",
                    )
                seen_duplicates.add(name)

    @staticmethod
    def _merge_lookup_names(merges: Sequence[EntityMerge]) -> List[str]:
        """Every name the mentioning-episodes read needs: canonicals too, not only duplicates --
        see :meth:`_merge_mention_rows` for why the canonicals' own edges have to be visible."""
        names = {merge.canonical.name for merge in merges}
        names.update(name for merge in merges for name in merge.duplicate_names)
        return list(names)

    def _merge_mention_rows(
        self, scope: Scope, mentions: List[Dict[str, Any]], canonical_by_duplicate: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """The re-pointed edges, in :func:`cypher.link_mentions_statement`'s row shape.

        ``mentions`` carries the canonicals' own live edges alongside the duplicates' -- read by
        :meth:`_merge_lookup_names`'s wider name set -- so this can tell the two apart:

        * A row naming a canonical directly is **not** re-pointed; it is not a duplicate. It exists
          in ``mentions`` only so the next bullet can see it.
        * A row naming a duplicate is skipped, not re-pointed, when the *same episode* already has
          a live edge to that duplicate's canonical. Re-pointing it anyway would ``MERGE`` onto the
          canonical's own edge (both compute the same ``link_id``) and ``SET r = row.props``
          overwrite its ``position`` with the duplicate mention's -- corrupting the order
          ``recall_episodes_statement`` already returns correctly for that edge.
        * Otherwise the duplicate's own ``position`` carries over, defaulting to ``0`` rather than
          ``None`` -- a live edge predating SM-G6's ``position`` field returns ``null`` from Neo4j,
          and ``SET r = row.props`` is a full overwrite: a ``null`` here would propagate onto the
          re-created edge and sort last under ``recall_episodes_statement``'s ``ORDER BY position``.

        Deduplicated on ``link_id`` on top of both: two duplicates folding into one canonical, both
        mentioned by the same episode, must not produce two rows for the edge that survives.
        """
        entity = cypher.scope_props(scope)
        now = now_epoch_s()
        canonical_names = set(canonical_by_duplicate.values())
        already_linked = {(row["episode_id"], row["name"]) for row in mentions if row["name"] in canonical_names}
        seen = set()
        rows = []
        for row in mentions:
            duplicate_name = row["name"]
            if duplicate_name not in canonical_by_duplicate:
                continue  # a canonical's own row -- present only to populate `already_linked`
            canonical_name = canonical_by_duplicate[duplicate_name]
            if (row["episode_id"], canonical_name) in already_linked:
                continue  # the canonical already has a live edge here; its position is authoritative
            link_id = mention_link_id(scope, row["episode_id"], canonical_name)
            if link_id in seen:
                continue
            seen.add(link_id)
            position = row["position"] if row["position"] is not None else 0
            props = {
                "link_id": link_id,
                "episode_id": row["episode_id"],
                "name": canonical_name,
                "position": position,
                **entity,  # carries `scope_key` and every other scope slot
                "created_at": now,
            }
            rows.append(
                {
                    "scope_key": entity["scope_key"],
                    "episode_id": row["episode_id"],
                    "name": canonical_name,
                    "entity": entity,
                    "props": props,
                }
            )
        return rows

    def _merge_write_statements(
        self, scope: Scope, merges: Sequence[EntityMerge], mentions: List[Dict[str, Any]]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """The write half of a merge, once the mentioning episodes have already been read."""
        canonical_by_duplicate = {name: merge.canonical.name for merge in merges for name in merge.duplicate_names}
        upsert_text, upsert_bound = cypher.upsert_entities_statement(scope)
        canonical_rows = self._entity_rows(scope, [merge.canonical for merge in merges])
        statements: List[Tuple[str, Dict[str, Any]]] = [(upsert_text, {**upsert_bound, "rows": canonical_rows})]
        mention_rows_ = self._merge_mention_rows(scope, mentions, canonical_by_duplicate)
        if mention_rows_:
            text, bound = cypher.link_mentions_statement(scope)
            statements.append((text, {**bound, "mentions": mention_rows_}))
        names = list(canonical_by_duplicate)
        text, params = cypher.delete_entities_statement(scope)
        statements.append((text, {**params, "names": names}))
        return statements

    def merge_entities(self, scope: Scope, merges: Sequence[EntityMerge]) -> None:
        """One transaction, unlike every other write on this port: the mentioning-episodes read
        that :meth:`_merge_mention_rows` depends on has to observe the same state the write
        commits against, or a concurrent write adding a mention to a duplicate between the two
        would have that edge silently lost -- see the port docstring's TOCTOU note.
        """
        if not merges:
            return
        for merge in merges:
            self._reject_foreign_scope(scope, merge.canonical.scope)
        self._reject_overlapping_merges(merges)
        mention_text, mention_params = cypher.mentioning_episodes_statement(scope)
        names = self._merge_lookup_names(merges)
        with self._translated("merge_entities"), self._driver.session(database=self.database) as session:
            with session.begin_transaction() as tx:
                mentions = [dict(record) for record in tx.run(mention_text, **{**mention_params, "names": names})]
                for text, params in self._merge_write_statements(scope, merges, mentions):
                    tx.run(text, **params)
                tx.commit()

    async def amerge_entities(self, scope: Scope, merges: Sequence[EntityMerge]) -> None:
        """Async twin of :meth:`merge_entities`, with the same one-transaction rule."""
        if not merges:
            return
        for merge in merges:
            self._reject_foreign_scope(scope, merge.canonical.scope)
        self._reject_overlapping_merges(merges)
        mention_text, mention_params = cypher.mentioning_episodes_statement(scope)
        names = self._merge_lookup_names(merges)
        with self._translated("merge_entities"):
            async with self._async_driver().session(database=self.database) as session:
                tx = await session.begin_transaction()
                try:
                    result = await tx.run(mention_text, **{**mention_params, "names": names})
                    mentions = [dict(record) async for record in result]
                    for text, params in self._merge_write_statements(scope, merges, mentions):
                        await tx.run(text, **params)
                    await tx.commit()
                except BaseException:
                    await tx.rollback()
                    raise

    # -- reads ----------------------------------------------------------------

    def _merge_hits(self, rows: List[List[Dict[str, Any]]], scope: Scope, query: MemoryQuery) -> List[MemoryRecord]:
        """Merge the legs, dedup by ``(scope, id)`` keeping the higher score, re-rank, and apply
        ``limit``.

        Each statement applied ``limit`` to its own leg, so the union can hold up to four times it --
        five when ``multihop_ppr_enabled`` adds its fifth leg (SM-H4 #134) -- ranking across legs
        and cutting again is what makes ``limit`` mean what it says.

        **Deduped by ``(scope, id)``, not merely concatenated (SM-G9 #107).** The three row-shape
        legs cannot collide -- each addresses a disjoint shape -- but the anchor-seeded multi-hop leg
        reads the same ``:REL`` edges the row-shape REL leg does, so one edge that both lexically
        matches the query *and* sits within ``max_hops`` of an anchor is a legitimate hit on both.
        Returning it twice would double its weight in whatever sums these scores downstream (the
        provider's ``_merge`` does exactly that); keeping the higher of the two scores is the correct
        answer to "how relevant is this edge", not an arbitrary tiebreak. **Scope joins id in the
        dedup key** -- ``id`` alone is not unique across scopes: a tenant-wide read can legitimately
        return two different users' rows sharing one id, and a bare-id dedup would collapse them into
        one (``test_a_wider_scope_reads_what_a_narrower_one_wrote``-shaped case).
        """
        best: Dict[Tuple[str, str], MemoryRecord] = {}
        for leg in rows:
            for row in leg:
                record = self._record(row, scope)
                dedup_key = (record.scope.key(), record.id)
                current = best.get(dedup_key)
                if current is None or (record.score or 0.0) > (current.score or 0.0):
                    best[dedup_key] = record
        hits = sorted(best.values(), key=lambda r: (-(r.score or 0.0), r.created_at or 0, r.content, r.id))
        return hits[: query.limit]

    @staticmethod
    def _requested(query: MemoryQuery, tier: Optional[MemoryTier]) -> MemoryQuery:
        """Narrow to a single tier when asked; otherwise the query's own set stands.

        The tier set is then resolved through ``MemoryQuery.resolved_tiers`` rather than re-derived
        here, because when one leg honoured the tier filter and the other did not, the leakage
        surfaced only after the merge and was hard to trace back.
        """
        return query if tier is None else replace(query, tiers=[tier])

    @staticmethod
    def _window_params(query: MemoryQuery) -> Dict[str, Any]:
        """``valid_from``/``valid_to`` for the traversal's bi-temporal predicate (SM-G9 #107).

        Always both keys, bound to ``None`` for an unwindowed read: the Cypher predicate references
        ``$valid_from``/``$valid_to`` unconditionally, and an omitted parameter is a driver error,
        not "no predicate" -- the same reason ``recall_episodes_statement`` binds ``$since``/
        ``$until`` the same way.
        """
        window = query.window()
        return {"valid_from": window.valid_from if window else None, "valid_to": window.valid_to if window else None}

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        query = self._requested(query, tier)
        if not query.limit or not _terms(query.text):
            return []
        rows = self._run(
            cypher.traverse_statements(
                scope, max_hops=self.max_traverse_hops, max_anchor_entities=self.max_traverse_anchor_entities
            ),
            terms=_terms(query.text),
            limit=query.limit,
            tiers=_tiers(query),
            **self._window_params(query),
        )
        if self.multihop_ppr_enabled:
            rows = [*rows, self._ppr_rows(scope, query)]
        return self._merge_hits(rows, scope, query)

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        query = self._requested(query, tier)
        if not query.limit or not _terms(query.text):
            return []
        rows = await self._arun(
            cypher.traverse_statements(
                scope, max_hops=self.max_traverse_hops, max_anchor_entities=self.max_traverse_anchor_entities
            ),
            terms=_terms(query.text),
            limit=query.limit,
            tiers=_tiers(query),
            **self._window_params(query),
        )
        if self.multihop_ppr_enabled:
            rows = [*rows, await self._appr_rows(scope, query)]
        return self._merge_hits(rows, scope, query)

    @staticmethod
    def _ppr_temporal(query: MemoryQuery) -> Dict[str, Optional[int]]:
        """The bounds :func:`~egpt.integrations.memory.graph.neo4j.cypher.ppr_statements`'s
        ``as_of``/``valid_from``/``valid_to`` branch on (SM-H4 #134).

        ``query.as_of`` and a window are mutually exclusive on :class:`MemoryQuery` (validated
        there), so this passes through whichever the caller actually set and lets the statement
        builder pick the matching predicate -- point-in-time for ``as_of``, overlap
        (:func:`~egpt.integrations.memory.graph.neo4j.cypher._window_predicate`) for a window or for
        neither set. A prior version collapsed a window to its ``valid_to`` bound and ran it through
        the point-in-time predicate, which is a *stricter*, different question than "does this
        edge's validity overlap my window" -- round-1 review, Major; see ``ppr_statements``'s own
        docstring for the worked counter-example.
        """
        if query.as_of is not None:
            return {"as_of": query.as_of, "valid_from": None, "valid_to": None}
        window = query.window()
        return {
            "as_of": None,
            "valid_from": window.valid_from if window else None,
            "valid_to": window.valid_to if window else None,
        }

    def _ppr_statements_for(self, scope: Scope, query: MemoryQuery, graph_name: str) -> Dict[str, Any]:
        # `**self._ppr_temporal(query)` type-checks as unsound to mypy: unpacking a
        # `Dict[str, Optional[int]]` leaves it unable to rule out the dict also supplying
        # `graph_name: Optional[str]`, so the three bounds are passed by name instead.
        temporal = self._ppr_temporal(query)
        return cypher.ppr_statements(
            scope,
            terms=_terms(query.text),
            seed_top_m=self.multihop_ppr_seed_top_m,
            alpha=self.multihop_ppr_alpha,
            tiers=_tiers(query),
            limit=query.limit,
            graph_name=graph_name,
            as_of=temporal["as_of"],
            valid_from=temporal["valid_from"],
            valid_to=temporal["valid_to"],
        )

    def _ppr_rows(self, scope: Scope, query: MemoryQuery) -> List[Dict[str, Any]]:
        """The PPR leg's rows, run and cleaned up outside :meth:`_run`'s transaction (SM-H4 #134).

        Not folded into :meth:`_run`'s one-transaction-per-call model, on purpose: a GDS graph
        projection is a server-side catalog entry that the surrounding Neo4j transaction does not
        govern -- a projection made mid-transaction is not rolled back if the transaction later is,
        unlike an ordinary write. So cleanup here is a Python ``try``/``finally`` around plain
        per-statement ``session.run`` calls (the same style :meth:`init_schema` already uses for
        catalog/schema-shaped operations, for the identical reason), not the driver's transactional
        rollback.

        **The catalog name is unique per call** (:func:`~egpt.integrations.memory.graph.neo4j.
        cypher.ppr_graph_name`'s deterministic per-scope prefix, plus a fresh ``uuid4`` suffix here
        -- round-1 review, Major). A deterministic per-scope name let two concurrent PPR calls for
        the same scope collide on one catalog entry: one call's cleanup ``drop`` could destroy
        another's in-flight projection, two ``project`` calls could race GDS's "already exists"
        refusal, or -- worse, with no error at all -- one call's ``rank`` could silently complete
        over a *different* concurrent call's re-projected topology built at a different ``as_of``. A
        unique name per call cannot collide with a concurrent one. The residual is a **smaller**,
        different risk than what it replaces: a hard crash between creating the projection and this
        method's own ``finally`` leaves one orphaned catalog entry under a name nothing will ever
        reuse, rather than a live correctness hazard. Bounded and recoverable (an admin
        ``gds.graph.drop`` or an engine restart clears it); a periodic sweep is the natural mitigation
        if this proves to matter in practice, not built here.

        **``seeds`` is checked before any GDS call at all** (:func:`~egpt.integrations.memory.
        graph.neo4j.cypher.ppr_statements`'s own docstring explains why -- an empty ``sourceNodes``
        silently degrades to unseeded global PageRank rather than refusing). An empty result means
        this call never touches ``gds.graph.*`` at all, so there is nothing for the ``finally`` to
        clean up either.
        """
        graph_name = f"{cypher.ppr_graph_name(scope)}_{uuid4().hex[:12]}"
        statements = self._ppr_statements_for(scope, query, graph_name)
        with self._translated("multihop PPR"), self._driver.session(database=self.database) as session:
            if not [dict(record) for record in session.run(*statements["seeds"])]:
                return []
            try:
                session.run(*statements["project"]).consume()
                return [dict(record) for record in session.run(*statements["rank"])]
            finally:
                self._drop_ppr_graph(session, graph_name, statements["drop"])

    def _drop_ppr_graph(self, session: Any, graph_name: str, drop_statement: Tuple[str, Dict[str, Any]]) -> None:
        """Cleanup ``drop``, isolated so its own failure cannot mask ``rank``'s (SM-H4 #134, round-1
        review, Minor). A bare ``finally: session.run(...)`` that itself raised (the same
        connectivity problem that failed ``rank``, say) replaces the original exception outright --
        Python keeps only the most recent one -- so the actual root cause never reaches
        ``_translated``'s wrapping. Logged and swallowed instead: the original exception (if any) is
        what propagates, and an orphaned catalog entry here is the same bounded, recoverable residual
        this method's own docstring already accepts for a hard crash -- one more code path landing
        in the same place, not a new risk.
        """
        try:
            session.run(*drop_statement).consume()
        except Exception as exc:  # noqa: BLE001 -- cleanup-only; the caller's own exception must win
            log_warning(f"multihop PPR: failed to drop GDS graph {graph_name!r} during cleanup: {exc}")

    async def _appr_rows(self, scope: Scope, query: MemoryQuery) -> List[Dict[str, Any]]:
        """Async twin of :meth:`_ppr_rows`, with the same unique-name/seeds-guard/drop-after rule."""
        graph_name = f"{cypher.ppr_graph_name(scope)}_{uuid4().hex[:12]}"
        statements = self._ppr_statements_for(scope, query, graph_name)
        with self._translated("multihop PPR"):
            async with self._async_driver().session(database=self.database) as session:
                seed_result = await session.run(*statements["seeds"])
                if not [dict(record) async for record in seed_result]:
                    return []
                try:
                    await (await session.run(*statements["project"])).consume()
                    result = await session.run(*statements["rank"])
                    return [dict(record) async for record in result]
                finally:
                    await self._adrop_ppr_graph(session, graph_name, statements["drop"])

    async def _adrop_ppr_graph(self, session: Any, graph_name: str, drop_statement: Tuple[str, Dict[str, Any]]) -> None:
        """Async twin of :meth:`_drop_ppr_graph`, with the same swallow-and-log rule."""
        try:
            await (await session.run(*drop_statement)).consume()
        except Exception as exc:  # noqa: BLE001 -- cleanup-only; the caller's own exception must win
            log_warning(f"multihop PPR: failed to drop GDS graph {graph_name!r} during cleanup: {exc}")

    def recall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        # The port's refusals -- unscoped, unfiltered, inverted window -- are the statement
        # builder's, so both halves here get them from one place and so does a direct caller.
        statement = cypher.recall_episodes_statement(scope, entities=entities, since=since, until=until, limit=limit)
        return [self._record(row, scope) for row in self._run([statement])[0]]

    async def arecall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        statement = cypher.recall_episodes_statement(scope, entities=entities, since=since, until=until, limit=limit)
        return [self._record(row, scope) for row in (await self._arun([statement]))[0]]

    def recall_facts(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        relation: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        # The port's refusals live in the statement builder, exactly as `recall_episodes` reuses
        # them, so both halves here and a direct caller all refuse identically.
        statement = cypher.recall_facts_statement(
            scope, entities=entities, relation=relation, since=since, until=until, limit=limit
        )
        return [self._record(row, scope) for row in self._run([statement])[0]]

    async def arecall_facts(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        relation: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        statement = cypher.recall_facts_statement(
            scope, entities=entities, relation=relation, since=since, until=until, limit=limit
        )
        return [self._record(row, scope) for row in (await self._arun([statement]))[0]]

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
