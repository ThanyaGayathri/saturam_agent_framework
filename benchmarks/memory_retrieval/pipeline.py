"""Config-driven retrieval pipeline -- the plug-and-play seam.

The five options the issue asks us to evaluate are all just configurations of this
one pipeline; nothing downstream changes when the fusion / reranker / scorer swaps:

    (a) vector-only        retrievers=[dense]
    (b) hybrid + RRF       retrievers=[dense, bm25], fusion=RRFFusion()
    (c) weighted fusion    retrievers=[dense, bm25], fusion=WeightedFusion()
    (d) hybrid + rerank    (b) + reranker=EgptRerankStage(...)
    (e) composite time     (b) + composite=CompositeScorer(...)

Stages run: retrieve (depth) -> fuse -> [rerank top rerank_depth] -> [composite] -> top_k.
Both sync (`search`) and async (`asearch`) are implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .fusion import Fusion
from .rerank import RerankStage
from .retrievers import Query, Retriever, Scored
from .scoring import CompositeScorer


@dataclass
class PipelineConfig:
    retrieve_depth: int = 100
    rerank_depth: int = 50
    final_k: int = 100  # keep deep enough to score recall@100; metrics slice by k


@dataclass
class RetrievalPipeline:
    retrievers: List[Retriever]
    fusion: Optional[Fusion] = None
    reranker: Optional[RerankStage] = None
    composite: Optional[CompositeScorer] = None
    config: PipelineConfig = field(default_factory=PipelineConfig)
    name: str = "pipeline"

    def _fuse(self, query: Query, lists: List[List[Scored]]) -> List[Scored]:
        if len(lists) == 1 and self.fusion is None:
            return lists[0]
        if self.fusion is None:
            raise RuntimeError("multiple retrievers require a fusion strategy")
        return self.fusion.fuse(
            lists, top_k=max(self.config.retrieve_depth, self.config.final_k)
        )

    def search(
        self,
        query: Query,
        corpus: Dict[str, str],
        doc_times: Optional[Dict[str, float]] = None,
        query_time: Optional[float] = None,
    ) -> List[Scored]:
        lists = [r.search(query, self.config.retrieve_depth) for r in self.retrievers]
        fused = self._fuse(query, lists)
        result = fused
        if self.reranker is not None:
            result = self.reranker.rerank(
                query.text,
                fused[: self.config.rerank_depth],
                corpus,
                self.config.final_k,
            )
        if self.composite is not None:
            result = self.composite.rescore(
                result[: self.config.rerank_depth] if self.reranker is None else result,
                top_k=self.config.final_k,
                doc_times=doc_times,
                query_time=query_time,
            )
        return result[: self.config.final_k]

    async def asearch(
        self,
        query: Query,
        corpus: Dict[str, str],
        doc_times: Optional[Dict[str, float]] = None,
        query_time: Optional[float] = None,
    ) -> List[Scored]:
        lists = [
            await r.asearch(query, self.config.retrieve_depth) for r in self.retrievers
        ]
        fused = self._fuse(query, lists)
        result = fused
        if self.reranker is not None:
            result = await self.reranker.arerank(
                query.text,
                fused[: self.config.rerank_depth],
                corpus,
                self.config.final_k,
            )
        if self.composite is not None:
            result = self.composite.rescore(
                result[: self.config.rerank_depth] if self.reranker is None else result,
                top_k=self.config.final_k,
                doc_times=doc_times,
                query_time=query_time,
            )
        return result[: self.config.final_k]

    def to_run(self, scored: List[Scored]) -> Dict[str, float]:
        """Convert ranked list to a pytrec_eval run row {doc_id: score}, preserving order."""
        n = len(scored)
        return {doc_id: float(n - i) for i, (doc_id, _s) in enumerate(scored)}
