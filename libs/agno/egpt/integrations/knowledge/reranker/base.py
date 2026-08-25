import asyncio
from typing import List

from pydantic import BaseModel, ConfigDict

from egpt.knowledge.document import Document


class Reranker(BaseModel):
    """Base class for rerankers"""

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    def rerank(self, query: str, documents: List[Document]) -> List[Document]:
        raise NotImplementedError

    async def arerank(self, query: str, documents: List[Document]) -> List[Document]:
        """Async variant of :meth:`rerank`.

        Only ``InfinityReranker`` has its own (a real async network call); every other adapter
        -- ``cohere``, ``sentence_transformer``, ``aws_bedrock`` -- had no async path at all
        (SM-R8 DECISION.md S5 finding). Rather than add the same thread-offload to each, it lives
        here once: an adapter with a genuinely async client overrides it, and every other adapter
        gets a working ``arerank`` for free.
        """
        return await asyncio.to_thread(self.rerank, query, documents)
