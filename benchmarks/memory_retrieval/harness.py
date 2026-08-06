"""Run one (dataset x option) case -> quality metrics + latency, reproducibly.

An `Option` is a named pipeline configuration (a-e, plus parameter variants). The
harness embeds each dataset's corpus/queries once (cached to disk, shared across all
options), builds fresh per-task retrievers, times the retrieval stages per query, and
scores with pytrec_eval. The cross-encoder reranker is built once and reused across
tasks (never reloaded in the loop).

Latency: we time the search stages (retrieve -> fuse -> rerank -> composite) per query.
Query-embedding latency is option-invariant (every option embeds the query once) and is
excluded so the numbers isolate what the decision is about. All latencies are on this
Intel-Mac CPU and are labelled indicative in the write-up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .data import Dataset, RetrievalTask
from .embcache import EmbeddingCache
from .fusion import RRFFusion, WeightedFusion
from .metrics import MetricResult, evaluate
from .pipeline import PipelineConfig, RetrievalPipeline
from .rerank import RerankStage
from .retrievers import BM25Retriever, DenseRetriever, Query
from .scoring import CompositeScorer


@dataclass
class Option:
    """A named pipeline configuration. `reranker` is a prebuilt, reused stage."""

    name: str
    use_bm25: bool = True
    fusion_kind: str = "rrf"  # rrf | weighted | none
    rrf_k: int = 60
    vector_weight: float = 0.5
    reranker: Optional[RerankStage] = None
    composite: Optional[CompositeScorer] = None
    config: PipelineConfig = field(default_factory=PipelineConfig)

    def build(
        self, dense: DenseRetriever, bm25: Optional[BM25Retriever]
    ) -> RetrievalPipeline:
        retrievers = [dense] + ([bm25] if (self.use_bm25 and bm25 is not None) else [])
        fusion = None
        if len(retrievers) > 1:
            if self.fusion_kind == "rrf":
                fusion = RRFFusion(self.rrf_k)
            elif self.fusion_kind == "weighted":
                fusion = WeightedFusion(vector_weight=self.vector_weight)
            else:
                raise ValueError(
                    f"multiple retrievers need a fusion; got {self.fusion_kind}"
                )
        return RetrievalPipeline(
            retrievers=retrievers,
            fusion=fusion,
            reranker=self.reranker,
            composite=self.composite,
            config=self.config,
            name=self.name,
        )


@dataclass
class CaseResult:
    dataset: str
    option: str
    metrics: MetricResult
    latency_ms: List[float]
    n_queries: int

    def summary(self) -> Dict[str, float]:
        lat = np.array(self.latency_ms) if self.latency_ms else np.array([0.0])
        return {
            "ndcg@10": self.metrics.mean("ndcg_cut_10"),
            "recall@10": self.metrics.mean("recall_10"),
            "recall@50": self.metrics.mean("recall_50"),
            "recall@100": self.metrics.mean("recall_100"),
            "mrr": self.metrics.mean("recip_rank"),
            "lat_p50_ms": float(np.percentile(lat, 50)),
            "lat_p95_ms": float(np.percentile(lat, 95)),
            "n": self.n_queries,
        }


def run_case(
    dataset: Dataset,
    option: Option,
    cache: EmbeddingCache,
    max_tasks: Optional[int] = None,
    max_queries: Optional[int] = None,
) -> CaseResult:
    tasks = dataset.tasks if max_tasks is None else dataset.tasks[:max_tasks]
    run_by_task: Dict[str, Dict[str, Dict[str, float]]] = {}
    latencies: List[float] = []
    n_queries = 0

    for task in tasks:
        doc_ids = list(task.corpus.keys())
        doc_texts = [task.corpus[i] for i in doc_ids]
        dmat = cache.embed(f"{dataset.name}/{task.task_id}/docs", doc_texts)

        qids = list(task.queries.keys())
        qtexts = [task.queries[q] for q in qids]
        qmat = cache.embed(f"{dataset.name}/{task.task_id}/q", qtexts)
        if max_queries is not None and len(qids) > max_queries:
            qids = qids[:max_queries]
            qtexts = qtexts[:max_queries]
            qmat = qmat[:max_queries]

        dense = DenseRetriever()
        dense.set_matrix(doc_ids, dmat)
        bm25 = BM25Retriever() if option.use_bm25 else None
        if bm25 is not None:
            bm25.index(task.corpus)
        pipe = option.build(dense, bm25)

        task_run: Dict[str, Dict[str, float]] = {}
        for j, qid in enumerate(qids):
            qtime = task.query_times.get(qid) if task.query_times else None
            query = Query(qid=qid, text=qtexts[j], vector=qmat[j])
            t0 = time.perf_counter()
            scored = pipe.search(
                query, task.corpus, doc_times=task.doc_times, query_time=qtime
            )
            latencies.append((time.perf_counter() - t0) * 1000.0)
            task_run[qid] = pipe.to_run(scored)
            n_queries += 1
        run_by_task[task.task_id] = task_run

    metrics = evaluate(
        dataset if max_tasks is None else _slice(dataset, tasks), run_by_task
    )
    return CaseResult(dataset.name, option.name, metrics, latencies, n_queries)


def _slice(dataset: Dataset, tasks: List[RetrievalTask]) -> Dataset:
    return Dataset(
        name=dataset.name,
        tasks=tasks,
        has_time=dataset.has_time,
        label_source=dataset.label_source,
        notes=dataset.notes,
    )
