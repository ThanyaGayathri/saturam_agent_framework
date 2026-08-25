"""
Observability test fixtures.

Provides an InMemorySpanExporter wired into a fresh TracerProvider so that
tests can assert on emitted spans without any external network or database.

The EGPT package is published as ``egpt`` but the third-party instrumentor
(openinference-instrumentation-agno) imports from ``agno.*``.  This is a
test-only workaround that lets the instrumentor's hardcoded ``agno.*``
imports resolve against this framework's ``egpt.*`` package.  Production code
(``tracing/setup.py``) passes ``skip_dep_check=True`` to bypass the same
incompatibility but does not need module aliasing because it never imports
from the instrumented paths directly.  Note: ``find_model_subclasses()``
(called inside ``instrument()``) walks all ``agno.models.*`` subpackages and
wraps 35-46 real Model subclasses in production; here we only wrap StubModel
and StubModelWithToolCall manually.

Coverage gaps (documented, follow-up work):
- ``find_model_subclasses()`` auto-discovery is not exercised: StubModel is
  defined here (not under an ``agno.models.*``-resolvable path), so we
  manually wrap it.  A future follow-up could define the stub inside a
  package discoverable by ``pkgutil.walk_packages`` to cover that path.
- Only ``invoke``/``invoke_stream`` are wrapped; ``ainvoke``/``ainvoke_stream``
  are not (no async tests yet).  A follow-up should add async coverage.
"""

from __future__ import annotations

import sys
from typing import Generator

import pytest

pytest.importorskip("openinference", reason="observability tests require openinference-instrumentation-agno")
pytest.importorskip("opentelemetry.sdk", reason="observability tests require opentelemetry-sdk")

# ---------------------------------------------------------------------------
# sys.modules aliasing for agno.* -> egpt.*
# ---------------------------------------------------------------------------
# Must happen at module level (before the openinference imports below attempt
# to resolve ``agno.*``).  Tracked in _AGNO_ALIASES for teardown in the
# session-scoped fixture.

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
import egpt.workflow  # noqa: E402
import egpt.workflow.workflow  # noqa: E402
import egpt.workflow.step  # noqa: E402

_AGNO_ALIASES: list[str] = []

sys.modules["agno"] = egpt
_AGNO_ALIASES.append("agno")

for _mod_name in list(sys.modules.keys()):
    if _mod_name.startswith("egpt."):
        _alias = "agno." + _mod_name[5:]
        if _alias not in sys.modules:
            sys.modules[_alias] = sys.modules[_mod_name]
            _AGNO_ALIASES.append(_alias)

# ---------------------------------------------------------------------------
# StubModel definition
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


class StubModelWithToolCall(StubModel):
    """A stub model that requests a tool call on the first invocation, then returns content.

    Defines its own invoke() so it must also be wrapt-wrapped in the otel_setup
    fixture to emit LLM spans (Python MRO means the wrapper on StubModel.invoke
    is never reached for instances of this subclass).
    """

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0

    def invoke(self, messages=None, **kwargs):  # type: ignore[override]
        self._call_count += 1
        if self._call_count == 1:
            # First call: request a tool call
            return ModelResponse(
                content=None,
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_001",
                        "type": "function",
                        "function": {
                            "name": "add",
                            "arguments": '{"a": 2, "b": 3}',
                        },
                    }
                ],
                response_usage=MessageMetrics(
                    input_tokens=15,
                    output_tokens=8,
                    total_tokens=23,
                ),
            )
        else:
            # Second call: return final response after tool result
            return ModelResponse(
                content="The sum of 2 and 3 is 5.",
                role="assistant",
                response_usage=MessageMetrics(
                    input_tokens=20,
                    output_tokens=10,
                    total_tokens=30,
                ),
            )


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


@pytest.fixture(autouse=True, scope="session")
def _cleanup_agno_aliases():
    """Remove agno.* sys.modules aliases after the test session.

    The aliases are registered at module level (required for import resolution)
    but cleaned up here to avoid polluting other tests sharing the same process.
    """
    yield
    for alias in _AGNO_ALIASES:
        sys.modules.pop(alias, None)


@pytest.fixture
def otel_setup() -> Generator[tuple[InMemorySpanExporter, TracerProvider], None, None]:
    """
    Provide a fresh TracerProvider + InMemorySpanExporter, with the
    AgnoInstrumentor wired in and the StubModel manually instrumented.

    Yields (exporter, provider).

    Note: StubModel is manually wrapped because find_model_subclasses()
    only discovers classes under agno.models.* subpackages via
    pkgutil.walk_packages.  This means the auto-discovery path is NOT
    exercised by these tests — see module docstring for the coverage gap.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Wire the AgnoInstrumentor (wraps agent._run, team._run, FunctionCall.execute)
    instrumentor = AgnoInstrumentor()
    instrumentor.instrument(tracer_provider=provider, skip_dep_check=True)

    # Manually wrap StubModel.invoke/invoke_stream since find_model_subclasses()
    # cannot discover classes defined outside agno.models.* subpackages.
    # TODO: Also wrap ainvoke/ainvoke_stream and add async test coverage.
    oi_tracer = OITracer(
        trace_api.get_tracer("egpt-observability-tests", tracer_provider=provider),
        config=TraceConfig(),
    )
    model_wrapper = _ModelWrapper(tracer=oi_tracer)

    # Save originals before wrapping (for teardown restoration).
    _original_stub_invoke = StubModel.invoke
    _original_stub_invoke_stream = StubModel.invoke_stream
    _original_tool_invoke = StubModelWithToolCall.invoke

    wrap_function_wrapper(StubModel, "invoke", model_wrapper.run)
    wrap_function_wrapper(StubModel, "invoke_stream", model_wrapper.run_stream)
    wrap_function_wrapper(StubModelWithToolCall, "invoke", model_wrapper.run)

    yield exporter, provider

    # Cleanup: restore original methods to avoid nesting wrappers across tests.
    try:
        provider.shutdown()
        instrumentor.uninstrument()
    finally:
        StubModel.invoke = _original_stub_invoke  # type: ignore[method-assign]
        StubModel.invoke_stream = _original_stub_invoke_stream  # type: ignore[method-assign]
        StubModelWithToolCall.invoke = _original_tool_invoke  # type: ignore[method-assign]


@pytest.fixture
def stub_model() -> StubModel:
    """A stub model that returns deterministic responses with token metrics."""
    return StubModel()
