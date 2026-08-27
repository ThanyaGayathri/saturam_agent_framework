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
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider  # noqa: E402
from egpt.memory.config import MemoryProviderConfig  # noqa: E402
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope  # noqa: E402
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
    """The four attributes every memory span must carry, per SM-X4's acceptance criteria."""
    assert attrs["egpt.memory.tenant_id"] == "acme"
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
