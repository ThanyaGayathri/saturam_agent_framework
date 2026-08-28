"""``Model._resolved_provider_key`` memoization (SM-X8 follow-up, PR #159 review finding #6).

``BaseModelProvider._registry_key_for`` does an O(N) scan over the model registry
(``egpt.registry.discovery``). Before this, it ran twice for the same instance on the same
extraction call: once inside ``Model._apply_cost_to_metrics`` (run unconditionally on every
``.response()``/``.aresponse()``, via ``_finalize_call_accounting``), and again right after in
``egpt.memory.metrics.meter_extraction_cost``, called on that identical ``model`` object by every
SM-X8 extraction call site (``LLMEntityExtractor.extract`` and its four siblings). These pin that
the second lookup now reuses the first's cached answer instead of re-scanning.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

from egpt.models.base import _UNRESOLVED_PROVIDER_KEY, Model
from egpt.providers.model_provider import BaseModelProvider


class _MinimalModel(Model):
    """A ``Model`` with nothing wired -- only the identity/caching question matters here, never a
    real ``.response()`` call."""

    def invoke(self, *args: Any, **kwargs: Any):  # pragma: no cover - never called
        raise AssertionError("not invoked")

    async def ainvoke(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    def invoke_stream(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    def _parse_provider_response(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    def _parse_provider_response_delta(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")


def test_resolved_provider_key_is_memoized_per_instance():
    model = _MinimalModel(id="x")

    with patch.object(BaseModelProvider, "_registry_key_for", wraps=BaseModelProvider._registry_key_for) as spy:
        first = model._resolved_provider_key()
        second = model._resolved_provider_key()

    assert first == second
    spy.assert_called_once_with(model)


def test_two_instances_do_not_share_a_cache():
    """Memoization is per-instance, not a class-level or global cache -- two different models must
    not answer with each other's resolved key."""
    a, b = _MinimalModel(id="a"), _MinimalModel(id="b")

    with patch.object(BaseModelProvider, "_registry_key_for", wraps=BaseModelProvider._registry_key_for) as spy:
        a._resolved_provider_key()
        b._resolved_provider_key()

    assert spy.call_count == 2


def test_apply_cost_to_metrics_and_meter_extraction_cost_share_one_resolution():
    """The exact duplication review finding #6 flagged: ``_apply_cost_to_metrics`` and
    ``meter_extraction_cost`` each independently called ``_registry_key_for`` on the same model
    for the same call. ``meter_extraction_cost`` must now reuse the cache ``_apply_cost_to_metrics``
    already populated instead of re-resolving."""
    from egpt.memory.embedding import MemoryMetricsAggregator
    from egpt.memory.metrics import meter_extraction_cost
    from egpt.memory.types import Scope

    model = _MinimalModel(id="x")

    class _Metrics:
        cost = None
        input_tokens = 10
        output_tokens = 5
        cache_read_tokens = 0
        reasoning_tokens = 0

    class _Usage:
        input_tokens = 10
        output_tokens = 5
        cache_read_tokens = 0
        reasoning_tokens = 0

    with patch.object(BaseModelProvider, "_registry_key_for", wraps=BaseModelProvider._registry_key_for) as spy:
        model._apply_cost_to_metrics(_Metrics())
        meter_extraction_cost(
            MemoryMetricsAggregator(),
            scope=Scope(tenant_id="acme"),
            model=model,
            response_usage=_Usage(),
        )

    spy.assert_called_once_with(model)


def test_meter_extraction_cost_still_works_against_a_plain_double_without_the_cache():
    """A bare test double (``.id`` only, no ``_resolved_provider_key``) must still resolve a
    provider key -- ``meter_extraction_cost``'s existing ``test_memory_metrics.py`` tests use
    exactly this shape, and the cache is an optimization, not a new requirement on callers."""
    from egpt.memory.embedding import MemoryMetricsAggregator
    from egpt.memory.metrics import meter_extraction_cost
    from egpt.memory.types import Scope

    class _PlainDouble:
        id = "fake-chat-1"

    class _Usage:
        input_tokens = 10
        output_tokens = 5
        cache_read_tokens = 0
        reasoning_tokens = 0

    agg = MemoryMetricsAggregator()
    meter_extraction_cost(agg, scope=Scope(tenant_id="acme"), model=_PlainDouble(), response_usage=_Usage())

    assert len(agg.costs) == 1
    assert agg.costs[0].tenant_id == "acme"


# ---------------------------------------------------------------------------
# Deepcopy contract (Minor finding, PR #159 round-2 review, models/base.py:204): the cache field
# is `field(default=..., init=False)` with no `default_factory`, so it is a class-level default,
# not an instance attribute, until the first `_resolved_provider_key()` call. `Model.__deepcopy__`
# only iterates `self.__dict__`, so an unresolved copy must start unresolved too (falling through
# to the same class default) rather than accidentally inheriting some other instance's state, and
# a resolved copy must carry its own already-resolved value across.
# ---------------------------------------------------------------------------


def test_deepcopy_of_an_unresolved_model_stays_unresolved():
    model = _MinimalModel(id="x")
    assert "_provider_key_cache" not in model.__dict__  # never resolved -- not in __dict__ yet

    copy = deepcopy(model)

    assert "_provider_key_cache" not in copy.__dict__
    assert copy._provider_key_cache is _UNRESOLVED_PROVIDER_KEY


def test_deepcopy_of_a_resolved_model_carries_the_resolved_value():
    model = _MinimalModel(id="x")
    resolved = model._resolved_provider_key()
    assert "_provider_key_cache" in model.__dict__  # now a real instance attribute

    copy = deepcopy(model)

    assert copy._provider_key_cache == resolved
    with patch.object(BaseModelProvider, "_registry_key_for", wraps=BaseModelProvider._registry_key_for) as spy:
        assert copy._resolved_provider_key() == resolved
    spy.assert_not_called()  # the copy reused the deep-copied cache, it did not re-resolve
