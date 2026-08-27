"""Embedding cost metering through ``MemoryEmbedder.embed_texts``/``aembed_texts`` (SM-X8,
aicoe-tracker#144).

``scope`` is an optional kwarg, defaulting to ``None`` so every pre-existing caller (there are
several: ``pgvector.py``'s ``upsert``/``search``, every other test that already calls
``embed_texts`` without it) stays unaffected and unmetered. This file pins that backward
compatibility as a real assertion, not just a docstring claim, and pins that passing ``scope``
populates the shared default aggregator.

No real embedding API: every embedder here is a double, in the same spirit as
``test_memory_embedding.py``'s ``Recorder``/``BatchRecorder``.
"""

from __future__ import annotations

import asyncio

import pytest

from egpt.memory.embedding import MemoryEmbedder
from egpt.memory.metrics import get_default_aggregator, reset_default_aggregator
from egpt.memory.types import Scope

DIM = 4
SCOPE = Scope(tenant_id="acme", agent_id="agent-1")


@pytest.fixture(autouse=True)
def clean_default_aggregator():
    reset_default_aggregator()
    yield
    reset_default_aggregator()


class UsageRecorder:
    """A single-text embedder (no ``accepts_text_batches``) that implements both the plain and
    the ``_and_usage`` call shapes, matching ``OpenAIEmbedder``'s real signatures."""

    id = "usage-recorder-v1"
    dimensions = DIM
    batch_size = 100
    offline_capable = True

    def __init__(self):
        self.usage_calls = []
        self.plain_calls = []

    def _vector(self, text):
        return [float(len(text)), 0.0, 0.0, 0.0]

    def get_embedding(self, text):
        self.plain_calls.append(text)
        return self._vector(text)

    def get_embedding_and_usage(self, text):
        self.usage_calls.append(text)
        return self._vector(text), {"prompt_tokens": len(text), "total_tokens": len(text)}

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)

    async def async_get_embedding_and_usage(self, text):
        await asyncio.sleep(0)
        return self.get_embedding_and_usage(text)


class NoUsageRecorder(UsageRecorder):
    """Declares no ``_and_usage`` variant -- the base ``Embedder`` stub raises
    ``NotImplementedError``, which the pipeline must fall back from without breaking the embed."""

    id = "no-usage-recorder-v1"

    def get_embedding_and_usage(self, text):
        raise NotImplementedError

    async def async_get_embedding_and_usage(self, text):
        raise NotImplementedError


class BatchRecorder:
    """A batching embedder (``AzureOpenAIEmbedder``/``SentenceTransformerEmbedder`` shape) --
    ``get_embedding_and_usage`` on this tree's real batching embedders is typed and implemented
    for a single ``str`` only, so handing it a list would silently mis-shape the request. The
    pipeline must not attempt metering through that path for a batching embedder."""

    id = "batch-recorder-v1"
    dimensions = DIM
    accepts_text_batches = True
    batch_size = 100
    offline_capable = True

    def __init__(self):
        self.usage_calls = []

    def get_embedding(self, text):
        texts = list(text)
        return [[float(len(t)), 0.0, 0.0, 0.0] for t in texts]

    def get_embedding_and_usage(self, text):
        # Real batching embedders in this tree accept only `str` here; a double that tolerated a
        # list would hide the exact bug the pipeline's non-batching guard exists to avoid.
        self.usage_calls.append(text)
        if not isinstance(text, str):
            raise TypeError("get_embedding_and_usage takes one string")
        return [float(len(text)), 0.0, 0.0, 0.0], {"prompt_tokens": len(text)}

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)

    async def async_get_embedding_and_usage(self, text):
        await asyncio.sleep(0)
        return self.get_embedding_and_usage(text)


def pipeline(embedder):
    return MemoryEmbedder(embedder=embedder, dimensions=DIM, embedding_version="v1")


# ---------------------------------------------------------------------------
# scope=None is a complete no-op on the aggregator (backward compatibility)
# ---------------------------------------------------------------------------


class TestNoScopeIsUnmetered:
    def test_embed_texts_without_scope_touches_nothing(self):
        p = pipeline(UsageRecorder())
        vectors = p.embed_texts(["hello", "world"])

        assert len(vectors) == 2
        assert get_default_aggregator().costs == []

    def test_embed_query_without_scope_touches_nothing(self):
        p = pipeline(UsageRecorder())
        p.embed_query("hello")

        assert get_default_aggregator().costs == []

    async def test_aembed_texts_without_scope_touches_nothing(self):
        p = pipeline(UsageRecorder())
        vectors = await p.aembed_texts(["hello", "world"])

        assert len(vectors) == 2
        assert get_default_aggregator().costs == []


# ---------------------------------------------------------------------------
# scope= populates the default aggregator, for a non-batching embedder
# ---------------------------------------------------------------------------


class TestScopePopulatesTheAggregator:
    def test_embed_texts_with_scope_records_one_cost_entry_per_text(self):
        embedder = UsageRecorder()
        p = pipeline(embedder)

        vectors = p.embed_texts(["hello", "there"], scope=SCOPE)

        assert len(vectors) == 2
        entries = get_default_aggregator().costs
        assert len(entries) == 2
        for entry in entries:
            assert entry.tenant_id == "acme"
            assert entry.agent_id == "agent-1"
            assert entry.kind == "embedding"
        # get_embedding_and_usage was the call actually made, not the plain fallback.
        assert embedder.usage_calls == ["hello", "there"]
        assert embedder.plain_calls == []

    def test_embed_query_with_scope_records_a_cost_entry(self):
        p = pipeline(UsageRecorder())

        p.embed_query("hello", scope=SCOPE)

        assert len(get_default_aggregator().costs) == 1

    async def test_aembed_texts_with_scope_records_entries(self):
        embedder = UsageRecorder()
        p = pipeline(embedder)

        await p.aembed_texts(["hello", "there"], scope=SCOPE)

        assert len(get_default_aggregator().costs) == 2
        assert embedder.usage_calls == ["hello", "there"]

    async def test_aembed_query_with_scope_records_a_cost_entry(self):
        p = pipeline(UsageRecorder())

        await p.aembed_query("hello", scope=SCOPE)

        assert len(get_default_aggregator().costs) == 1


# ---------------------------------------------------------------------------
# An embedder with no _and_usage variant falls back without breaking the embed
# ---------------------------------------------------------------------------


class TestFallbackWhenUsageIsUnimplemented:
    def test_embed_texts_falls_back_to_the_plain_call_and_still_records_an_unknown_entry(self):
        embedder = NoUsageRecorder()
        p = pipeline(embedder)

        vectors = p.embed_texts(["hello"], scope=SCOPE)

        assert len(vectors) == 1
        assert vectors[0] == [5.0, 0.0, 0.0, 0.0]
        assert embedder.plain_calls == ["hello"]
        entries = get_default_aggregator().costs
        assert len(entries) == 1
        assert entries[0].cost is None
        assert entries[0].input_tokens == 0

    async def test_aembed_texts_falls_back_to_the_plain_call(self):
        embedder = NoUsageRecorder()
        p = pipeline(embedder)

        vectors = await p.aembed_texts(["hello"], scope=SCOPE)

        assert len(vectors) == 1
        assert len(get_default_aggregator().costs) == 1
        assert get_default_aggregator().costs[0].cost is None


# ---------------------------------------------------------------------------
# A batching embedder is not metered through this hook (documented gap, see embedding.py)
# ---------------------------------------------------------------------------


class TestBatchingEmbedderIsNotMeteredHere:
    def test_embed_texts_on_a_batching_embedder_records_nothing_even_with_scope(self):
        embedder = BatchRecorder()
        p = pipeline(embedder)

        vectors = p.embed_texts(["hello", "there"], scope=SCOPE)

        assert len(vectors) == 2
        assert get_default_aggregator().costs == []
        # And critically: the singular usage API was never called with a list, which is the
        # request-corruption failure mode this design avoids.
        assert embedder.usage_calls == []

    async def test_aembed_texts_on_a_batching_embedder_records_nothing(self):
        embedder = BatchRecorder()
        p = pipeline(embedder)

        await p.aembed_texts(["hello", "there"], scope=SCOPE)

        assert get_default_aggregator().costs == []
        assert embedder.usage_calls == []
