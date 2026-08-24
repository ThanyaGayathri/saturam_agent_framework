"""
Observability test fixtures.

Provides an InMemorySpanExporter wired into a fresh TracerProvider so that
tests can assert on emitted spans without any external network or database.

The EGPT package is published as ``egpt`` but the third-party instrumentor
(openinference-instrumentation-agno) imports from ``agno.*``.  We register
sys.modules aliases so the instrumentor resolves the same objects as the
framework.  This is the same mechanism that the framework's own tracing
setup.py uses in production.
"""

from __future__ import annotations

import sys
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Step 1: Register egpt.* as agno.* in sys.modules BEFORE any OTel import.
# This allows the AgnoInstrumentor to resolve `agno.agent._run`, etc.
# ---------------------------------------------------------------------------

import egpt  # noqa: E402
import egpt.agent  # noqa: E402
import egpt.agent._run  # noqa: E402
import egpt.agent.agent  # noqa: E402
import egpt.team  # noqa: E402
import egpt.team._run  # noqa: E402
import egpt.tools  # noqa: E402
import egpt.tools.function  # noqa: E402
import egpt.models  # noqa: E402
import egpt.models.base  # noqa: E402
import egpt.models.message  # noqa: E402
import egpt.run  # noqa: E402
import egpt.run.agent  # noqa: E402
import egpt.run.team  # noqa: E402
import egpt.run.messages  # noqa: E402

sys.modules["agno"] = egpt
for _mod_name in list(sys.modules.keys()):
    if _mod_name.startswith("egpt."):
        _alias = "agno." + _mod_name[5:]
        if _alias not in sys.modules:
            sys.modules[_alias] = sys.modules[_mod_name]

# ---------------------------------------------------------------------------
# Step 2: Define the StubModel (must exist before instrumentation so we can
# manually wrap it like the instrumentor wraps discovered model subclasses).
# ---------------------------------------------------------------------------

from egpt.metrics import MessageMetrics  # noqa: E402
from egpt.models.base import Model  # noqa: E402
from egpt.models.response import ModelResponse  # noqa: E402


class StubModel(Model):
    """A minimal Model subclass that returns canned responses with usage metrics.

    Defined at module scope so tests get a consistent, known model.
    """

    def __init__(self) -> None:
        super().__init__(id="stub-1", name="StubModel", provider="stub")

    def invoke(self, messages=None, **kwargs):  # type: ignore[override]
        return ModelResponse(
            content="Hello from stub model",
            role="assistant",
            response_usage=MessageMetrics(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cost=0.0001,
            ),
        )

    def invoke_stream(self, messages=None, **kwargs):  # type: ignore[override]
        yield ModelResponse(
            content="Hello from stub model",
            role="assistant",
            response_usage=MessageMetrics(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cost=0.0001,
            ),
        )

    async def ainvoke(self, messages=None, **kwargs):  # type: ignore[override]
        return self.invoke(messages=messages, **kwargs)

    async def ainvoke_stream(self, messages=None, **kwargs):  # type: ignore[override]
        yield self.invoke(messages=messages, **kwargs)

    def _parse_provider_response(self, response):  # type: ignore[override]
        return response

    def _parse_provider_response_delta(self, response):  # type: ignore[override]
        return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

from openinference.instrumentation import OITracer, TraceConfig  # noqa: E402
from openinference.instrumentation.agno import AgnoInstrumentor  # noqa: E402
from openinference.instrumentation.agno._model_wrapper import _ModelWrapper  # noqa: E402
from opentelemetry import trace as trace_api  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from wrapt import wrap_function_wrapper  # noqa: E402


@pytest.fixture
def otel_setup() -> Generator[tuple[InMemorySpanExporter, TracerProvider], None, None]:
    """
    Provide a fresh TracerProvider + InMemorySpanExporter, with the
    AgnoInstrumentor wired in and the StubModel manually instrumented.

    Yields (exporter, provider).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Wire the AgnoInstrumentor (wraps agent._run, team._run, FunctionCall.execute)
    instrumentor = AgnoInstrumentor()
    instrumentor.instrument(tracer_provider=provider, skip_dep_check=True)

    # Manually wrap StubModel.invoke/invoke_stream since find_model_subclasses()
    # cannot discover classes defined outside agno.models.* subpackages.
    oi_tracer = OITracer(
        trace_api.get_tracer("egpt-observability-tests", tracer_provider=provider),
        config=TraceConfig(),
    )
    model_wrapper = _ModelWrapper(tracer=oi_tracer)
    wrap_function_wrapper(StubModel, "invoke", model_wrapper.run)
    wrap_function_wrapper(StubModel, "invoke_stream", model_wrapper.run_stream)

    yield exporter, provider

    # Cleanup
    provider.shutdown()
    try:
        instrumentor.uninstrument()
    except Exception:
        pass


@pytest.fixture
def stub_model() -> StubModel:
    """A stub model that returns deterministic responses with token metrics."""
    return StubModel()
