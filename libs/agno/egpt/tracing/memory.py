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
"""

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
    """
    try:
        from opentelemetry import trace as trace_api

        tracer = trace_api.get_tracer(__name__)
        span_cm = tracer.start_as_current_span(f"memory.{operation}")
    except Exception:
        log_debug("Failed to start memory span (tracing may not be configured)")
        yield
        return

    start = time.perf_counter()
    with span_cm as span:
        try:
            if span.is_recording():
                span.set_attribute("egpt.memory.tenant_id", tenant_id)
                if tier is not None:
                    span.set_attribute("egpt.memory.tier", tier)
                if store is not None:
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
