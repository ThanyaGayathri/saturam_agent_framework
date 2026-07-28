"""The retrieval abstraction the spike is built around.

``MultiHopRetriever`` is the plug-and-play seam: the harness only ever calls
``index`` / ``retrieve`` (and their async twins) and never knows which strategy it
is driving. Three adapters implement it:

  - ``VectorRetriever``  — the dense baseline (no LLM at index or query time).
  - ``HippoRetriever``   — HippoRAG-style OpenIE -> KG -> Personalized PageRank.
  - ``HybridRetriever``  — RRF fusion of the two (the research doc's default fuse).

Because all three share the seam, switching strategy in ``run.py`` is a one-word
config change, and a future adapter over the SM-G temporal graph (Neo4j/GDS PPR)
drops in the same way with no call-site edits.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from harness.dataset import Passage
from harness.embeddings import CachedEmbedder, TextEmbedder
from harness.fusion import Fusion as FusionStrategy
from harness.fusion import RRFFusion
from harness.kg import KnowledgeGraph
from harness.llm import LLMClient, Usage
from harness.openie import (
    aextract_query_entities,
    aextract_triples,
    extract_query_entities,
    extract_triples,
)


@dataclass
class Retrieved:
    pid: str
    title: str
    score: float


@dataclass
class IndexStats:
    """Write-side cost of building the index (the thing SM-R9 must weigh)."""

    n_passages: int = 0
    n_entities: int = 0
    n_edges: int = 0
    llm: Usage = field(default_factory=Usage)
    embed_calls: int = 0
    index_seconds: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "n_passages": self.n_passages,
            "n_entities": self.n_entities,
            "n_edges": self.n_edges,
            "llm": self.llm.to_dict(),
            "embed_calls": self.embed_calls,
            "index_seconds": round(self.index_seconds, 2),
        }


def _passage_repr(p: Passage) -> str:
    return f"{p.title}. {p.text}"


class MultiHopRetriever:
    """Abstract retrieval seam. Both sync and async variants are required."""

    name = "base"

    def index(self, passages: Sequence[Passage]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def aindex(self, passages: Sequence[Passage]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def retrieve(self, question: str, k: int) -> List[Retrieved]:  # pragma: no cover - interface
        raise NotImplementedError

    async def aretrieve(self, question: str, k: int) -> List[Retrieved]:  # pragma: no cover
        raise NotImplementedError

    @property
    def stats(self) -> IndexStats:  # pragma: no cover - interface
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Dense baseline
# --------------------------------------------------------------------------- #
class VectorRetriever(MultiHopRetriever):
    name = "vector"

    def __init__(self, embedder: Optional[TextEmbedder] = None) -> None:
        self.embedder = embedder or CachedEmbedder()
        self._passages: List[Passage] = []
        self._matrix: Optional[np.ndarray] = None
        self._stats = IndexStats()

    def index(self, passages: Sequence[Passage]) -> None:
        t0 = time.perf_counter()
        self._passages = list(passages)
        self._matrix = self.embedder.embed([_passage_repr(p) for p in self._passages])
        self._finish_stats(t0)

    async def aindex(self, passages: Sequence[Passage]) -> None:
        t0 = time.perf_counter()
        self._passages = list(passages)
        self._matrix = await self.embedder.aembed([_passage_repr(p) for p in self._passages])
        self._finish_stats(t0)

    def _finish_stats(self, t0: float) -> None:
        self._stats.n_passages = len(self._passages)
        self._stats.embed_calls = getattr(self.embedder, "calls", 0)
        self._stats.index_seconds = time.perf_counter() - t0

    def _rank(self, qvec: np.ndarray, k: int) -> List[Retrieved]:
        assert self._matrix is not None, "index() first"
        # macOS Accelerate's BLAS raises spurious divide/overflow/invalid RuntimeWarnings
        # from this matmul. Verified benign before trusting the re-measured numbers:
        # inputs and outputs are all-finite, row norms are exactly 1.0, and the BLAS
        # result matches an independent np.einsum path to 1.8e-15. Same issue and same
        # disposition as SM-R8 (e2cd21d).
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            sims = self._matrix @ qvec
        assert np.isfinite(sims).all(), "non-finite similarities — this warning is NOT benign"
        order = np.argsort(-sims)[:k]
        return [Retrieved(self._passages[i].pid, self._passages[i].title, float(sims[i])) for i in order]

    def retrieve(self, question: str, k: int) -> List[Retrieved]:
        qvec = self.embedder.embed([question])[0]
        return self._rank(qvec, k)

    async def aretrieve(self, question: str, k: int) -> List[Retrieved]:
        qvec = (await self.embedder.aembed([question]))[0]
        return self._rank(qvec, k)

    @property
    def stats(self) -> IndexStats:
        return self._stats


# --------------------------------------------------------------------------- #
# HippoRAG-style PPR
# --------------------------------------------------------------------------- #
class HippoRetriever(MultiHopRetriever):
    """HippoRAG-style OpenIE -> KG -> Personalized PageRank.

    ``synonym_tau`` and ``link_threshold`` are **absolute cosine thresholds and are
    therefore embedder-specific** — they were tuned against `text-embedding-3-small`
    and do not port unchanged to SM-R4's ratified embedder. Measured on this corpus
    with `bge-base-en-v1.5`@768 (285 passage titles as a proxy entity-name set, 40,470
    pairs): median off-diagonal cosine 0.379, p99 0.553, so tau=0.85 sits far out in
    the tail and admits just 12 pairs (0.03%). A threshold that far into the tail is
    highly sensitive to the embedder's cosine scale, so SM-H4 must re-tune both values
    per embedder rather than inherit them.
    """

    name = "hippo"

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        embedder: Optional[TextEmbedder] = None,
        alpha: float = 0.5,
        synonym_tau: float = 0.85,
        link_threshold: float = 0.5,
        link_top_m: int = 3,
    ) -> None:
        self.llm = llm or LLMClient()
        self.embedder = embedder or CachedEmbedder()
        self.alpha = alpha
        self.synonym_tau = synonym_tau
        self.link_threshold = link_threshold
        self.link_top_m = link_top_m
        self.kg = KnowledgeGraph()
        self._pid_to_title: Dict[str, str] = {}
        self._entity_keys: List[str] = []
        self._entity_matrix: Optional[np.ndarray] = None
        self._stats = IndexStats()

    # ------------------------------------------------------------- indexing
    def _embed_entities(self) -> Dict[str, np.ndarray]:
        keys = self.kg.entity_keys()
        displays = [self.kg.entity_display[k] for k in keys]
        mat = self.embedder.embed(displays) if displays else np.zeros((0, 1))
        self._entity_keys = keys
        self._entity_matrix = mat
        return {k: mat[i] for i, k in enumerate(keys)}

    def _finish_index(self, t0: float, ent_vecs: Dict[str, np.ndarray]) -> None:
        self.kg.finalize(ent_vecs, synonym_tau=self.synonym_tau)
        self._stats.n_passages = self.kg.n_passages
        self._stats.n_entities = len(self._entity_keys)
        self._stats.n_edges = self.kg.graph.number_of_edges()
        self._stats.embed_calls = getattr(self.embedder, "calls", 0)
        self._stats.index_seconds = time.perf_counter() - t0

    def index(self, passages: Sequence[Passage]) -> None:
        t0 = time.perf_counter()
        for p in passages:
            self._pid_to_title[p.pid] = p.title
            triples = extract_triples(_passage_repr(p), self.llm, self._stats.llm)
            self.kg.add_passage(p.pid, triples)
        ent_vecs = self._embed_entities()
        self._finish_index(t0, ent_vecs)

    async def aindex(self, passages: Sequence[Passage]) -> None:
        t0 = time.perf_counter()
        for p in passages:
            self._pid_to_title[p.pid] = p.title
            triples = await aextract_triples(_passage_repr(p), self.llm, self._stats.llm)
            self.kg.add_passage(p.pid, triples)
        keys = self.kg.entity_keys()
        displays = [self.kg.entity_display[k] for k in keys]
        mat = await self.embedder.aembed(displays) if displays else np.zeros((0, 1))
        self._entity_keys, self._entity_matrix = keys, mat
        ent_vecs = {k: mat[i] for i, k in enumerate(keys)}
        self._finish_index(t0, ent_vecs)

    # ------------------------------------------------------------ retrieval
    def _seed_from_entities(self, q_entities: Sequence[str], q_vecs: np.ndarray) -> Dict[str, float]:
        """Link query entities to KG nodes by embedding cosine; weight by cosine*IDF."""
        seed: Dict[str, float] = {}
        if self._entity_matrix is None or self._entity_matrix.shape[0] == 0:
            return seed
        for qi in range(len(q_entities)):
            sims = self._entity_matrix @ q_vecs[qi]
            order = np.argsort(-sims)[: self.link_top_m]
            for idx in order:
                if sims[idx] < self.link_threshold:
                    continue
                key = self._entity_keys[idx]
                weight = float(sims[idx]) * (self.kg.idf(key) or 1.0)
                seed[key] = max(seed.get(key, 0.0), weight)
        return seed

    def _finalize_ranks(self, seed: Dict[str, float], k: int) -> List[Retrieved]:
        ppr = self.kg.personalized_pagerank(seed, alpha=self.alpha)
        ranked = self.kg.rank_passages(ppr, k)
        return [Retrieved(pid, self._pid_to_title.get(pid, pid), score) for pid, score in ranked]

    def retrieve(self, question: str, k: int) -> List[Retrieved]:
        q_entities = extract_query_entities(question, self.llm, self._stats.llm) or [question]
        q_vecs = self.embedder.embed(q_entities)
        seed = self._seed_from_entities(q_entities, q_vecs)
        if not seed:
            # graceful degradation: seed on nearest entities to the whole question
            seed = self._seed_from_entities([question], self.embedder.embed([question]))
        return self._finalize_ranks(seed, k)

    async def aretrieve(self, question: str, k: int) -> List[Retrieved]:
        q_entities = await aextract_query_entities(question, self.llm, self._stats.llm) or [question]
        q_vecs = await self.embedder.aembed(q_entities)
        seed = self._seed_from_entities(q_entities, q_vecs)
        if not seed:
            seed = self._seed_from_entities([question], await self.embedder.aembed([question]))
        return self._finalize_ranks(seed, k)

    @property
    def stats(self) -> IndexStats:
        return self._stats


# --------------------------------------------------------------------------- #
# Hybrid (dense + PPR) — fusion strategy is configurable
# --------------------------------------------------------------------------- #
class HybridRetriever(MultiHopRetriever):
    """Fuses the dense and PPR legs. **Fusion method is a measured choice, not a default.**

    SM-R8 (`benchmarks/memory_retrieval/DECISION.md`) ratified *weighted* / relative-score
    fusion for its 2-leg case and explicitly declined to adopt RRF automatically once a
    graph leg joins. SM-R9 originally shipped RRF only; both are now measurable here via
    `harness/fusion.py` (ported from SM-R8 so the comparison uses the same code).

    The scale mismatch is the crux: the dense leg emits cosine similarities (flat, ~0.15-0.87)
    while the PPR leg emits stationary probabilities (tiny and extremely skewed). RRF sees
    only ranks and throws that away; weighted min-max keeps it.
    """

    name = "hybrid"

    def __init__(
        self,
        vector: VectorRetriever,
        hippo: HippoRetriever,
        rrf_k: int = 60,
        fusion: Optional[FusionStrategy] = None,
    ) -> None:
        self.vector = vector
        self.hippo = hippo
        self.rrf_k = rrf_k
        # Default stays RRF so previously committed results remain reproducible.
        self.fusion = fusion if fusion is not None else RRFFusion(k=rrf_k)

    def index(self, passages: Sequence[Passage]) -> None:
        self.vector.index(passages)
        self.hippo.index(passages)

    async def aindex(self, passages: Sequence[Passage]) -> None:
        await self.vector.aindex(passages)
        await self.hippo.aindex(passages)

    def _fuse(self, a: List[Retrieved], b: List[Retrieved], k: int) -> List[Retrieved]:
        titles: Dict[str, str] = {r.pid: r.title for r in list(a) + list(b)}
        # Leg order is (dense, ppr) - WeightedFusion's `vector_weight` applies to the first.
        fused = self.fusion.fuse([[(r.pid, r.score) for r in a], [(r.pid, r.score) for r in b]], k)
        return [Retrieved(pid, titles[pid], score) for pid, score in fused]

    def retrieve(self, question: str, k: int) -> List[Retrieved]:
        pool = max(k, 10)
        return self._fuse(self.vector.retrieve(question, pool), self.hippo.retrieve(question, pool), k)

    async def aretrieve(self, question: str, k: int) -> List[Retrieved]:
        pool = max(k, 10)
        a = await self.vector.aretrieve(question, pool)
        b = await self.hippo.aretrieve(question, pool)
        return self._fuse(a, b, k)

    @property
    def stats(self) -> IndexStats:
        return self.hippo.stats
