"""Cross-encoder rerank stage -- wraps the real egpt `Reranker` base class.

We do NOT reimplement reranking: `EgptRerankStage` adapts any egpt `Reranker`
(the self-hosted `SentenceTransformerReranker` / `InfinityReranker` for the
air-gapped Mode A, or the provider `CohereReranker` for Mode B) to the pipeline's
`(query, candidates, corpus) -> ranked` shape. This is the reuse-first stance: the
concrete reranker is selected by config, call sites never import a specific one.

egpt's base `Reranker.rerank` is sync-only (only `InfinityReranker` ships `arerank`),
so `arerank` here uses the wrapped reranker's async method when present and otherwise
runs the sync path in a thread -- giving the pipeline a true async variant without
modifying core.

`build_reranker` is the config-driven factory (offline first).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple

from egpt.knowledge.document import Document
from egpt.knowledge.reranker.base import Reranker

Scored = Tuple[str, float]


class RerankStage(ABC):
    name: str = "rerank"

    @abstractmethod
    def rerank(
        self, query: str, candidates: List[Scored], corpus: Dict[str, str], top_k: int
    ) -> List[Scored]: ...

    async def arerank(
        self, query: str, candidates: List[Scored], corpus: Dict[str, str], top_k: int
    ) -> List[Scored]:
        return await asyncio.to_thread(self.rerank, query, candidates, corpus, top_k)


class EgptRerankStage(RerankStage):
    """Adapts an egpt `Reranker` (any offline/provider adapter) to the pipeline."""

    def __init__(self, reranker: Reranker, label: str):
        self.reranker = reranker
        self.name = f"rerank[{label}]"

    def _docs(self, candidates: List[Scored], corpus: Dict[str, str]) -> List[Document]:
        return [
            Document(id=doc_id, content=corpus.get(doc_id, ""))
            for doc_id, _ in candidates
        ]

    def rerank(
        self, query: str, candidates: List[Scored], corpus: Dict[str, str], top_k: int
    ) -> List[Scored]:
        if not candidates:
            return []
        docs = self._docs(candidates, corpus)
        reranked = self.reranker.rerank(query=query, documents=docs)
        out = [
            (d.id, float(d.reranking_score if d.reranking_score is not None else 0.0))
            for d in reranked
        ]
        return out[:top_k]

    async def arerank(
        self, query: str, candidates: List[Scored], corpus: Dict[str, str], top_k: int
    ) -> List[Scored]:
        afn = getattr(self.reranker, "arerank", None)
        if afn is None:
            return await super().arerank(query, candidates, corpus, top_k)
        docs = self._docs(candidates, corpus)
        reranked = await afn(query=query, documents=docs)
        out = [
            (d.id, float(d.reranking_score if d.reranking_score is not None else 0.0))
            for d in reranked
        ]
        return out[:top_k]


# --------------------------------------------------------------------------- #
# Factory (offline first)
# --------------------------------------------------------------------------- #
# Small, fast CPU-friendly cross-encoder (Mode A, air-gapped) vs the larger,
# higher-quality multilingual one egpt defaults to. Both are self-hostable.
RERANKER_MODELS = {
    "st-minilm": ("sentence_transformer", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    "st-bge-m3": ("sentence_transformer", "BAAI/bge-reranker-v2-m3"),
    "cohere": ("cohere", "rerank-v3.5"),
    "infinity": ("infinity", "BAAI/bge-reranker-base"),
}


def build_reranker(name: str, **kwargs) -> EgptRerankStage:
    kind, model = RERANKER_MODELS[name]
    if kind == "sentence_transformer":
        from egpt.knowledge.reranker.sentence_transformer import (
            SentenceTransformerReranker,
        )

        # egpt's adapter constructs CrossEncoder with model_name_or_path=/model_kwargs=
        # (sentence-transformers >= 4.0). This Intel-Mac measurement host caps torch at
        # 2.2.2 -> ST 3.4.1, whose CrossEncoder API differs. We inject a
        # positionally-built CrossEncoder (portable across ST versions) via the
        # adapter's own `cross_encoder` field, so egpt's rerank logic runs unmodified
        # and core stays untouched. See findings/reranker-st-version-seam.md.
        from sentence_transformers import CrossEncoder

        # Force CPU: this Intel host's MPS pool OOMs on the larger cross-encoders
        # (bge-reranker-v2-m3) with long docs, and MPS latency isn't representative of the
        # air-gapped CPU/GPU serving target. Positional model arg is stable across ST versions.
        ce = CrossEncoder(model, device="cpu")
        return EgptRerankStage(
            SentenceTransformerReranker(model=model, cross_encoder=ce), label=name
        )
    if kind == "cohere":
        from egpt.knowledge.reranker.cohere import CohereReranker

        return EgptRerankStage(CohereReranker(model=model, **kwargs), label=name)
    if kind == "infinity":
        from egpt.knowledge.reranker.infinity import InfinityReranker

        return EgptRerankStage(InfinityReranker(model=model, **kwargs), label=name)
    raise ValueError(f"unknown reranker {name!r}")
