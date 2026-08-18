from dataclasses import dataclass
from typing import ClassVar, Dict, List, Optional, Tuple


@dataclass
class Embedder:
    """Base class for managing embedders"""

    dimensions: Optional[int] = 1536
    enable_batch: bool = False
    batch_size: int = 100  # Number of texts to process in each API call
    #: Whether ``get_embedding``/``async_get_embedding`` accept a ``List[str]`` and return one
    #: vector per element, in order -- a property of the *single-text* call, distinct from
    #: ``enable_batch`` above (which gates a separate ``*_get_embeddings_batch_and_usage`` API on
    #: the embedders that implement it). Declared here, at ``ClassVar[bool] = False``, so a typo on
    #: a subclass (``accept_text_batches``, ``accepts_text_batch``) is a plain attribute lookup that
    #: silently returns the class-level default instead of raising -- the same risk a base
    #: declaration exists to close for every other flag on this class. ``egpt.memory.embedding``
    #: reads this to decide whether to batch a chunk of texts into one call.
    accepts_text_batches: ClassVar[bool] = False

    def get_embedding(self, text: str) -> List[float]:
        raise NotImplementedError

    def get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        raise NotImplementedError

    async def async_get_embedding(self, text: str) -> List[float]:
        raise NotImplementedError

    async def async_get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        raise NotImplementedError
