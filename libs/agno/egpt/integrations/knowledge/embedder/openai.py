from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union, overload

from typing_extensions import Literal

from egpt.integrations.knowledge.embedder._embedding_ordering import sorted_embeddings
from egpt.knowledge.embedder.base import Embedder
from egpt.utils.log import log_info, log_warning

try:
    from openai import AsyncOpenAI
    from openai import OpenAI as OpenAIClient
    from openai.types.create_embedding_response import CreateEmbeddingResponse
except ImportError:
    raise ImportError("`openai` not installed")


@dataclass
class OpenAIEmbedder(Embedder):
    id: str = "text-embedding-3-small"
    dimensions: Optional[int] = None
    encoding_format: Literal["float", "base64"] = "float"
    user: Optional[str] = None
    api_key: Optional[str] = None
    organization: Optional[str] = None
    base_url: Optional[str] = None
    request_params: Optional[Dict[str, Any]] = None
    client_params: Optional[Dict[str, Any]] = None
    openai_client: Optional[OpenAIClient] = None
    async_client: Optional[AsyncOpenAI] = None

    #: ``get_embedding``/``async_get_embedding`` accept a ``List[str]`` and return one vector per
    #: element, in order -- the same contract ``AzureOpenAIEmbedder`` declares, on the same
    #: endpoint. ``ClassVar`` for the same reason as there: a declared capability of the class,
    #: not a per-instance switch. Before this, a list passed here was sent as one request (the
    #: endpoint accepts a list natively) and ``response.data[0]`` alone came back -- one vector
    #: for N texts, which a caller batching an ontology into the store read as a count mismatch.
    #:
    #: Declaring this also has a side effect on ``egpt.memory.embedding``: a batching embedder's
    #: calls are not usage-metered through ``MemoryEmbedder.embed_texts``/``aembed_texts`` (see
    #: that method's docstring for why) -- ``OpenAIEmbedder`` now shares that known gap with
    #: ``AzureOpenAIEmbedder``, deliberately, rather than as an incidental side effect of this flag.
    accepts_text_batches: ClassVar[bool] = True

    def __post_init__(self):
        if self.dimensions is None:
            self.dimensions = 3072 if self.id == "text-embedding-3-large" else 1536

    @property
    def client(self) -> OpenAIClient:
        if self.openai_client:
            return self.openai_client

        _client_params: Dict[str, Any] = {
            "api_key": self.api_key,
            "organization": self.organization,
            "base_url": self.base_url,
        }
        _client_params = {k: v for k, v in _client_params.items() if v is not None}
        if self.client_params:
            _client_params.update(self.client_params)
        self.openai_client = OpenAIClient(**_client_params)
        return self.openai_client

    @property
    def aclient(self) -> AsyncOpenAI:
        if self.async_client:
            return self.async_client
        params = {
            "api_key": self.api_key,
            "organization": self.organization,
            "base_url": self.base_url,
        }
        filtered_params: Dict[str, Any] = {k: v for k, v in params.items() if v is not None}
        if self.client_params:
            filtered_params.update(self.client_params)
        self.async_client = AsyncOpenAI(**filtered_params)
        return self.async_client

    def _request_params_for(self, text: Union[str, List[str]]) -> Dict[str, Any]:
        """The one request shape every call below sends; ``input`` is a string or a list of them."""
        _request_params: Dict[str, Any] = {
            "input": text,
            "model": self.id,
            "encoding_format": self.encoding_format,
        }
        if self.user is not None:
            _request_params["user"] = self.user
        # Pass dimensions for text-embedding-3 models or when using custom base_url (third-party APIs)
        if self.id.startswith("text-embedding-3") or self.base_url is not None:
            _request_params["dimensions"] = self.dimensions
        if self.request_params:
            _request_params.update(self.request_params)
        return _request_params

    def response(self, text: Union[str, List[str]]) -> CreateEmbeddingResponse:
        return self.client.embeddings.create(**self._request_params_for(text))

    @overload
    def get_embedding(self, text: str) -> List[float]: ...
    @overload
    def get_embedding(self, text: List[str]) -> List[List[float]]: ...
    def get_embedding(self, text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        """One vector for a single text, or one vector per text, in order, for a list.

        The plural form is one request: the endpoint accepts a list of inputs natively. Vectors
        come back sorted by ``.index`` rather than trusted to arrive in request order -- the same
        guard ``AzureOpenAIEmbedder.get_embedding`` takes, for the same reason: a documented
        ordering is not an enforced one, and zipping against the wrong record fails silently.
        """
        try:
            response: CreateEmbeddingResponse = self.response(text=text)
            if isinstance(text, str):
                return response.data[0].embedding
            return sorted_embeddings(response)
        except Exception as e:
            log_warning(f"Failed to get embedding: {str(e)}")
            return []

    def get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        try:
            response: CreateEmbeddingResponse = self.response(text=text)

            embedding = response.data[0].embedding
            usage = response.usage
            if usage:
                return embedding, usage.model_dump()
            return embedding, None
        except Exception as e:
            log_warning(f"Failed to get embedding and usage: {str(e)}")
            return [], None

    @overload
    async def async_get_embedding(self, text: str) -> List[float]: ...
    @overload
    async def async_get_embedding(self, text: List[str]) -> List[List[float]]: ...
    async def async_get_embedding(self, text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        """Async twin of :meth:`get_embedding`: same call shape, same ordering guarantee."""
        try:
            response: CreateEmbeddingResponse = await self.aclient.embeddings.create(**self._request_params_for(text))
            if isinstance(text, str):
                return response.data[0].embedding
            return sorted_embeddings(response)
        except Exception as e:
            log_warning(f"Failed to get async embedding: {str(e)}")
            return []

    async def async_get_embedding_and_usage(self, text: str):
        req: Dict[str, Any] = {
            "input": text,
            "model": self.id,
            "encoding_format": self.encoding_format,
        }
        if self.user is not None:
            req["user"] = self.user
        # Pass dimensions for text-embedding-3 models or when using custom base_url (third-party APIs)
        if self.id.startswith("text-embedding-3") or self.base_url is not None:
            req["dimensions"] = self.dimensions
        if self.request_params:
            req.update(self.request_params)

        try:
            response = await self.aclient.embeddings.create(**req)
            embedding = response.data[0].embedding
            usage = response.usage
            return embedding, usage.model_dump() if usage else None
        except Exception as e:
            log_warning(f"Error getting embedding: {str(e)}")
            return [], None

    async def async_get_embeddings_batch_and_usage(
        self, texts: List[str]
    ) -> Tuple[List[List[float]], List[Optional[Dict]]]:
        """
        Get embeddings and usage for multiple texts in batches (async version).

        Args:
            texts: List of text strings to embed

        Returns:
            Tuple of (List of embedding vectors, List of usage dictionaries)
        """
        all_embeddings = []
        all_usage = []
        log_info(f"Getting embeddings and usage for {len(texts)} texts in batches of {self.batch_size} (async)")

        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i : i + self.batch_size]

            req: Dict[str, Any] = {
                "input": batch_texts,
                "model": self.id,
                "encoding_format": self.encoding_format,
            }
            if self.user is not None:
                req["user"] = self.user
            # Pass dimensions for text-embedding-3 models or when using custom base_url (third-party APIs)
            if self.id.startswith("text-embedding-3") or self.base_url is not None:
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
                        log_warning(f"Error in individual async embedding fallback: {e2}: {e2}")
                        all_embeddings.append([])
                        all_usage.append(None)

        return all_embeddings, all_usage
