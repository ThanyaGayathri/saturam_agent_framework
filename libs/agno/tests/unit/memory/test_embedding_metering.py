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
from egpt.memory.errors import MemoryProviderConfigError
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


class ApiFailingUsageRecorder(UsageRecorder):
    """Declares ``_and_usage`` but it fails the way a real API call fails -- a bare exception with
    **no** try/except of its own, matching ``CohereEmbedder``/``VoyageAIEmbedder``/
    ``AzureOpenAIEmbedder``'s real ``get_embedding_and_usage`` (unlike their paired
    ``get_embedding``, which does swallow a failure). The plain call still succeeds -- a transient
    failure on the metered path only, exactly the shape a real timeout/auth-error/rate-limit takes
    (review finding, PR #159)."""

    id = "api-failing-usage-recorder-v1"

    def get_embedding_and_usage(self, text):
        self.usage_calls.append(text)
        raise ConnectionError("simulated API failure")

    async def async_get_embedding_and_usage(self, text):
        await asyncio.sleep(0)
        self.usage_calls.append(text)
        raise ConnectionError("simulated API failure")


class DuckTypedEmbedder:
    """A minimal duck-typed embedder -- ``get_embedding``/``async_get_embedding`` only, exactly
    like ``StubEmbedder`` in ``test_pgvector_memory_substrate.py``. It does not inherit from
    ``Embedder`` and has no ``get_embedding_and_usage``/``async_get_embedding_and_usage`` at all --
    not even one that raises ``NotImplementedError``. Calling a missing method raises
    ``AttributeError``, not ``NotImplementedError``, which is exactly the shape the pipeline must
    guard against without crashing (finding #1, PR #159 review)."""

    id = "duck-typed-v1"
    dimensions = DIM
    offline_capable = True

    def __init__(self):
        self.plain_calls = []

    def get_embedding(self, text):
        self.plain_calls.append(text)
        return [float(len(text)), 0.0, 0.0, 0.0]

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)


class EmptyVectorRecorder(UsageRecorder):
    """``get_embedding_and_usage`` returns a real usage payload but an empty vector -- the shape
    ``OpenAIEmbedder`` produces when it swallows its own API failure and returns ``([], None)``.
    ``_as_vectors`` refuses this with ``MemoryProviderConfigError`` (empty result), which must not
    come at the cost of losing the usage this call already, genuinely, paid for (finding at
    ``embedding.py:679``, PR #159 round-2 review)."""

    id = "empty-vector-v1"

    def get_embedding_and_usage(self, text):
        self.usage_calls.append(text)
        return [], {"prompt_tokens": len(text), "total_tokens": len(text)}

    async def async_get_embedding_and_usage(self, text):
        await asyncio.sleep(0)
        return self.get_embedding_and_usage(text)


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
# A real API failure on the metered path (not just an unimplemented method) must degrade the same
# way the non-metered call already does, not surface a raw SDK exception (major finding, PR #159
# review -- see ApiFailingUsageRecorder).
# ---------------------------------------------------------------------------


class TestFallbackWhenUsageCallRaisesAnApiFailure:
    def test_embed_texts_does_not_raise_and_falls_back_to_the_plain_call(self):
        embedder = ApiFailingUsageRecorder()
        p = pipeline(embedder)

        vectors = p.embed_texts(["hello"], scope=SCOPE)

        assert len(vectors) == 1
        assert vectors[0] == [5.0, 0.0, 0.0, 0.0]
        assert embedder.usage_calls == ["hello"]  # the metered call was attempted
        assert embedder.plain_calls == ["hello"]  # ...then the fallback ran
        entries = get_default_aggregator().costs
        assert len(entries) == 1
        assert entries[0].cost is None

    async def test_aembed_texts_does_not_raise_and_falls_back_to_the_plain_call(self):
        embedder = ApiFailingUsageRecorder()
        p = pipeline(embedder)

        vectors = await p.aembed_texts(["hello"], scope=SCOPE)

        assert len(vectors) == 1
        assert embedder.usage_calls == ["hello"]
        assert embedder.plain_calls == ["hello"]
        entries = get_default_aggregator().costs
        assert len(entries) == 1
        assert entries[0].cost is None

    def test_embed_query_does_not_raise(self):
        p = pipeline(ApiFailingUsageRecorder())

        vector = p.embed_query("hello", scope=SCOPE)

        assert vector == [5.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# An embedder lacking get_embedding_and_usage entirely degrades gracefully, not with an
# AttributeError (regression pinned by finding #1, PR #159 review -- see DuckTypedEmbedder).
# ---------------------------------------------------------------------------


class TestEmbedderMissingUsageMethodEntirely:
    def test_embed_texts_does_not_raise_and_falls_back_to_the_plain_call(self):
        """Same observable shape as ``NoUsageRecorder`` (declared but ``NotImplementedError``) --
        the fallback records an ``cost=None`` "unknown" entry rather than skipping metering
        outright, since a call was genuinely attempted with a scope."""
        embedder = DuckTypedEmbedder()
        p = pipeline(embedder)

        vectors = p.embed_texts(["hello"], scope=SCOPE)

        assert len(vectors) == 1
        assert vectors[0] == [5.0, 0.0, 0.0, 0.0]
        assert embedder.plain_calls == ["hello"]
        entries = get_default_aggregator().costs
        assert len(entries) == 1
        assert entries[0].cost is None
        assert entries[0].input_tokens == 0

    async def test_aembed_texts_does_not_raise_and_falls_back_to_the_plain_call(self):
        embedder = DuckTypedEmbedder()
        p = pipeline(embedder)

        vectors = await p.aembed_texts(["hello"], scope=SCOPE)

        assert len(vectors) == 1
        assert embedder.plain_calls == ["hello"]
        entries = get_default_aggregator().costs
        assert len(entries) == 1
        assert entries[0].cost is None

    def test_embed_query_does_not_raise(self):
        p = pipeline(DuckTypedEmbedder())

        vector = p.embed_query("hello", scope=SCOPE)

        assert vector == [5.0, 0.0, 0.0, 0.0]
        assert len(get_default_aggregator().costs) == 1


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


# ---------------------------------------------------------------------------
# Usage is recorded even when the embedder's vector fails shape validation (finding at
# embedding.py:679, PR #159 round-2 review): a real, likely-billed API call must never end up
# with zero metering trace just because it also returned an unusable vector.
# ---------------------------------------------------------------------------


class TestUsageRecordedBeforeVectorValidation:
    def test_embed_texts_records_usage_even_though_the_empty_vector_then_raises(self):
        embedder = EmptyVectorRecorder()
        p = pipeline(embedder)

        with pytest.raises(MemoryProviderConfigError):
            p.embed_texts(["hello"], scope=SCOPE)

        assert embedder.usage_calls == ["hello"]
        entries = get_default_aggregator().costs
        assert len(entries) == 1
        assert entries[0].tenant_id == "acme"
        assert entries[0].kind == "embedding"

    async def test_aembed_texts_records_usage_even_though_the_empty_vector_then_raises(self):
        embedder = EmptyVectorRecorder()
        p = pipeline(embedder)

        with pytest.raises(MemoryProviderConfigError):
            await p.aembed_texts(["hello"], scope=SCOPE)

        assert embedder.usage_calls == ["hello"]
        assert len(get_default_aggregator().costs) == 1
