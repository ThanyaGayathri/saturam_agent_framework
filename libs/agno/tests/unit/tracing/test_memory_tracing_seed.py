"""Tests for SM-X4's follow-up: OTel spans for SeedMemoryProvider.read/write/consolidate.

`SeedMemoryProvider` is the *other* production `MemoryProvider` implementation (the ADR 0003
WRAP/EXTEND bridge over the pre-existing legacy memory stack) -- the original PR #158 wrapped only
`CoLocatedMemoryProvider`, leaving this one untraced. Mirrors `test_memory_tracing.py`'s fixtures
and assertions; kept as a separate file/fixture set rather than importing them, matching that
file's own "neither inherits ambient tracing state nor leaks its own" framing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk", reason="tracing tests require opentelemetry-sdk")

from opentelemetry import trace as trace_api  # noqa: E402
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import StatusCode  # noqa: E402

from egpt.memory.adapters.seed import SeedMemoryProvider  # noqa: E402
from egpt.memory.config import MemoryProviderConfig  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import MemoryEvent, MemoryQuery, PartialWriteError, Scope  # noqa: E402
from tests.unit.memory.test_provider_seed_bridge import (  # noqa: E402
    FakeMachine,
    FakeManager,
    FakeStore,
    raises_from_process,
)

ACME = Scope(tenant_id="acme", user_id="u1")


def seed_config(**options) -> MemoryProviderConfig:
    return MemoryProviderConfig(
        provider="seed", options={"single_tenant": True, "tenant_id": ACME.tenant_id, **options}
    )


# ---------------------------------------------------------------------------
# Fixtures -- duplicated from test_memory_tracing.py rather than imported, on
# purpose: that file's own docstring frames its hygiene fixtures as file-local
# so no test file inherits ambient tracing state from, or leaks it into,
# another. See that file for the reasoning behind each one.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_tracing_env(monkeypatch):
    for prefix in ("", "EGPT_", "AGNO_"):
        for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_ENABLED"):
            monkeypatch.delenv(f"{prefix}{name}", raising=False)
    monkeypatch.delenv("EGPT_TRACING_DB_ENABLED", raising=False)


@pytest.fixture(autouse=True)
def _reset_global_tracer(monkeypatch):
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", trace_api._TRACER_PROVIDER_SET_ONCE.__class__())
    yield
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", trace_api._TRACER_PROVIDER_SET_ONCE.__class__())


@pytest.fixture
def span_exporter():
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace_api.set_tracer_provider(tracer_provider)
    return exporter


def spans_named(exporter: InMemorySpanExporter, name: str):
    return [span for span in exporter.get_finished_spans() if span.name == name]


def assert_common_attributes(attrs) -> None:
    """Same attributes `test_memory_tracing.py` asserts, minus `store`: the seed has no
    substrates (see `substrates()`), so `memory_span` is passed `store=None` and omits it."""
    assert attrs["egpt.memory.tenant_id"] == "acme"
    # See test_memory_tracing.py's assert_common_attributes -- create_trace_from_spans reads this
    # bare key off the trace's root span, with no `.memory.` fallback.
    assert attrs["egpt.tenant_id"] == "acme"
    assert isinstance(attrs["egpt.memory.tier"], str) and attrs["egpt.memory.tier"]
    assert "egpt.memory.store" not in attrs
    assert isinstance(attrs["egpt.memory.latency_ms"], float)
    assert attrs["egpt.memory.latency_ms"] >= 0.0


# ---------------------------------------------------------------------------
# write / awrite -> memory.write
# ---------------------------------------------------------------------------


def test_write_produces_a_memory_write_span(span_exporter):
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())
    events = [MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC)]

    receipt = bridge.write(ACME, events)

    assert receipt.accepted == 1
    spans = spans_named(span_exporter, "memory.write")
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert_common_attributes(attrs)
    assert attrs["egpt.memory.tier"] == "semantic"


async def test_awrite_produces_a_memory_write_span(span_exporter):
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())
    events = [MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC)]

    receipt = await bridge.awrite(ACME, events)

    assert receipt.accepted == 1
    assert len(spans_named(span_exporter, "memory.write")) == 1
    assert spans_named(span_exporter, "memory.awrite") == []


# ---------------------------------------------------------------------------
# read / aread -> memory.read
# ---------------------------------------------------------------------------


def test_read_produces_a_memory_read_span(span_exporter):
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    slice_ = bridge.read(ACME, MemoryQuery(text="q"))

    assert slice_.scope == ACME
    spans = spans_named(span_exporter, "memory.read")
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert_common_attributes(attrs)
    assert attrs["egpt.memory.tier"] == "all"


async def test_aread_produces_a_memory_read_span(span_exporter):
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    slice_ = await bridge.aread(ACME, MemoryQuery(text="q"))

    assert slice_.scope == ACME
    assert len(spans_named(span_exporter, "memory.read")) == 1
    assert spans_named(span_exporter, "memory.aread") == []


# ---------------------------------------------------------------------------
# consolidate / aconsolidate -> memory.consolidate
# ---------------------------------------------------------------------------


def test_consolidate_produces_a_memory_consolidate_span(span_exporter):
    machine = FakeMachine()
    bridge = SeedMemoryProvider(manager=FakeManager(), machine=machine, config=seed_config())

    report = bridge.consolidate(ACME)

    assert report.scope == ACME
    spans = spans_named(span_exporter, "memory.consolidate")
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert_common_attributes(attrs)
    # The seed runs only the curator's dedup lane -- not co_located's episodic->semantic.
    assert attrs["egpt.memory.tier"] == "semantic"


async def test_aconsolidate_produces_a_memory_consolidate_span(span_exporter):
    """`aconsolidate` is not separately wrapped (see its docstring) -- it gets a span through
    delegating to the already-traced sync `consolidate` via `asyncio.to_thread`, which propagates
    OTel context into the worker thread."""
    machine = FakeMachine()
    bridge = SeedMemoryProvider(manager=FakeManager(), machine=machine, config=seed_config())

    report = await bridge.aconsolidate(ACME)

    assert report.scope == ACME
    assert len(spans_named(span_exporter, "memory.consolidate")) == 1
    assert spans_named(span_exporter, "memory.aconsolidate") == []


# ---------------------------------------------------------------------------
# Regression: a raising tracer/processor, or a raising wrapped call, must
# each fail independently -- same two cases test_memory_tracing.py pins for
# CoLocatedMemoryProvider.
# ---------------------------------------------------------------------------


class _ExplodingOnStartProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None):
        raise RuntimeError("on_start exploded")

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def test_write_still_completes_when_a_span_processor_explodes_on_start(span_exporter):
    trace_api.get_tracer_provider().add_span_processor(_ExplodingOnStartProcessor())
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())
    events = [MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC)]

    receipt = bridge.write(ACME, events)

    assert receipt.accepted == 1
    assert spans_named(span_exporter, "memory.write") == []


def test_a_raised_exception_from_the_wrapped_call_still_propagates_and_closes_the_span(span_exporter):
    store = FakeStore(learning_type="user_profile")
    store.process = raises_from_process(RuntimeError("store down"))
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    with pytest.raises(PartialWriteError):
        bridge.write(ACME, [MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC)])

    spans = spans_named(span_exporter, "memory.write")
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in spans[0].events)
