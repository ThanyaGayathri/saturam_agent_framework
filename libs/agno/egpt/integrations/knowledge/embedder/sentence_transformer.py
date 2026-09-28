from dataclasses import dataclass
from typing import ClassVar, Dict, List, Optional, Tuple, Union

from egpt.knowledge.embedder.base import Embedder
from egpt.utils.log import log_warning

try:
    from sentence_transformers import SentenceTransformer

except ImportError:
    raise ImportError("`sentence-transformers` not installed, please run `pip install sentence-transformers`")

try:
    import numpy as np

except ImportError:
    raise ImportError("numpy not installed, use `pip install numpy`")


@dataclass
class SentenceTransformerEmbedder(Embedder):
    id: str = "sentence-transformers/all-MiniLM-L6-v2"
    dimensions: int = 384
    sentence_transformer_client: Optional[SentenceTransformer] = None
    prompt: Optional[str] = None
    normalize_embeddings: bool = False
    #: ``get_embedding`` accepts a list of texts and returns one vector per element -- see its
    #: ``Union[str, List[str]]`` signature below, where ``encode`` returns a 2D array.
    #:
    #: Distinct from ``Embedder.enable_batch``, which gates the ``*_get_embeddings_batch_and_usage``
    #: API this class does not implement; that flag is left alone, so no knowledge-pipeline call
    #: site changes behaviour. Read by ``egpt.memory.embedding`` (SM-V2 #96), which batches through
    #: ``get_embedding`` because the batch API is async-only and would split the sync/async twins.
    #:
    #: ``ClassVar``, not a dataclass field: this declares a capability of the class, and a plain
    #: field would make ``SentenceTransformerEmbedder(accepts_text_batches=False)`` a valid call
    #: that silently turns batching off on the one embedder that supports it, with nothing
    #: downstream able to tell that from a real refusal.
    accepts_text_batches: ClassVar[bool] = True

    def __post_init__(self):
        # Initialize the SentenceTransformer model eagerly to avoid race conditions in async contexts
        if self.sentence_transformer_client is None:
            self.sentence_transformer_client = SentenceTransformer(model_name_or_path=self.id)
        self._warm_up()

    def _warm_up(self) -> None:
        """Run one throwaway encode, single-threaded, at construction.

        Constructing the model is **not** the same as initializing it. `SentenceTransformer(...)`
        loads weights, but the first `encode()` is what allocates the backend's compute context
        (Metal on macOS, and the lazy kernel setup torch does generally). Two threads entering
        that first `encode()` concurrently deadlock: both processes sit at ~300% CPU and neither
        returns, indefinitely.

        The eager construction above was already there for this reason -- "to avoid race
        conditions in async contexts" -- it just stopped one step short of the call that
        actually does the initializing.

        Reproduced on a fresh instance: two threads whose *first* call is concurrent never
        return; the same two threads after a single warm-up call finish in 0.0s. It surfaced
        through a retrieval fan-out that ran a prose leg (pgvector `hybrid_search`) and an
        ontology leg (`search_vector`) concurrently over one shared embedder -- neither leg
        being at fault, and each fine on its own.

        Cheap: one encode of one short string, once per instance. Failures are swallowed, since
        a warm-up that cannot run is not a reason to refuse to construct an embedder -- the real
        call will raise on its own terms, with its own text, if the model is genuinely broken.
        """
        try:
            self.sentence_transformer_client.encode(  # type: ignore[union-attr]
                "warm up", prompt=self.prompt, normalize_embeddings=self.normalize_embeddings
            )
        except Exception as e:  # noqa: BLE001 -- see docstring
            log_warning(f"SentenceTransformer warm-up failed, first concurrent encode may block: {e}")

    def get_embedding(self, text: Union[str, List[str]]) -> List[float]:
        if self.sentence_transformer_client is None:
            raise RuntimeError("SentenceTransformer model not initialized")
        model = self.sentence_transformer_client
        embedding = model.encode(text, prompt=self.prompt, normalize_embeddings=self.normalize_embeddings)
        try:
            if isinstance(embedding, np.ndarray):
                return embedding.tolist()

            return embedding  # type: ignore
        except Exception as e:
            log_warning(f"Failed to get embedding: {str(e)}")
            return []

    def get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        return self.get_embedding(text=text), None

    async def async_get_embedding(self, text: Union[str, List[str]]) -> List[float]:
        """Async version using thread executor for CPU-bound operations."""
        import asyncio

        loop = asyncio.get_running_loop()
        # Run the CPU-bound operation in a thread executor
        return await loop.run_in_executor(None, self.get_embedding, text)

    async def async_get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        """Async version using thread executor for CPU-bound operations."""
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_embedding_and_usage, text)
