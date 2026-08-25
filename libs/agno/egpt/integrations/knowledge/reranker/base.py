import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List

from pydantic import BaseModel, ConfigDict

from egpt.knowledge.document import Document

#: Dedicated to the default `arerank`'s thread-offload, not `asyncio.to_thread`'s process-wide
#: default executor (round-3 review finding on `egpt.memory.retrieval.rerank`, this default's
#: first caller): that caller budgets and abandons an overrun rather than waiting on it, so a
#: slow/hung rerank call would otherwise pin a slot in the shared default pool for as long as the
#: call keeps running -- degrading every unrelated `asyncio.to_thread` use in the process, not
#: just later rerank calls. Small and bounded on purpose, the same reasoning as the caller's own
#: pool: rerank is not a throughput path, and a caller wanting more concurrency raises this.
_ARERANK_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="egpt-reranker-arerank")


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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_ARERANK_EXECUTOR, self.rerank, query, documents)
