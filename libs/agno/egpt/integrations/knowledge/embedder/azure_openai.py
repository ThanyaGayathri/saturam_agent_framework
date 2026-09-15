from dataclasses import dataclass
from os import getenv
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union, overload

from typing_extensions import Literal

from egpt.integrations.knowledge.embedder._embedding_ordering import sorted_embeddings
from egpt.knowledge.embedder.base import Embedder
from egpt.utils.log import log_warning, logger

try:
    from openai import AsyncAzureOpenAI as AsyncAzureOpenAIClient
    from openai import AzureOpenAI as AzureOpenAIClient
    from openai.types.create_embedding_response import CreateEmbeddingResponse
except ImportError:
    raise ImportError("`openai` not installed")


@dataclass
class AzureOpenAIEmbedder(Embedder):
    id: str = "text-embedding-3-small"  # This has to match the model that you deployed at the provided URL
    dimensions: Optional[int] = None
    encoding_format: Literal["float", "base64"] = "float"
    user: Optional[str] = None
    api_key: Optional[str] = getenv("AZURE_EMBEDDER_OPENAI_API_KEY")
    api_version: str = getenv("AZURE_EMBEDDER_OPENAI_API_VERSION", "2024-10-21")
    azure_endpoint: Optional[str] = getenv("AZURE_EMBEDDER_OPENAI_ENDPOINT")
    azure_deployment: Optional[str] = getenv("AZURE_EMBEDDER_DEPLOYMENT")
    base_url: Optional[str] = None
    azure_ad_token: Optional[str] = None
    azure_ad_token_provider: Optional[Any] = None
    organization: Optional[str] = None
    request_params: Optional[Dict[str, Any]] = None
    client_params: Optional[Dict[str, Any]] = None
    openai_client: Optional[AzureOpenAIClient] = None
    async_client: Optional[AsyncAzureOpenAIClient] = None
    #: The Azure/OpenAI embeddings endpoint takes a list of inputs in one request natively (see
    #: `async_get_embeddings_batch_and_usage` below, which already relies on this), so
    #: `get_embedding` accepting a list costs nothing extra over the network -- unlike
    #: `SentenceTransformerEmbedder`'s in-process batching, there is no separate capability being
    #: claimed here, only that the existing single-text path also accepts the plural form.
    #:
    #: `ClassVar`, not a dataclass field: this is a declared capability of the class, and a plain
    #: field would let `AzureOpenAIEmbedder(accepts_text_batches=False)` silently turn it off per
    #: instance with nothing downstream able to tell the difference from a real refusal.
    accepts_text_batches: ClassVar[bool] = True

    def __post_init__(self):
        if self.dimensions is None and self.id.startswith("text-embedding-3"):
            self.dimensions = 1536

    @property
    def client(self) -> AzureOpenAIClient:
        if self.openai_client:
            return self.openai_client

        _client_params: Dict[str, Any] = {}
        if self.api_key:
            _client_params["api_key"] = self.api_key
        if self.api_version:
            _client_params["api_version"] = self.api_version
        if self.organization:
            _client_params["organization"] = self.organization
        if self.azure_endpoint:
            _client_params["azure_endpoint"] = self.azure_endpoint
        if self.azure_deployment:
            _client_params["azure_deployment"] = self.azure_deployment
        if self.base_url:
            _client_params["base_url"] = self.base_url
        if self.azure_ad_token:
            _client_params["azure_ad_token"] = self.azure_ad_token
        if self.azure_ad_token_provider:
            _client_params["azure_ad_token_provider"] = self.azure_ad_token_provider

        if self.client_params:
            _client_params.update(self.client_params)

        return AzureOpenAIClient(**_client_params)

    @property
    def aclient(self) -> AsyncAzureOpenAIClient:
        if self.async_client:
            return self.async_client

        _client_params: Dict[str, Any] = {}
        if self.api_key:
            _client_params["api_key"] = self.api_key
        if self.api_version:
            _client_params["api_version"] = self.api_version
        if self.organization:
            _client_params["organization"] = self.organization
        if self.azure_endpoint:
            _client_params["azure_endpoint"] = self.azure_endpoint
        if self.azure_deployment:
            _client_params["azure_deployment"] = self.azure_deployment
        if self.base_url:
            _client_params["base_url"] = self.base_url
        if self.azure_ad_token:
            _client_params["azure_ad_token"] = self.azure_ad_token
        if self.azure_ad_token_provider:
            _client_params["azure_ad_token_provider"] = self.azure_ad_token_provider

        if self.client_params:
            _client_params.update(self.client_params)

        self.async_client = AsyncAzureOpenAIClient(**_client_params)
        return self.async_client

    def _response(self, text: str) -> CreateEmbeddingResponse:
        _request_params: Dict[str, Any] = {
            "input": [text],
            "model": self.id,
            "encoding_format": self.encoding_format,
        }
        if self.user is not None:
            _request_params["user"] = self.user
        if self.dimensions is not None:
            _request_params["dimensions"] = self.dimensions
        if self.request_params:
            _request_params.update(self.request_params)

        return self.client.embeddings.create(**_request_params)

    def _batch_response(self, texts: List[str]) -> CreateEmbeddingResponse:
        """One request for many texts -- the same endpoint `_response` calls, given a list."""
        _request_params: Dict[str, Any] = {
            "input": texts,
            "model": self.id,
            "encoding_format": self.encoding_format,
        }
        if self.user is not None:
            _request_params["user"] = self.user
        if self.dimensions is not None:
            _request_params["dimensions"] = self.dimensions
        if self.request_params:
            _request_params.update(self.request_params)

        return self.client.embeddings.create(**_request_params)

    @overload
    def get_embedding(self, text: str) -> List[float]: ...
    @overload
    def get_embedding(self, text: List[str]) -> List[List[float]]: ...
    def get_embedding(self, text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        """One vector for a single text, or one vector per text, in order, for a list.

        The plural form is one request, not one per text -- the endpoint natively accepts a list
        of inputs (`async_get_embeddings_batch_and_usage` below already relies on this), and
        `response.data` returns in the same order it was given.

        The two ``@overload`` signatures above are the real contract: a caller passing ``str`` is
        told it gets ``List[float]`` back, and a caller passing ``List[str]`` is told it gets
        ``List[List[float]]`` -- the single implementation signature below is deliberately the
        union of both, because that is what a type checker sees at the call site, not what runs.
        """
        if isinstance(text, str):
            response: CreateEmbeddingResponse = self._response(text=text)
            try:
                return response.data[0].embedding
            except Exception as e:
                log_warning(f"Failed to get embedding: {str(e)}")
                return []
        if not text:
            return []
        response = self._batch_response(list(text))
        try:
            # Sorted by `.index` rather than trusted to arrive in request order: the API documents
            # that it preserves order, but a documented behaviour is not an enforced one, and this
            # zips silently against the wrong record if it is ever wrong -- the same failure mode
            # `_as_vectors`' count check exists to catch on the memory pipeline side of this call.
            return sorted_embeddings(response)
        except Exception as e:
            log_warning(f"Failed to get batch embeddings: {str(e)}")
            return []

    def get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        response: CreateEmbeddingResponse = self._response(text=text)

        embedding = response.data[0].embedding
        usage = response.usage
        return embedding, usage.model_dump()

    async def _aresponse(self, text: str) -> CreateEmbeddingResponse:
        """Async version of _response method."""
        _request_params: Dict[str, Any] = {
            "input": [text],
            "model": self.id,
            "encoding_format": self.encoding_format,
        }
        if self.user is not None:
            _request_params["user"] = self.user
        if self.dimensions is not None:
            _request_params["dimensions"] = self.dimensions
        if self.request_params:
            _request_params.update(self.request_params)

        return await self.aclient.embeddings.create(**_request_params)

    async def _abatch_response(self, texts: List[str]) -> CreateEmbeddingResponse:
        """Async twin of :meth:`_batch_response`."""
        _request_params: Dict[str, Any] = {
            "input": texts,
            "model": self.id,
            "encoding_format": self.encoding_format,
        }
        if self.user is not None:
            _request_params["user"] = self.user
        if self.dimensions is not None:
            _request_params["dimensions"] = self.dimensions
        if self.request_params:
            _request_params.update(self.request_params)

        return await self.aclient.embeddings.create(**_request_params)

    @overload
    async def async_get_embedding(self, text: str) -> List[float]: ...
    @overload
    async def async_get_embedding(self, text: List[str]) -> List[List[float]]: ...
    async def async_get_embedding(self, text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        """Async twin of :meth:`get_embedding`, same call shape, same ordering guarantee, same
        reason for the two ``@overload`` signatures above the union implementation."""
        if isinstance(text, str):
            response: CreateEmbeddingResponse = await self._aresponse(text=text)
            try:
                return response.data[0].embedding
            except Exception as e:
                log_warning(f"Failed to get embedding: {str(e)}")
                return []
        if not text:
            return []
        response = await self._abatch_response(list(text))
        try:
            return sorted_embeddings(response)
        except Exception as e:
            log_warning(f"Failed to get batch embeddings: {str(e)}")
            return []

    async def async_get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        """Async version of get_embedding_and_usage using the native Azure OpenAI async client."""
        response: CreateEmbeddingResponse = await self._aresponse(text=text)

        embedding = response.data[0].embedding
        usage = response.usage
        return embedding, usage.model_dump()

    async def async_get_embeddings_batch_and_usage(
        self, texts: List[str]
    ) -> Tuple[List[List[float]], List[Optional[Dict]]]:
        """
        Get embeddings and usage for multiple texts in batches.

        Args:
            texts: List of text strings to embed

        Returns:
            Tuple of (List of embedding vectors, List of usage dictionaries)
        """
        all_embeddings = []
        all_usage = []
        logger.info(f"Getting embeddings and usage for {len(texts)} texts in batches of {self.batch_size}")

        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i : i + self.batch_size]

            req: Dict[str, Any] = {
                "input": batch_texts,
                "model": self.id,
                "encoding_format": self.encoding_format,
            }
            if self.user is not None:
                req["user"] = self.user
            if self.dimensions is not None:
                req["dimensions"] = self.dimensions
            if self.request_params:
                req.update(self.request_params)

            try:
                response: CreateEmbeddingResponse = await self.aclient.embeddings.create(**req)
                batch_embeddings = sorted_embeddings(response)
                all_embeddings.extend(batch_embeddings)

                # For each embedding in the batch, add the same usage information
                usage_dict = response.usage.model_dump() if response.usage else None
                all_usage.extend([usage_dict] * len(batch_embeddings))
            except Exception as e:
                log_warning(f"Error in async batch embedding: {str(e)}")
                # Fallback to individual calls for this batch
                for text in batch_texts:
                    try:
                        embedding, usage = await self.async_get_embedding_and_usage(text)
                        all_embeddings.append(embedding)
                        all_usage.append(usage)
                    except Exception as e2:
                        log_warning(f"Error in individual async embedding fallback: {e2}")
                        all_embeddings.append([])
                        all_usage.append(None)

        return all_embeddings, all_usage
