"""
Memory Tracing (Langfuse/OTel Spans)
=====================================

This example shows SM-X4: manual OTel spans around the `MemoryProvider` seam.

Agent, model and tool calls are auto-instrumented via `AgnoInstrumentor` (wired
by `setup_tracing()`, see `cookbook/observability/trace_to_database.py`) -- but
`MemoryProvider.read`/`write`/`consolidate` have nothing to do with an agent
run, so that instrumentor never sees them. Before this ticket a consolidation
pass or a memory write was invisible in a trace even with tracing fully set
up. `egpt.tracing.memory.memory_span` closes that gap: `CoLocatedMemoryProvider`
now opens one span per call, named `memory.read` / `memory.write` /
`memory.consolidate` (the async twins share these names rather than getting
`memory.aread` etc., so a caller does not need to know which half ran).

Key concepts:
- Every memory span carries four attributes: `egpt.memory.tenant_id` (from
  `Scope.tenant_id`), `egpt.memory.tier` (which tier(s) the call touched, or
  the derived default when none were named), `egpt.memory.store` (which
  substrates this provider has wired -- `substrates()`, config-level, not
  per-call), and `egpt.memory.latency_ms` (wall-clock time of the call).
- Tracing is strictly additive: `memory_span` wraps every OpenTelemetry call
  in a bare `except Exception` that logs and falls through (the same idiom
  `agent/_run.py`'s `_set_tenant_on_span` uses for EGPT-4.2's tenant
  enrichment), so a tracing bug can never break the memory call it wraps.

This file needs no external service for the memory store itself: there is no
SQLite-backed vector substrate in this repo (production backends are pgvector
and Neo4j), so -- following the same precedent as
`cookbook/11_memory/23_consolidation_worker.py` and
`cookbook/11_memory/39_working_memory.py` -- it uses a tiny in-memory vector
stand-in, kept vector-only (a graph substrate is optional, SM-C5). Tracing
itself, per CLAUDE.md's "SQLite for dev only", persists to a local SQLite
`tmp/memory_tracing.db` via `DatabaseSpanExporter` -- no Langfuse keys needed.

One environment note: `setup_tracing()` requires the optional
`openinference-instrumentation-agno` package (agent-side auto-instrumentation)
before it will claim the global TracerProvider -- see its docstring in
`egpt/tracing/setup.py`. That package is unrelated to memory tracing (which
never goes through `AgnoInstrumentor`), but if it is not installed in the
running environment, `setup_tracing()` logs a warning and leaves tracing
inactive. This script detects that and falls back to wiring the same
`DatabaseSpanExporter` directly, so the memory spans below are visible either
way.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from egpt.integrations.db.sqlite import SqliteDb
from egpt.memory import (
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    require_scope,
)
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.tracing import DatabaseSpanExporter, setup_tracing

SCOPE = Scope(tenant_id="acme", user_id="ada@example.com")

# ---------------------------------------------------------------------------
# A toy vector substrate -- the same trimmed shape 23_consolidation_worker.py
# and 39_working_memory.py use, so this file needs no database, no API key,
# and no network for the memory store itself.
# ---------------------------------------------------------------------------


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity."""

    def __init__(self) -> None:
        self.rows: Dict[str, MemoryRecord] = {}

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        for record in records:
            self.rows[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        wanted = {tier} if tier else set(query.resolved_tiers())
        terms = set(query.text.lower().split())
        hits = [
            replace(
                record, score=float(len(terms & set(record.content.lower().split())))
            )
            for record in self.rows.values()
            if record.tier in wanted and record.expired_at is None
        ]
        ranked = sorted((h for h in hits if h.score), key=lambda r: -(r.score or 0.0))
        return ranked[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        rows = [replace(r) for r in self.rows.values() if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        if after is not None:
            rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="get_record")
        return self.rows.get(id)

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)


# ---------------------------------------------------------------------------
# Tracing setup
# ---------------------------------------------------------------------------


def configure_tracing(db: SqliteDb) -> None:
    """Call the standard entry point, and fall back to wiring the DB exporter directly if the
    optional AgnoInstrumentor dependency is not present -- see the module docstring."""
    from opentelemetry import trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    # Default SimpleSpanProcessor: a blocking DB write per span.end(), fine for a single-run
    # script -- see egpt/tracing/memory.py's module docstring for why a latency-sensitive async
    # path should pass batch_processing=True instead.
    setup_tracing(db=db)

    if isinstance(trace_api.get_tracer_provider(), TracerProvider):
        print(
            "setup_tracing() activated the global TracerProvider (AgnoInstrumentor available)."
        )
        return

    print(
        "setup_tracing() left tracing inactive -- openinference-instrumentation-agno is not "
        "installed in this environment (see the module docstring). Memory tracing does not need "
        "it, so wiring the same DatabaseSpanExporter directly:"
    )
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(DatabaseSpanExporter(db=db)))
    trace_api.set_tracer_provider(tracer_provider)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db = SqliteDb(db_file="tmp/memory_tracing.db")
    configure_tracing(db)

    provider = CoLocatedMemoryProvider(
        vector=InMemoryVector(),
        config=MemoryProviderConfig(provider="co_located"),
    )

    print()
    print("write -> read -> consolidate, each wrapped in its own memory span")
    print("=" * 68)

    receipt = provider.write(
        SCOPE,
        [
            MemoryEvent(content="the customer prefers email over phone", scope=SCOPE),
            MemoryEvent(content="the customer is on the enterprise plan", scope=SCOPE),
        ],
    )
    print(f"write:       accepted {receipt.accepted} event(s)")

    slice_ = provider.read(SCOPE, MemoryQuery(text="customer email", limit=5))
    print(f"read:        {len(slice_.records)} record(s)")

    report = provider.consolidate(SCOPE)
    print(
        f"consolidate: {report.episodic_scanned} episode(s) -> {report.semantic_written} fact(s)"
    )

    print()
    print("Spans captured for this run")
    print("=" * 68)
    spans = [span for span in db.get_spans() if span.name.startswith("memory.")]
    for span in sorted(spans, key=lambda s: s.start_time):
        attrs = span.attributes or {}
        print(span.name)
        print(f"  egpt.memory.tenant_id: {attrs.get('egpt.memory.tenant_id')}")
        print(f"  egpt.memory.tier:      {attrs.get('egpt.memory.tier')}")
        print(f"  egpt.memory.store:     {attrs.get('egpt.memory.store')}")
        print(f"  egpt.memory.latency_ms: {attrs.get('egpt.memory.latency_ms')}")

    assert {"memory.write", "memory.read", "memory.consolidate"} <= {
        s.name for s in spans
    }, "expected one span each for write, read, and consolidate"
    print()
    print(f"{len(spans)} memory span(s) captured, all three verbs represented.")
