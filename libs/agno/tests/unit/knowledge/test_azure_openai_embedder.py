"""Unit tests for AzureOpenAIEmbedder input formatting.

Validates that the embedder always sends input as a list to the Azure
OpenAI API, which is required by non-default deployed models (e.g.
Cohere-embed-v3-multilingual).  See: https://github.com/agno-agi/agno/issues/6759
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture()
def _mock_openai():
    """Create and return mocked OpenAI client and response objects for testing AzureOpenAIEmbedder without real credentials."""
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
    mock_response.usage = MagicMock()
    mock_response.usage.model_dump.return_value = {"prompt_tokens": 5, "total_tokens": 5}

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = mock_response

    return mock_client, mock_response


class TestAzureOpenAIEmbedderInputFormat:
    """Ensure input is always sent as a list, not a bare string."""

    def test_response_wraps_input_as_list(self, _mock_openai):
        mock_client, _ = _mock_openai

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            openai_client=mock_client,
        )

        embedder.get_embedding("hello world")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert isinstance(call_kwargs["input"], list), "input must be a list for Azure API compatibility"
        assert call_kwargs["input"] == ["hello world"]

    def test_response_wraps_input_for_non_default_model(self, _mock_openai):
        mock_client, _ = _mock_openai

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="Cohere-embed-v3-multilingual",
            dimensions=1024,
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            azure_deployment="Cohere-embed-v3-multilingual",
            openai_client=mock_client,
        )

        embedder.get_embedding("The quick brown fox")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert isinstance(call_kwargs["input"], list)
        assert call_kwargs["input"] == ["The quick brown fox"]

    def test_dimensions_passed_for_custom_azure_deployment(self, _mock_openai):
        """When azure_deployment is set, dimensions should be forwarded."""
        mock_client, _ = _mock_openai

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="Cohere-embed-v3-multilingual",
            dimensions=1024,
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            azure_deployment="Cohere-embed-v3-multilingual",
            openai_client=mock_client,
        )

        embedder.get_embedding("test")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["dimensions"] == 1024

    def test_dimensions_not_sent_for_custom_model_without_explicit_dimensions(self, _mock_openai):
        """When using a custom model without setting dimensions, dimensions should NOT be in the request."""
        mock_client, _ = _mock_openai

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="Cohere-embed-v3-multilingual",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            azure_deployment="Cohere-embed-v3-multilingual",
            openai_client=mock_client,
        )

        embedder.get_embedding("test")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "dimensions" not in call_kwargs

    @pytest.mark.asyncio()
    async def test_aresponse_wraps_input_as_list(self, _mock_openai):
        _, mock_response = _mock_openai

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        async_mock = MagicMock()
        async_mock.embeddings.create = AsyncMock(return_value=mock_response)

        embedder = AzureOpenAIEmbedder(
            id="Cohere-embed-v3-multilingual",
            dimensions=1024,
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            azure_deployment="Cohere-embed-v3-multilingual",
            async_client=async_mock,
        )

        await embedder.async_get_embedding("test async input")

        call_kwargs = async_mock.embeddings.create.call_args[1]
        assert isinstance(call_kwargs["input"], list)
        assert call_kwargs["input"] == ["test async input"]


class TestAzureOpenAIEmbedderBatching:
    """``get_embedding`` given a list is one request, not one per text (SM-V2 #96 round-2 finding
    #2). The memory pipeline reads this shape through the declared ``accepts_text_batches``
    capability, matching ``SentenceTransformerEmbedder``'s existing one."""

    def test_accepts_text_batches_is_declared(self):
        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        assert AzureOpenAIEmbedder.accepts_text_batches is True

    def test_a_list_of_texts_is_one_api_call_returning_one_vector_per_text_in_order(self, _mock_openai):
        mock_client, mock_response = _mock_openai
        mock_response.data = [
            MagicMock(index=0, embedding=[0.1, 0.1]),
            MagicMock(index=1, embedding=[0.2, 0.2]),
            MagicMock(index=2, embedding=[0.3, 0.3]),
        ]

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            openai_client=mock_client,
        )

        result = embedder.get_embedding(["a", "b", "c"])

        assert mock_client.embeddings.create.call_count == 1
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == ["a", "b", "c"]
        assert result == [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]

    def test_a_response_out_of_order_is_sorted_by_index_not_trusted_to_arrive_in_order(self, _mock_openai):
        """The regression this closes: relying on the API's documented-but-unenforced ordering
        zips a vector to the wrong text the moment a response ever arrives out of order. Sorted by
        the response's own ``.index`` rather than trusted positionally."""
        mock_client, mock_response = _mock_openai
        mock_response.data = [
            MagicMock(index=2, embedding=[0.3, 0.3]),
            MagicMock(index=0, embedding=[0.1, 0.1]),
            MagicMock(index=1, embedding=[0.2, 0.2]),
        ]

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            openai_client=mock_client,
        )

        result = embedder.get_embedding(["a", "b", "c"])

        assert result == [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]

    def test_a_single_bare_string_still_takes_the_single_text_path(self, _mock_openai):
        """The plural form is additive -- a bare string still returns a bare vector, not a
        one-element list of one, which would silently break every existing single-text caller."""
        mock_client, _ = _mock_openai

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            openai_client=mock_client,
        )

        result = embedder.get_embedding("solo")

        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio()
    async def test_the_async_half_batches_identically(self, _mock_openai):
        _, mock_response = _mock_openai
        mock_response.data = [MagicMock(index=0, embedding=[0.1, 0.1]), MagicMock(index=1, embedding=[0.2, 0.2])]

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        async_mock = MagicMock()
        async_mock.embeddings.create = AsyncMock(return_value=mock_response)

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            async_client=async_mock,
        )

        result = await embedder.async_get_embedding(["x", "y"])

        assert async_mock.embeddings.create.call_count == 1
        call_kwargs = async_mock.embeddings.create.call_args[1]
        assert call_kwargs["input"] == ["x", "y"]
        assert result == [[0.1, 0.1], [0.2, 0.2]]

    @pytest.mark.asyncio()
    async def test_the_async_half_sorts_by_index_too(self, _mock_openai):
        """The async twin of the out-of-order test above -- ``_abatch_response`` is a separate code
        path from ``_batch_response`` and needs its own proof, not an inference from the sync half."""
        _, mock_response = _mock_openai
        mock_response.data = [
            MagicMock(index=2, embedding=[0.3, 0.3]),
            MagicMock(index=0, embedding=[0.1, 0.1]),
            MagicMock(index=1, embedding=[0.2, 0.2]),
        ]

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        async_mock = MagicMock()
        async_mock.embeddings.create = AsyncMock(return_value=mock_response)

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            async_client=async_mock,
        )

        result = await embedder.async_get_embedding(["a", "b", "c"])

        assert result == [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]

    @pytest.mark.asyncio()
    async def test_async_get_embeddings_batch_and_usage_also_sorts_by_index(self, _mock_openai):
        """Review finding on #270: this method sends the same list-input request as
        ``async_get_embedding`` but read ``response.data`` back in raw order, with no sort guard --
        the same bug closed elsewhere in this class, missed on this call site."""
        _, mock_response = _mock_openai
        mock_response.data = [
            MagicMock(index=2, embedding=[0.3, 0.3]),
            MagicMock(index=0, embedding=[0.1, 0.1]),
            MagicMock(index=1, embedding=[0.2, 0.2]),
        ]

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        async_mock = MagicMock()
        async_mock.embeddings.create = AsyncMock(return_value=mock_response)

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            async_client=async_mock,
        )

        embeddings, _usage = await embedder.async_get_embeddings_batch_and_usage(["a", "b", "c"])

        assert embeddings == [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]

    def test_an_empty_list_short_circuits_without_a_request(self, _mock_openai):
        mock_client, _ = _mock_openai

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            openai_client=mock_client,
        )

        assert embedder.get_embedding([]) == []
        mock_client.embeddings.create.assert_not_called()

    @pytest.mark.asyncio()
    async def test_the_async_empty_list_short_circuits_too(self, _mock_openai):
        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        async_mock = MagicMock()
        async_mock.embeddings.create = AsyncMock()

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            async_client=async_mock,
        )

        assert await embedder.async_get_embedding([]) == []
        async_mock.embeddings.create.assert_not_called()

    def test_a_response_with_a_duplicate_and_a_missing_index_is_refused_not_silently_zipped(self, _mock_openai):
        """Same failure mode as the OpenAI-side test of the same name: same length as the input,
        but the index set has a duplicate and a gap, so a plain sort cannot catch it -- only the
        shared ``sorted_embeddings`` index-set check can. Refused, not silently zipped."""
        mock_client, mock_response = _mock_openai
        mock_response.data = [
            MagicMock(index=0, embedding=[0.1, 0.1]),
            MagicMock(index=0, embedding=[0.1, 0.1]),
            MagicMock(index=1, embedding=[0.2, 0.2]),
        ]

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            openai_client=mock_client,
        )

        assert embedder.get_embedding(["a", "b", "c"]) == []

    def test_a_short_response_is_refused_not_silently_accepted_as_one_fewer_vector(self, _mock_openai):
        """Review finding on #270, round 3: a response with one fewer record than requested is a
        clean permutation of *its own* (shorter) length, so a check against the response's own
        data alone would pass it. Refused because the check is against the request size."""
        mock_client, mock_response = _mock_openai
        mock_response.data = [
            MagicMock(index=0, embedding=[0.1, 0.1]),
            MagicMock(index=1, embedding=[0.2, 0.2]),
        ]

        from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        embedder = AzureOpenAIEmbedder(
            id="text-embedding-3-small",
            api_key="test-key",
            azure_endpoint="https://test.openai.azure.com/",
            openai_client=mock_client,
        )

        assert embedder.get_embedding(["a", "b", "c"]) == []
