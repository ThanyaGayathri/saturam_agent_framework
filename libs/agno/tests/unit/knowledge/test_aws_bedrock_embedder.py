"""Unit tests for AwsBedrockEmbedder's usage extraction (SM-X8, PR #159 review).

``get_embedding_and_usage``/``async_get_embedding_and_usage`` used to read
``response.get("usage")`` off the parsed response body, but Cohere-on-Bedrock's body is
``{"embeddings", "id", "response_type", "texts"}`` -- no ``usage``/``meta`` key -- so ``usage``
was always ``None``, silently defeating cost metering for this embedder. These pin that
``_usage_from_response`` actually finds a token count where Bedrock puts one: either forwarded
Cohere-native ``meta.billed_units``, or the ``x-amzn-bedrock-input-token-count`` HTTP header --
and that it still degrades to ``None`` (not a crash) when neither is present.

No real AWS call: ``get_client``/``get_async_client`` are stubbed with fakes shaped like the real
boto3/aioboto3 response envelope (``{"body": ..., "ResponseMetadata": {"HTTPHeaders": ...}}``).
"""

from __future__ import annotations

import json

import pytest

boto3 = pytest.importorskip("boto3", reason="AwsBedrockEmbedder requires boto3")
pytest.importorskip("aioboto3", reason="AwsBedrockEmbedder's async path requires aioboto3")

from egpt.integrations.knowledge.embedder.aws_bedrock import AwsBedrockEmbedder  # noqa: E402

EMBEDDING = [0.1, 0.2, 0.3]


class _SyncBody:
    """Mimics botocore's streaming body: one ``read()`` returning bytes."""

    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class _AsyncBody:
    """Mimics aioboto3's streaming body: one awaitable ``read()``."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _sync_response(payload: dict, headers: dict | None = None) -> dict:
    return {"body": _SyncBody(payload), "ResponseMetadata": {"HTTPHeaders": headers or {}}}


def _async_response(payload: dict, headers: dict | None = None) -> dict:
    return {"body": _AsyncBody(payload), "ResponseMetadata": {"HTTPHeaders": headers or {}}}


class _FakeSyncClient:
    def __init__(self, response: dict):
        self._response = response
        self.calls = 0

    def invoke_model(self, **kwargs):
        self.calls += 1
        return self._response


class _FakeAsyncClient:
    def __init__(self, response: dict):
        self._response = response
        self.calls = 0

    async def invoke_model(self, **kwargs):
        self.calls += 1
        return self._response


class _FakeAsyncClientContext:
    """The object ``async with self.get_async_client() as client:`` enters."""

    def __init__(self, client: _FakeAsyncClient):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc_info):
        return False


def embedder_with(response: dict) -> AwsBedrockEmbedder:
    embedder = AwsBedrockEmbedder(id="cohere.embed-multilingual-v3")
    embedder.get_client = lambda: _FakeSyncClient(response)
    return embedder


def embedder_with_async(response: dict) -> AwsBedrockEmbedder:
    embedder = AwsBedrockEmbedder(id="cohere.embed-multilingual-v3")
    embedder.get_async_client = lambda: _FakeAsyncClientContext(_FakeAsyncClient(response))
    return embedder


BODY_NO_USAGE = {"embeddings": [EMBEDDING], "id": "req-1", "response_type": "embeddings_floats", "texts": ["hi"]}
BODY_WITH_BILLED_UNITS = {**BODY_NO_USAGE, "meta": {"billed_units": {"input_tokens": 7}}}


class TestUsageFromBilledUnitsInTheBody:
    """Bedrock forwarding Cohere's native ``meta.billed_units`` shape unmodified."""

    def test_get_embedding_and_usage_reads_it(self):
        embedder = embedder_with(_sync_response(BODY_WITH_BILLED_UNITS))

        embedding, usage = embedder.get_embedding_and_usage("hello")

        assert embedding == EMBEDDING
        assert usage == {"input_tokens": 7}

    async def test_async_get_embedding_and_usage_reads_it(self):
        embedder = embedder_with_async(_async_response(BODY_WITH_BILLED_UNITS))

        embedding, usage = await embedder.async_get_embedding_and_usage("hello")

        assert embedding == EMBEDDING
        assert usage == {"input_tokens": 7}


class TestUsageFromTheBedrockTokenCountHeader:
    """No body-level usage, but Bedrock's own metering header is present."""

    def test_get_embedding_and_usage_reads_it(self):
        embedder = embedder_with(_sync_response(BODY_NO_USAGE, headers={"x-amzn-bedrock-input-token-count": "12"}))

        embedding, usage = embedder.get_embedding_and_usage("hello")

        assert embedding == EMBEDDING
        assert usage == {"input_tokens": 12}

    async def test_async_get_embedding_and_usage_reads_it(self):
        embedder = embedder_with_async(
            _async_response(BODY_NO_USAGE, headers={"x-amzn-bedrock-input-token-count": "12"})
        )

        embedding, usage = await embedder.async_get_embedding_and_usage("hello")

        assert embedding == EMBEDDING
        assert usage == {"input_tokens": 12}


class TestUsageIsNoneWhenNeitherIsPresent:
    """The pre-fix behavior for a response that carries no usage signal anywhere -- must still
    degrade to ``cost=None`` ("unknown"), not raise or fabricate a count."""

    def test_get_embedding_and_usage_returns_none(self):
        embedder = embedder_with(_sync_response(BODY_NO_USAGE))

        embedding, usage = embedder.get_embedding_and_usage("hello")

        assert embedding == EMBEDDING
        assert usage is None

    async def test_async_get_embedding_and_usage_returns_none(self):
        embedder = embedder_with_async(_async_response(BODY_NO_USAGE))

        embedding, usage = await embedder.async_get_embedding_and_usage("hello")

        assert embedding == EMBEDDING
        assert usage is None

    def test_get_embedding_is_unaffected(self):
        """`get_embedding` (no usage) must keep working exactly as before -- `response()` now
        stashes a `"usage"` key onto the body, but nothing about the embedding path reads it."""
        embedder = embedder_with(_sync_response(BODY_WITH_BILLED_UNITS))

        assert embedder.get_embedding("hello") == EMBEDDING


class TestMalformedHeaderDoesNotRaise:
    def test_a_non_numeric_header_falls_back_to_none(self):
        embedder = embedder_with(
            _sync_response(BODY_NO_USAGE, headers={"x-amzn-bedrock-input-token-count": "not-a-number"})
        )

        _, usage = embedder.get_embedding_and_usage("hello")

        assert usage is None
