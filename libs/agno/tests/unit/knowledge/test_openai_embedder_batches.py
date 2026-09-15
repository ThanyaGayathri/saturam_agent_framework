"""``OpenAIEmbedder.get_embedding`` / ``async_get_embedding`` given a list.

Before this, a list was sent as one request (the endpoint accepts a list natively) and only
``response.data[0].embedding`` came back -- one vector for N texts. A caller batching an
ontology into a vector store read that as "1024 vectors for 27 concepts" and refused. The
Azure embedder already had the plural contract; this pins the same contract on the OpenAI one,
including the declared capability the memory pipeline reads to decide whether to batch at all.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("openai", reason="OpenAIEmbedder imports the openai SDK at module scope")

from egpt.integrations.knowledge.embedder.openai import OpenAIEmbedder  # noqa: E402


def _response_for(inputs):
    """What the endpoint returns: one record per input, ``index`` naming its position.
    Returned deliberately *out of order* so the sort by index is exercised, not assumed."""
    items = [SimpleNamespace(index=i, embedding=[float(i), float(i) + 0.5]) for i in range(len(inputs))]
    return SimpleNamespace(data=list(reversed(items)), usage=None)


class _Embeddings:
    def __init__(self):
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        given = request["input"]
        return _response_for(given if isinstance(given, list) else [given])


class _AsyncEmbeddings(_Embeddings):
    async def create(self, **request):  # type: ignore[override]
        return super().create(**request)


def _embedder(sync=True):
    embeddings = _AsyncEmbeddings() if not sync else _Embeddings()
    client = SimpleNamespace(embeddings=embeddings)
    if sync:
        return OpenAIEmbedder(id="text-embedding-3-large", dimensions=2, openai_client=client), embeddings
    return OpenAIEmbedder(id="text-embedding-3-large", dimensions=2, async_client=client), embeddings


def test_the_capability_is_declared_on_the_class():
    assert OpenAIEmbedder.accepts_text_batches is True


def test_a_single_text_still_returns_one_vector():
    embedder, embeddings = _embedder()
    assert embedder.get_embedding("one") == [0.0, 0.5]
    assert embeddings.calls[0]["input"] == "one"


def test_a_list_is_one_request_and_one_vector_per_text_in_order():
    embedder, embeddings = _embedder()
    vectors = embedder.get_embedding(["a", "b", "c"])
    assert vectors == [[0.0, 0.5], [1.0, 1.5], [2.0, 2.5]]
    assert len(embeddings.calls) == 1
    assert embeddings.calls[0]["input"] == ["a", "b", "c"]
    assert embeddings.calls[0]["dimensions"] == 2


@pytest.mark.asyncio
async def test_the_async_half_has_the_same_contract():
    embedder, embeddings = _embedder(sync=False)
    assert await embedder.async_get_embedding("one") == [0.0, 0.5]
    vectors = await embedder.async_get_embedding(["a", "b"])
    assert vectors == [[0.0, 0.5], [1.0, 1.5]]
    assert [c["input"] for c in embeddings.calls] == ["one", ["a", "b"]]
