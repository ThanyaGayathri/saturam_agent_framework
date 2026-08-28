"""Memory cost and store-growth metering, per tenant (SM-X8, aicoe-tracker#144).

Memory has real, uncontrolled cost: every write/read embeds text (an embedding API call), and
consolidation runs extraction/reflection/summarization LLM calls. None of it was metered or
attributed per tenant before this module.

**A separate structure, not a graft onto ``egpt.metrics``.** ``egpt/metrics.py``'s
``RunMetrics``/``SessionMetrics`` roll-up hierarchy is tied to a live in-flight agent run; a
consolidation pass runs on a background/sleep-time lane with no ``RunMetrics`` in scope, so
forcing this data into that hierarchy would mean inventing a run for a call that has none.
Instead this is a plain, purpose-built structure: dataclasses plus free functions, no framework
magic, and no dependency on ``egpt.tracing`` (that is a separate, unrelated ticket -- SM-X4 --
about OTel spans, not cost).

**The dataclasses, the aggregator, and embedding-cost metering live in ``egpt.memory.embedding``,
not here** -- that module is on the type island (see ``test_memory_layout.py``), importable from
an integrations-layer substrate backend, and the embedding API call being metered happens there.
This module re-exports them (rather than redefining them, which would split every call site onto
two disconnected aggregator instances) and adds only what the island guard forbids there:
``meter_extraction_cost`` needs ``egpt.providers.model_provider.BaseModelProvider``, and
``record_growth`` is the plain services-layer counterpart for `write`/`consolidate` growth. Every
call site -- embedding, extraction, and store-growth -- shares the one aggregator instance
``egpt.memory.embedding.get_default_aggregator()`` returns, regardless of which module recorded
onto it.

**Every metering function is best-effort and never raises.** A missing/malformed usage payload or
an unpriced model records a ``cost=None`` entry ("unknown"), never a coerced ``0.0`` (a wrong
number is worse than a missing one -- see ``egpt/models/pricing.py``'s own module docstring) and
never an exception that could break the write/embed/extract call it is riding along with.

**A data holder, not a sink.** ``MemoryMetricsAggregator`` only accumulates entries in-process;
shipping them to a database table, Prometheus, or a log line is the metrics pipeline's job (the
"EGPT-4.5" ticket the issue mentions), not this module's -- as of this writing that pipeline has
not landed anywhere in the tree, so there is nothing to plug into yet. ``get_default_aggregator``
gives every call site in this package one shared instance to record onto in the meantime.
"""

from __future__ import annotations

from typing import Any, Tuple

from egpt.memory.embedding import (
    MemoryCostEntry,
    MemoryGrowthEntry,
    MemoryMetricsAggregator,
    embedder_provider_key,
    get_default_aggregator,
    meter_embedding_cost,
    reset_default_aggregator,
)
from egpt.memory.types import Scope
from egpt.utils.log import log_debug

__all__ = [
    "MemoryCostEntry",
    "MemoryGrowthEntry",
    "MemoryMetricsAggregator",
    "get_default_aggregator",
    "reset_default_aggregator",
    "embedder_provider_key",
    "meter_embedding_cost",
    "meter_extraction_cost",
    "record_growth",
]


def meter_extraction_cost(
    aggregator: MemoryMetricsAggregator,
    *,
    scope: Scope,
    model: Any,
    response_usage: Any,
) -> None:
    """Compute + record cost for one extraction/reflection/summarization LLM call.

    ``response_usage`` is ``ModelResponse.response_usage`` from a ``model.response(...)``/
    ``.aresponse(...)`` call made without a ``run_response=`` kwarg -- which is every call site
    this is used from (entity/relation extraction, reflection, session summarization). Those calls
    have real token counts on ``response_usage`` but a ``None`` ``.cost``: ``Model.
    _apply_cost_to_metrics`` only ever populates cost onto an *internal* ``assistant_message.
    metrics`` object, never copying it back onto ``response_usage`` unless ``run_response=`` was
    passed. So this computes cost itself from the token counts that are present, using the same
    provider-key derivation ``Model._apply_cost_to_metrics`` uses
    (``BaseModelProvider._registry_key_for``), rather than inventing a second one.

    Never raises: an unresolvable provider key, an unpriced model, or a ``None`` ``response_usage``
    records a ``cost=None`` entry rather than breaking the extraction call.
    """
    try:
        from egpt.models.pricing import compute_cost
        from egpt.providers.model_provider import BaseModelProvider

        input_tokens = int(getattr(response_usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(response_usage, "output_tokens", 0) or 0)
        cache_read_tokens = int(getattr(response_usage, "cache_read_tokens", 0) or 0)
        reasoning_tokens = int(getattr(response_usage, "reasoning_tokens", 0) or 0)

        try:
            # `model` is a real `Model` instance at every call site in this tree -- its
            # `_resolved_provider_key` memoizes this same lookup, which `Model.response`/
            # `.aresponse` (just called, by every caller of this function) already ran once via
            # `_apply_cost_to_metrics`. Falling back to a direct, unmemoized lookup keeps this
            # working against a plain test double that only has `.id`, not the full `Model` shape.
            # `hasattr`, not `isinstance(model, Model)`: `unittest.mock.MagicMock(spec=Model)`
            # deliberately makes `isinstance` checks against `Model` pass (it patches `__class__`
            # for exactly that reason), so an `isinstance` guard would not have caught it either --
            # it would still call the mock's auto-specced `_resolved_provider_key` and get an
            # unconfigured `MagicMock` back. Validating the *result*'s type below is what actually
            # guards against that, for any test double shape, not just this one.
            if hasattr(model, "_resolved_provider_key"):
                provider_key = model._resolved_provider_key()
            else:
                provider_key = BaseModelProvider._registry_key_for(model)
            if provider_key is not None and not isinstance(provider_key, str):
                # A double whose `_resolved_provider_key` isn't wired to return a real value (a
                # bare `MagicMock(spec=Model)`, or any other test double built the same way)
                # returns something here that is neither a real registry key nor `None`. Treated
                # as unresolved rather than handed to `compute_cost`, which would either raise or
                # silently never match -- the same "unknown, not a coerced value" stance the rest
                # of this function takes for cost.
                provider_key = None
        except Exception as exc:
            log_debug(f"Memory extraction cost: could not resolve a provider key for {model!r}: {exc}")
            provider_key = None
        model_id = getattr(model, "id", None)

        try:
            cost = compute_cost(
                provider_key=provider_key,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                reasoning_tokens=reasoning_tokens,
            )
        except Exception as exc:
            log_debug(f"Memory extraction cost computation unavailable for this call: {exc}")
            cost = None

        aggregator.record_cost(
            MemoryCostEntry(
                tenant_id=scope.tenant_id,
                agent_id=getattr(scope, "agent_id", None),
                kind="extraction",
                provider_key=provider_key,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
            )
        )
    except Exception as exc:
        log_debug(f"Memory extraction cost metering skipped: {exc}")


def record_growth(
    aggregator: MemoryMetricsAggregator,
    scope: Scope,
    operation: str,
    *,
    tier: str,
    accepted: int,
    event_ids: Tuple[str, ...] = (),
) -> None:
    """Record one write/consolidate call's contribution to store size.

    ``event_ids`` are the ids this call actually stamped -- see :class:`MemoryGrowthEntry` for why
    a downstream consumer needs them (collapsing a retried, deterministically-re-ided batch that
    would otherwise be double-counted).

    Never raises: a bad ``scope`` (missing ``tenant_id``) is the one case this cannot recover
    from gracefully, but every caller in this package already holds a validated ``Scope`` by the
    time it reaches its own ``return`` statement, so this is not guarded the way the cost meters
    are -- there is no meaningful "unknown tenant" growth entry to fall back to.
    """
    aggregator.record_growth(
        MemoryGrowthEntry(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            operation=operation,
            tier=tier,
            accepted=accepted,
            event_ids=event_ids,
        )
    )
