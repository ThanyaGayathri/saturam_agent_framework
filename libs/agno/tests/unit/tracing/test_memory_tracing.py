"""Tests for SM-X4: OTel spans for CoLocatedMemoryProvider.read/write/consolidate.

Memory operations sit outside AgnoInstrumentor's reach (it wraps agent/model/tool calls, not the
`MemoryProvider` seam), so `egpt.tracing.memory.memory_span` is what puts a span around
`read`/`write`/`consolidate` -- and their async twins, which share one span name with the sync
half rather than getting their own `memory.aread`/`memory.awrite`/`memory.aconsolidate`.

Registers a real `TracerProvider` wired to an in-memory exporter (the standard OTel testing
pattern) so spans can be inspected directly, rather than mocking the tracing API.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk", reason="tracing tests require opentelemetry-sdk")

from opentelemetry import trace as trace_api  # noqa: E402
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import StatusCode  # noqa: E402

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider  # noqa: E402
from egpt.memory.config import MemoryProviderConfig  # noqa: E402
from egpt.memory.types import MemoryEvent, MemoryQuery, PartialWriteError, Scope  # noqa: E402
from egpt.tracing.schemas import Span, create_trace_from_spans  # noqa: E402
from tests.unit.memory._fakes import FakeGraph, FakeVector  # noqa: E402

ACME = Scope(tenant_id="acme", user_id="u1")


# ---------------------------------------------------------------------------
# Fixtures -- mirrors test_langfuse_setup.py's env/provider hygiene, so this
# file neither inherits ambient tracing state nor leaks its own into others.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_tracing_env(monkeypatch):
    """Remove all tracing-related env vars before each test to prevent ambient leaks."""
    for prefix in ("", "EGPT_", "AGNO_"):
        for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_ENABLED"):
            monkeypatch.delenv(f"{prefix}{name}", raising=False)
    monkeypatch.delenv("EGPT_TRACING_DB_ENABLED", raising=False)


@pytest.fixture(autouse=True)
def _reset_global_tracer(monkeypatch):
    """Reset the global tracer provider before AND after each test.

    Forces None both ways to avoid leaking a TracerProvider into subsequent test files running in
    the same pytest worker -- the OTel API's tracer provider is a process-global, set-once object.
    """
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", trace_api._TRACER_PROVIDER_SET_ONCE.__class__())
    yield
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", trace_api._TRACER_PROVIDER_SET_ONCE.__class__())


@pytest.fixture
def span_exporter():
    """A real TracerProvider wired to an in-memory exporter, set as the global provider.

    Standard OTel testing pattern: a `SimpleSpanProcessor` exports synchronously, so a span is
    visible in `get_finished_spans()` the instant its `with` block exits -- no flush needed.
    """
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace_api.set_tracer_provider(tracer_provider)
    return exporter


def build_provider() -> CoLocatedMemoryProvider:
    """A co-located provider over the same in-memory substrate doubles the adapter's own test
    suite uses (`tests/unit/memory/test_provider_co_located.py`), reused rather than reinvented."""
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)


def spans_named(exporter: InMemorySpanExporter, name: str):
    return [span for span in exporter.get_finished_spans() if span.name == name]


def assert_common_attributes(attrs) -> None:
    """The five attributes every memory span must carry, per SM-X4's acceptance criteria."""
    assert attrs["egpt.memory.tenant_id"] == "acme"
    # The bare `egpt.tenant_id` (no `.memory.` infix) is also set -- `create_trace_from_spans`
    # (`tracing/schemas.py`) resolves `Trace.tenant_id` by reading this exact key off the trace's
    # root span, with no `.memory.` fallback. See `test_standalone_memory_span_resolves_a_tenant_id`.
    assert attrs["egpt.tenant_id"] == "acme"
    assert isinstance(attrs["egpt.memory.tier"], str) and attrs["egpt.memory.tier"]
    assert isinstance(attrs["egpt.memory.store"], str) and attrs["egpt.memory.store"]
    assert isinstance(attrs["egpt.memory.latency_ms"], float)
    assert attrs["egpt.memory.latency_ms"] >= 0.0


# ---------------------------------------------------------------------------
# write / awrite -> memory.write
# ---------------------------------------------------------------------------


def test_write_produces_a_memory_write_span(span_exporter):
    provider = build_provider()
    events = [MemoryEvent(content="the customer prefers email over phone", scope=ACME)]

    receipt = provider.write(ACME, events)

    assert receipt.accepted == 1
    spans = spans_named(span_exporter, "memory.write")
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert_common_attributes(attrs)
    # `write`'s default tier (no tier named on the event) is DEFAULT_WRITE_TIER == episodic.
    assert attrs["egpt.memory.tier"] == "episodic"


async def test_awrite_produces_a_memory_write_span(span_exporter):
    """The async twin produces the SAME span name as the sync half -- not `memory.awrite`."""
    provider = build_provider()
    events = [MemoryEvent(content="the customer prefers email over phone", scope=ACME)]

    receipt = await provider.awrite(ACME, events)

    assert receipt.accepted == 1
    spans = spans_named(span_exporter, "memory.write")
    assert len(spans) == 1
    assert_common_attributes(spans[0].attributes)
    # No stray `memory.awrite` span was created alongside it.
    assert spans_named(span_exporter, "memory.awrite") == []


# ---------------------------------------------------------------------------
# read / aread -> memory.read
# ---------------------------------------------------------------------------


def test_read_produces_a_memory_read_span(span_exporter):
    provider = build_provider()
    provider.write(ACME, [MemoryEvent(content="the customer prefers email over phone", scope=ACME)])
    span_exporter.clear()

    slice_ = provider.read(ACME, MemoryQuery(text="customer email", limit=5))

    assert slice_.scope == ACME
    spans = spans_named(span_exporter, "memory.read")
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert_common_attributes(attrs)
    # No tiers were named on the query, so the read spans every persistent tier.
    assert attrs["egpt.memory.tier"] == "all"


async def test_aread_produces_a_memory_read_span(span_exporter):
    provider = build_provider()
    await provider.awrite(ACME, [MemoryEvent(content="the customer prefers email over phone", scope=ACME)])
    span_exporter.clear()

    slice_ = await provider.aread(ACME, MemoryQuery(text="customer email", limit=5))

    assert slice_.scope == ACME
    spans = spans_named(span_exporter, "memory.read")
    assert len(spans) == 1
    assert_common_attributes(spans[0].attributes)
    assert spans_named(span_exporter, "memory.aread") == []


# ---------------------------------------------------------------------------
# consolidate / aconsolidate -> memory.consolidate
# ---------------------------------------------------------------------------


def test_consolidate_produces_a_memory_consolidate_span(span_exporter):
    provider = build_provider()
    provider.write(ACME, [MemoryEvent(content="the customer prefers email over phone", scope=ACME)])
    span_exporter.clear()

    report = provider.consolidate(ACME)

    assert report.scope == ACME
    spans = spans_named(span_exporter, "memory.consolidate")
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert_common_attributes(attrs)
    # No explicit `tiers=` argument, so the derived-from-TIER_POLICY default names episodic->semantic.
    assert attrs["egpt.memory.tier"] == "episodic->semantic"


async def test_aconsolidate_produces_a_memory_consolidate_span(span_exporter):
    provider = build_provider()
    await provider.awrite(ACME, [MemoryEvent(content="the customer prefers email over phone", scope=ACME)])
    span_exporter.clear()

    report = await provider.aconsolidate(ACME)

    assert report.scope == ACME
    spans = spans_named(span_exporter, "memory.consolidate")
    assert len(spans) == 1
    assert_common_attributes(spans[0].attributes)
    assert spans_named(span_exporter, "memory.aconsolidate") == []


# ---------------------------------------------------------------------------
# Non-blocking: tracing absent must never break a memory call
# ---------------------------------------------------------------------------


def test_memory_operations_still_work_when_tracing_is_not_configured():
    """No TracerProvider is registered here (only `_reset_global_tracer`'s autouse reset ran), so
    `memory_span` gets a non-recording span from the default no-op provider. The point of this
    test is that read/write/consolidate return their ordinary values regardless -- tracing is
    strictly additive, never load-bearing for the memory call it wraps."""
    provider = build_provider()
    events = [MemoryEvent(content="the customer prefers email over phone", scope=ACME)]

    receipt = provider.write(ACME, events)
    assert receipt.accepted == 1

    report = provider.consolidate(ACME)
    assert report.scope == ACME
    assert report.semantic_written >= 1

    slice_ = provider.read(ACME, MemoryQuery(text="customer email", limit=5))
    assert slice_.scope == ACME
    assert len(slice_.records) >= 1


# ---------------------------------------------------------------------------
# Regression: a raising tracer/processor, or a raising wrapped call, must
# each fail independently -- neither may take the other down with it.
# ---------------------------------------------------------------------------


class _ExplodingOnStartProcessor(SpanProcessor):
    """A SpanProcessor whose `on_start` raises -- the exact shape the span-lifecycle gap this
    file regresses against was found in: `start_as_current_span(...)`'s `__enter__` is what calls
    every registered processor's `on_start`, and `SynchronousMultiSpanProcessor.on_start` does not
    itself guard a processor's exception, so a raising processor must not be allowed to break the
    memory call `memory_span` wraps."""

    def on_start(self, span, parent_context=None):
        raise RuntimeError("on_start exploded")

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def test_write_still_completes_when_a_span_processor_explodes_on_start(span_exporter):
    """`memory_span`'s guarding `try/except` must cover entering the span, not just building the
    context-manager object -- otherwise a raising processor propagates before the wrapped write
    ever runs."""
    trace_api.get_tracer_provider().add_span_processor(_ExplodingOnStartProcessor())
    provider = build_provider()
    events = [MemoryEvent(content="the customer prefers email over phone", scope=ACME)]

    receipt = provider.write(ACME, events)

    assert receipt.accepted == 1
    # Span creation aborted entirely (the exploding processor ran before any span was minted), so
    # nothing reached the exporter either -- this is the correct fallback, not a partial span.
    assert spans_named(span_exporter, "memory.write") == []


def test_a_raised_exception_from_the_wrapped_call_still_propagates_and_closes_the_span(span_exporter):
    """The half `memory_span`'s fix must not break: a real failure from the memory call itself
    must still reach the caller unmodified, and the span must still close, recorded as an error."""
    provider = build_provider()
    original = provider.graph.upsert_episodes

    def explode(scope, events_):
        raise RuntimeError("graph down")

    provider.graph.upsert_episodes = explode  # type: ignore[method-assign]
    try:
        with pytest.raises(PartialWriteError):
            provider.write(ACME, [MemoryEvent(content="episode one", scope=ACME)])
    finally:
        provider.graph.upsert_episodes = original  # type: ignore[method-assign]

    spans = spans_named(span_exporter, "memory.write")
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in spans[0].events)


# ---------------------------------------------------------------------------
# Regression: a standalone memory span (no enclosing agent run) must still
# resolve a tenant_id once it goes through create_trace_from_spans.
# ---------------------------------------------------------------------------


def test_standalone_memory_span_resolves_a_tenant_id(span_exporter):
    """A memory call with no enclosing AGENT span -- e.g. a background consolidation worker, or
    this PR's own cookbook -- makes the `memory.*` span itself the trace's root span.
    `create_trace_from_spans` reads `Trace.tenant_id` off the root span's `egpt.tenant_id`
    attribute only, with no `egpt.memory.tenant_id` fallback -- so `memory_span` must set the bare
    key too, or every such trace resolves to `tenant_id=None` and gets fail-closed hidden by every
    tenant-scoped trace API caller."""
    provider = build_provider()
    events = [MemoryEvent(content="the customer prefers email over phone", scope=ACME)]

    provider.write(ACME, events)

    otel_spans = span_exporter.get_finished_spans()
    assert len(otel_spans) == 1
    assert otel_spans[0].parent is None  # confirms this span really is the trace's root

    converted = [Span.from_otel_span(s) for s in otel_spans]
    trace = create_trace_from_spans(converted)

    assert trace is not None
    assert trace.tenant_id == "acme"


# ---------------------------------------------------------------------------
# Regression: a legal-but-empty tier/store must be recorded as a meaningful
# value, not silently dropped as if tracing had failed.
# ---------------------------------------------------------------------------


def test_write_with_no_events_records_an_explicit_empty_tier(span_exporter):
    """`write(scope, [])` is legal (`_prepare` has no emptiness guard). The derived tier label is
    `""`, which must still be recorded -- as `"none"`, not omitted -- so it stays distinguishable
    from a span that failed to record attributes at all."""
    provider = build_provider()

    provider.write(ACME, [])

    spans = spans_named(span_exporter, "memory.write")
    assert len(spans) == 1
    assert spans[0].attributes["egpt.memory.tier"] == "none"


def test_read_with_empty_tiers_records_an_explicit_empty_tier(span_exporter):
    """`MemoryQuery(tiers=[])` is a legal "search no tiers" state, distinct from `tiers=None`
    (which means "all"). It must not collapse to the same `""` a tracing failure would produce."""
    provider = build_provider()
    provider.write(ACME, [MemoryEvent(content="the customer prefers email over phone", scope=ACME)])
    span_exporter.clear()

    provider.read(ACME, MemoryQuery(text="customer email", limit=5, tiers=[]))

    spans = spans_named(span_exporter, "memory.read")
    assert len(spans) == 1
    assert spans[0].attributes["egpt.memory.tier"] == "none"
