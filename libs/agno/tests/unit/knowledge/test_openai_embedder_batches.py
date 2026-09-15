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


class _RaisingEmbeddings:
    """A client whose ``create`` always fails -- proves the exception path is reached and ``[]``
    is returned rather than the exception propagating."""

    def __init__(self):
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        raise RuntimeError("embeddings API unavailable")


class _AsyncRaisingEmbeddings(_RaisingEmbeddings):
    async def create(self, **request):  # type: ignore[override]
        return super().create(**request)


def _malformed_response_for(inputs):
    """Same length as a correct response, but the index set has a duplicate (0 twice) and a gap
    (no 2) -- the exact shape a plain sort cannot detect, only an index-set check can."""
    items = [SimpleNamespace(index=0, embedding=[0.0, 0.5])] + [
        SimpleNamespace(index=i, embedding=[float(i), float(i) + 0.5]) for i in range(len(inputs) - 1)
    ]
    return SimpleNamespace(data=items, usage=None)


class _MalformedIndexEmbeddings(_Embeddings):
    def create(self, **request):
        self.calls.append(request)
        given = request["input"]
        return _malformed_response_for(given if isinstance(given, list) else [given])


class _AsyncMalformedIndexEmbeddings(_MalformedIndexEmbeddings):
    async def create(self, **request):  # type: ignore[override]
        return super().create(**request)


def _embedder(sync=True, embeddings=None):
    if embeddings is None:
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


@pytest.mark.asyncio
async def test_async_get_embeddings_batch_and_usage_also_sorts_by_index():
    """Review finding on #270: this method sends the same list-input request as
    ``async_get_embedding`` but read ``response.data`` back in raw order, with no sort guard --
    the same bug this PR fixes elsewhere in this file, missed on this call site. ``_response_for``
    already returns records out of order, so a regression here would zip a vector to the wrong
    text rather than merely fail an equality check."""
    embedder, _ = _embedder(sync=False)
    embeddings, usage = await embedder.async_get_embeddings_batch_and_usage(["a", "b", "c"])
    assert embeddings == [[0.0, 0.5], [1.0, 1.5], [2.0, 2.5]]


def test_an_empty_list_short_circuits_without_a_request():
    embedder, embeddings = _embedder()
    assert embedder.get_embedding([]) == []
    assert embeddings.calls == []


@pytest.mark.asyncio
async def test_the_async_empty_list_short_circuits_too():
    embedder, embeddings = _embedder(sync=False)
    assert await embedder.async_get_embedding([]) == []
    assert embeddings.calls == []


def test_a_list_call_that_raises_returns_empty_not_the_exception():
    embedder, embeddings = _embedder(embeddings=_RaisingEmbeddings())
    assert embedder.get_embedding(["a", "b"]) == []
    assert len(embeddings.calls) == 1


@pytest.mark.asyncio
async def test_the_async_list_call_that_raises_returns_empty_too():
    embedder, embeddings = _embedder(sync=False, embeddings=_AsyncRaisingEmbeddings())
    assert await embedder.async_get_embedding(["a", "b"]) == []
    assert len(embeddings.calls) == 1


def test_a_response_with_a_duplicate_and_a_missing_index_is_refused_not_silently_zipped():
    """The failure mode the sort alone cannot catch: same length as the input, but the index set
    has a duplicate and a gap, so a plain sort would zip at least one vector to the wrong text
    with no error. The guard raises internally; ``get_embedding``'s existing except clause turns
    that into a logged warning and ``[]``, exactly like any other request failure -- a wrong-shape
    response is treated as a failure, not as a same-length success."""
    embedder, embeddings = _embedder(embeddings=_MalformedIndexEmbeddings())
    assert embedder.get_embedding(["a", "b", "c"]) == []
    assert len(embeddings.calls) == 1


@pytest.mark.asyncio
async def test_the_async_half_refuses_a_malformed_index_set_too():
    embedder, embeddings = _embedder(sync=False, embeddings=_AsyncMalformedIndexEmbeddings())
    assert await embedder.async_get_embedding(["a", "b", "c"]) == []
    assert len(embeddings.calls) == 1
