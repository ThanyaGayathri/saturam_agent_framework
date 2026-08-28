"""
SM-X4: Manual OTel spans for memory read/write/consolidate.

Agent, model and tool calls are auto-instrumented via ``AgnoInstrumentor`` (wired in
``tracing/setup.py``), but the ``MemoryProvider`` seam -- ``read``/``write``/``consolidate`` -- is
outside that instrumentor's reach: it has nothing to do with an agent run and no span is ever
created around it. :func:`memory_span` is how a memory call gets one.

Mirrors ``agent/_run.py``'s ``_set_tenant_on_span``: every OTel call is wrapped in a bare
``except Exception`` that logs and falls through, because a tracing bug must never break the
memory call it wraps. The difference from that helper is that this one *creates* the span (there
is no AgnoInstrumentor-authored span to enrich here), not merely enriches an existing one.

Latency note: this module's default span pipeline (``setup_tracing(batch_processing=False)``, the
default) runs a ``SimpleSpanProcessor``, which does a blocking DB write inline on every
``span.end()`` -- including every ``memory.read``/``memory.write``/``memory.consolidate`` span
this module opens. That was already true for every ``AgnoInstrumentor``-authored span; SM-X3
wiring memory calls into the live agent/team run loop is what puts it on a materially hotter path.
Pass ``setup_tracing(batch_processing=True)``, or supply an ``AsyncBaseDb``, before enabling
memory tracing on a latency-sensitive async path.
"""

import sys
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from egpt.utils.log import log_debug

__all__ = ["memory_span"]


@contextmanager
def memory_span(
    operation: str,
    *,
    tenant_id: str,
    tier: Optional[str],
    store: Optional[str],
) -> Iterator[None]:
    """Create and enrich one OTel span for a memory read/write/consolidate call.

    Named ``memory.<operation>`` -- ``operation`` is the seam verb (``"read"``, ``"write"``,
    ``"consolidate"``), shared between a sync method and its async twin: ``aread`` and ``read``
    both produce ``memory.read`` rather than ``memory.aread``, so a caller does not have to know
    which half ran to find the span.

    Sets ``egpt.memory.tenant_id``, ``egpt.memory.tier`` (when given), ``egpt.memory.store`` (when
    given), and -- once the wrapped call returns or raises -- ``egpt.memory.latency_ms``.

    Every OTel call here is wrapped in a bare ``except Exception`` that logs and falls through, the
    same idiom ``agent/_run.py``'s ``_set_tenant_on_span`` uses: tracing must never be why a memory
    call fails, and must never swallow an exception the wrapped call raises either -- the body
    always runs via ``yield``, tracing or no.

    The span's lifecycle is driven explicitly -- ``span_cm.__enter__()``/``__exit__()`` -- rather
    than through a ``with`` statement, so both halves stay inside their own guarded ``try``.
    ``__enter__`` is what actually runs the sampler, mints an id, and calls every registered
    ``SpanProcessor.on_start`` -- ``start_as_current_span(...)`` on its own only builds the
    context-manager object and does none of that -- so leaving ``__enter__`` for a bare ``with`` to
    invoke put that work outside this function's guarantee: a raising sampler/processor/id
    generator broke the memory call itself, not just the span. ``__exit__`` gets the same
    treatment, called with ``sys.exc_info()`` so a real exception from the wrapped call is still
    recorded on the span exactly as ``with`` would have done, and still propagates -- a ``finally``
    block never swallows the exception being propagated through it, and ``sys.exc_info()``
    correctly reports that exception (or ``(None, None, None)`` on the success path) from inside a
    ``finally``.
    """
    try:
        from opentelemetry import trace as trace_api

        tracer = trace_api.get_tracer(__name__)
        span_cm = tracer.start_as_current_span(f"memory.{operation}")
        start = time.perf_counter()
        span = span_cm.__enter__()
    except Exception:
        log_debug("Failed to start memory span (tracing may not be configured)")
        yield
        return

    try:
        if span.is_recording():
            span.set_attribute("egpt.memory.tenant_id", tenant_id)
            if tier:
                span.set_attribute("egpt.memory.tier", tier)
            if store:
                span.set_attribute("egpt.memory.store", store)
    except Exception:
        log_debug("Failed to set memory span attributes")

    try:
        yield
    finally:
        try:
            if span.is_recording():
                span.set_attribute("egpt.memory.latency_ms", round((time.perf_counter() - start) * 1000, 3))
        except Exception:
            log_debug("Failed to set memory span latency")
        try:
            span_cm.__exit__(*sys.exc_info())
        except Exception:
            log_debug("Failed to close memory span")
