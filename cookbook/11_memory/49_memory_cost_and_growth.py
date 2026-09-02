"""
Memory Cost and Store-Growth Metering
======================================

SM-X8 (aicoe-tracker#144): memory has real, uncontrolled cost -- every write/read embeds text
(an embedding API call), and consolidation runs extraction/reflection/summarization LLM calls --
and none of it was metered or attributed per tenant before this. This example drives the three
real production hooks that meter it, rather than calling into the aggregator API directly:

- `MemoryEmbedder.embed_texts(texts, scope=...)` (`egpt.memory.embedding`) meters an embedding
  call when a `scope` is passed.
- `LLMEntityExtractor.extract(episode)` (`egpt.memory.graph.entities`) meters the extraction LLM
  call it makes -- the same hook `LLMRelationExtractor`, `LLMInsightSynthesizer`, and
  `LLMSessionSummarizer` each carry for their own call.
- `CoLocatedMemoryProvider.write`/`consolidate` (`egpt.memory.adapters.co_located`) record store
  growth from `WriteReceipt.accepted` / the count of semantic facts a consolidation pass wrote.

Key concepts:
- Every entry lands on one shared `MemoryMetricsAggregator`
  (`egpt.memory.embedding.get_default_aggregator()`), regardless of which module recorded it:
  embedding cost is metered in `egpt.memory.embedding` (the type island substrate backends may
  import), extraction cost and store growth in `egpt.memory.metrics` (services layer, free to
  import the model registry) -- see both modules' docstrings for why the split.
- An unpriced model/embedder records `cost=None` ("unknown"), never a coerced `0.0`.
  `cost_by_tenant()` skips those entries rather than counting them as free -- summing unknowns as
  zero would make a tenant using an unpriced model look cheaper than one who made no calls at all.
- "Store growth" is a per-call delta (`WriteReceipt.accepted`, the count of facts one
  `consolidate` pass wrote), not a queried absolute -- no substrate in this tree exposes a row
  count.

No external services: a demo embedder and a demo extraction model, registered at runtime the same
way `47_per_tenant_memory_config.py` registers its demo provider, so `compute_cost` has a
synthetic price row to find for them. Neither makes a network call. The vector substrate is the
same trimmed in-memory stand-in `23_consolidation_worker.py` uses.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from egpt.knowledge.embedder import Embedder
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
from egpt.memory.embedding import (
    MemoryEmbedder,
    embedder_provider_key,
    get_default_aggregator,
)
from egpt.memory.graph.entities import (
    ExtractedEntities,
    ExtractedEntity,
    LLMEntityExtractor,
)
from egpt.models.pricing import PRICES, ModelPrice
from egpt.providers.model_provider import BaseModelProvider
from egpt.registry import discovery

ACME = Scope(tenant_id="acme", agent_id="research-agent")
GLOBEX = Scope(tenant_id="globex", agent_id="support-agent")


# ---------------------------------------------------------------------------
# A demo embedder and a demo extraction model, neither reaching the network.
# Registered at runtime the same way `47_per_tenant_memory_config.py`'s
# `@register_provider("cookbook_demo")` registers its demo provider, so
# `compute_cost` has a synthetic price row to find for them.
# ---------------------------------------------------------------------------


class DemoEmbedder(Embedder):
    """Word-length buckets standing in for real embedding similarity -- deterministic, no network."""

    id = "cookbook-demo-embedder"

    def _vector(self, text: str) -> List[float]:
        buckets = [0.0, 0.0, 0.0, 0.0]
        for word in text.lower().split():
            buckets[len(word) % 4] += 1.0
        return buckets

    def get_embedding(self, text: str) -> List[float]:
        return self._vector(text)

    def get_embedding_and_usage(
        self, text: str
    ) -> Tuple[List[float], Optional[Dict[str, Any]]]:
        tokens = max(1, len(text.split()))
        return self._vector(text), {"prompt_tokens": tokens, "total_tokens": tokens}

    async def async_get_embedding(self, text: str) -> List[float]:
        return self.get_embedding(text)

    async def async_get_embedding_and_usage(
        self, text: str
    ) -> Tuple[List[float], Optional[Dict[str, Any]]]:
        return self.get_embedding_and_usage(text)


class _DemoUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = 0
        self.reasoning_tokens = 0


class _DemoResponse:
    def __init__(self, parsed: ExtractedEntities, response_usage: _DemoUsage) -> None:
        self.parsed = parsed
        self.content = None
        self.response_usage = response_usage


class DemoExtractionModel:
    """A canned entity-extraction answer, standing in for a real model call."""

    id = "cookbook-demo-model"

    def response(self, messages: Any, response_format: Any = None) -> _DemoResponse:
        entities = ExtractedEntities(
            entities=[
                ExtractedEntity(name="Acme Corp", entity_type="company", confidence=0.9)
            ]
        )
        return _DemoResponse(
            parsed=entities, response_usage=_DemoUsage(input_tokens=42, output_tokens=8)
        )

    async def aresponse(
        self, messages: Any, response_format: Any = None
    ) -> _DemoResponse:
        return self.response(messages, response_format)


discovery.register(
    "model", "cookbook_demo_model", obj=DemoExtractionModel, override=True
)

embedder = DemoEmbedder(dimensions=4)
extraction_model = DemoExtractionModel()

# One synthetic price row per demo component, keyed exactly the way the real metering code would
# derive it -- `embedder_provider_key`'s module-leaf-of-`type(embedder)` derivation for the
# embedder, `BaseModelProvider._registry_key_for` (the runtime-registration match) for the model.
# `PRICES` ships intentionally empty (see `egpt/models/pricing.py`'s module docstring); this is
# the same synthetic-row pattern `tests/unit/models/test_pricing.py` uses to test the arithmetic
# without shipping a real vendor rate.
PRICES[(embedder_provider_key(embedder), embedder.id)] = ModelPrice(
    input=0.02, output=0.0, as_of=date(2026, 8, 1)
)
_model_provider_key = BaseModelProvider._registry_key_for(extraction_model)
assert _model_provider_key is not None, (
    "runtime registration above should make this resolvable"
)
PRICES[(_model_provider_key, extraction_model.id)] = ModelPrice(
    input=1.0, output=3.0, as_of=date(2026, 8, 1)
)


# ---------------------------------------------------------------------------
# The vector substrate: the same tiny in-memory stand-in `23_consolidation_worker.py` uses,
# trimmed to what a write + consolidate cycle needs.
# ---------------------------------------------------------------------------

_SCOPE_SLOTS = ("tenant_id", "agent_id", "user_id", "team_id", "session_id")


def _scoped(record_scope: Scope, call_scope: Scope) -> bool:
    return all(
        getattr(call_scope, slot) is None
        or getattr(record_scope, slot) == getattr(call_scope, slot)
        for slot in _SCOPE_SLOTS
    )


@dataclass
class InMemoryVector:
    rows: Dict[str, Dict[str, MemoryRecord]] = field(default_factory=dict)

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        for record in records:
            self._bucket(scope)[record.id] = replace(record, substrate=Substrate.VECTOR)

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
            for record in self._bucket(scope).values()
            if record.tier in wanted
            and record.expired_at is None
            and _scoped(record.scope, scope)
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
        rows = [
            replace(r)
            for r in self._bucket(scope).values()
            if r.tier is tier and _scoped(r.scope, scope)
        ]
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
        row = self._bucket(scope).get(id)
        return replace(row) if row is not None and _scoped(row.scope, scope) else None

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)


def main() -> None:
    aggregator = get_default_aggregator()

    print("Embedding cost -- MemoryEmbedder.embed_texts(scope=...)")
    print("=" * 60)
    pipeline = MemoryEmbedder(
        embedder=embedder, dimensions=4, embedding_version="cookbook-demo-v1"
    )
    pipeline.embed_texts(["a note about deploys", "a note about billing"], scope=ACME)
    pipeline.embed_texts(["a globex support ticket"], scope=GLOBEX)
    print(f"  embedding cost entries recorded: {len(aggregator.costs)}")

    print()
    print("Extraction cost -- LLMEntityExtractor.extract(episode)")
    print("=" * 60)
    extractor = LLMEntityExtractor(model=deepcopy(extraction_model))
    for scope, content in [
        (ACME, "Ada met with Acme Corp about the renewal"),
        (GLOBEX, "A globex ticket about entities"),
    ]:
        episode = MemoryRecord(
            id=f"ep-{scope.tenant_id}",
            tier=MemoryTier.EPISODIC,
            content=content,
            scope=scope,
        )
        found = extractor.extract(episode)
        print(f"  {scope.tenant_id:<8} extracted {len(found)} entit(y/ies)")
    print(f"  cost entries recorded so far: {len(aggregator.costs)}")

    print()
    print("Store growth -- CoLocatedMemoryProvider.write / .consolidate")
    print("=" * 60)
    for scope, topics in [
        (ACME, ["deploys", "billing", "onboarding"]),
        (GLOBEX, ["support", "pricing"]),
    ]:
        vector = InMemoryVector()
        provider = CoLocatedMemoryProvider(
            vector=vector, config=MemoryProviderConfig(provider="co_located")
        )
        for i, topic in enumerate(topics):
            provider.write(
                scope,
                [
                    MemoryEvent(
                        content=f"a note about {topic}",
                        scope=scope,
                        id=f"{scope.tenant_id}-e{i}",
                    )
                ],
            )
        provider.consolidate(scope)
    print(f"  growth entries recorded: {len(aggregator.growth)}")

    print()
    print("Aggregated, per tenant")
    print("=" * 60)
    costs = aggregator.cost_by_tenant()
    growth = aggregator.growth_by_tenant()
    for tenant in sorted(
        {e.tenant_id for e in aggregator.costs}
        | {e.tenant_id for e in aggregator.growth}
    ):
        print(
            f"  {tenant:<8} cost=${costs.get(tenant, 0.0):.6f}  facts_written={growth.get(tenant, 0)}"
        )

    unpriced = sum(1 for e in aggregator.costs if e.cost is None)
    print()
    print(f"unpriced entries (cost=None, skipped by cost_by_tenant): {unpriced}")
    assert costs["acme"] > 0.0, (
        "acme's embedding + extraction calls are both priced above"
    )
    assert costs["globex"] > 0.0
    assert growth["acme"] > 0 and growth["globex"] > 0


if __name__ == "__main__":
    main()
