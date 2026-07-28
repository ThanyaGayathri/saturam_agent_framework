"""PR #16 review follow-up: does the weighted-vs-RRF fusion call still hold with a
**third, differently-scaled** leg (dense + BM25 + graph), or does RRF's rank-basis
win once a real graph-style scorer joins, as DECISION.md §1.1 theorizes?

The reviewer's concern: §3.1's fusion matrix is 2-leg (dense+BM25) only; production
retrieval is 3-leg per D8 (+graph, SM-G9). Recommendation: "measure 3-leg
weighted-vs-RRF against a real (even synthetic) graph leg before the RRF switch is
ratified."

There is no production graph retriever yet (SM-G9 is unbuilt) and the one real PPR
prototype in this repo (SM-R9's HippoRetriever, benchmarks/memory_multihop/) needs
LLM-based OpenIE to build its KG -- out of scope here (no Azure spend without
explicit sign-off, and it was evaluated on a different corpus/task). So this builds
a **lightweight, no-LLM graph leg**: a k-NN similarity graph, then real Personalized
PageRank (power iteration) -- a legitimate, real graph algorithm, not fabricated
scores. It is an honest proxy for testing the *fusion-math* question this decision
turns on (can weighted/min-max fusion handle a third, differently-scaled scorer, or
does RRF's rank-basis win) -- NOT a claim about real graph-topology retrieval
quality (that's SM-G9's job, on a real entity/citation graph).

PPR's score distribution is exactly the kind of "incomparable scale" the decision's
theoretical argument is about: heavily right-skewed / near-zero-for-most-nodes
(power-iteration diffusion), unlike bounded cosine or BM25's roughly-linear scores.

**Adversarial check (PR #16 review follow-up, round 2):** this project has twice
shipped benchmarks with baked-in circularity that only surfaced under review
(SM-R5's importance-oracle confound, SM-R6's clean-boundary bug). `GraphRetriever`
below builds its k-NN graph from the corpus' dense embeddings AND seeds PPR from
the dense retriever's own top hits -- so it could win against RRF for a boring
reason: it's just a smoothed copy of the dense leg, making "weighted absorbs a 3rd
leg better" trivially true because that leg barely disagrees with dense. To check
this isn't what's happening, `LexicalGraphRetriever` rebuilds the *entire* graph
leg -- k-NN edges AND PPR seeds -- from BM25/TF-IDF instead, so it shares no
construction path with the dense leg it's fused against. `_diagnose_overlap`
quantifies how correlated each graph variant's top-10 actually is with dense's.

Run: PYTHONPATH=. .venvs/smr8/bin/python -m benchmarks.memory_retrieval.graph_leg_experiment
"""

from __future__ import annotations

import json
import os

import numpy as np

from .data import Dataset, load_dataset
from .embcache import EmbeddingCache
from .fusion import RRFFusion, WeightedFusion
from .metrics import evaluate, paired_bootstrap
from .retrievers import BM25Retriever, DenseRetriever, Query, Retriever

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


class GraphRetriever(Retriever):
    """Personalized PageRank over a k-NN graph built from the corpus' own dense
    embeddings. Real power-iteration PPR (not a lookup table) -- an honest,
    no-LLM proxy for a graph leg's *score-scale* behavior, not its topology.
    """

    name = "graph-ppr"

    def __init__(
        self,
        doc_ids: list,
        doc_vectors: np.ndarray,
        k_neighbors: int = 16,
        damping: float = 0.85,
        n_iter: int = 40,
        seed_depth: int = 20,
    ):
        self._ids = doc_ids
        self._id_to_idx = {d: i for i, d in enumerate(doc_ids)}
        self.k_neighbors = k_neighbors
        self.damping = damping
        self.n_iter = n_iter
        self.seed_depth = seed_depth
        self._dense_for_seeds = DenseRetriever(doc_vectors, doc_ids)
        self._P = self._build_transition_matrix(doc_vectors, k_neighbors)

    @staticmethod
    def _build_transition_matrix(mat: np.ndarray, k: int) -> np.ndarray:
        n = mat.shape[0]
        # macOS Accelerate BLAS raises spurious FP-exception RuntimeWarnings on clean
        # unit-norm matmuls (see retrievers.py); suppress, don't silence the computation.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            sims = mat @ mat.T  # unit-normalized -> cosine; (n, n), one BLAS matmul
        np.fill_diagonal(sims, -np.inf)  # no self-loops
        # keep only the top-k neighbors per row (sparse-in-spirit, dense-in-storage
        # for simplicity at this n; scifact/nfcorpus scale is fine dense)
        idx = np.argpartition(-sims, k, axis=1)[:, :k]
        knn = np.full_like(sims, 0.0)
        rows = np.repeat(np.arange(n), k)
        knn[rows, idx.reshape(-1)] = np.clip(sims[rows, idx.reshape(-1)], 0.0, None)
        row_sums = knn.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        return (knn / row_sums).astype(np.float32)  # row-stochastic transition matrix

    def index(self, corpus) -> None:
        pass  # graph is built in __init__ from the shared embedding cache

    def search(self, query: Query, top_k: int) -> list:
        seeds = self._dense_for_seeds.search(query, self.seed_depth)
        n = len(self._ids)
        v0 = np.zeros(n, dtype=np.float32)
        for doc_id, _ in seeds:
            v0[self._id_to_idx[doc_id]] = 1.0
        v0 /= v0.sum()
        r = v0.copy()
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            for _ in range(self.n_iter):
                r = (1 - self.damping) * v0 + self.damping * (r @ self._P)
        k = min(top_k, n)
        idx = np.argpartition(-r, k - 1)[:k]
        idx = idx[np.argsort(-r[idx])]
        return [(self._ids[i], float(r[i])) for i in idx]


class LexicalGraphRetriever(Retriever):
    """Same PPR mechanics as `GraphRetriever`, but the k-NN graph edges AND the
    restart seeds are built entirely from BM25/TF-IDF -- zero shared construction
    path with the dense leg it gets fused against. Used as an adversarial control:
    if weighted-beats-RRF only held because the graph leg was secretly a smoothed
    copy of dense, it should stop holding (or hold much less) here.
    """

    name = "graph-ppr-lexical"

    def __init__(
        self,
        doc_ids: list,
        corpus_texts: list,
        bm25_for_seeds: BM25Retriever,
        k_neighbors: int = 16,
        damping: float = 0.85,
        n_iter: int = 40,
        seed_depth: int = 20,
    ):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._ids = doc_ids
        self._id_to_idx = {d: i for i, d in enumerate(doc_ids)}
        self.damping = damping
        self.n_iter = n_iter
        self.seed_depth = seed_depth
        self._bm25_for_seeds = bm25_for_seeds
        tfidf = TfidfVectorizer(max_features=20000, stop_words="english")
        mat = tfidf.fit_transform(corpus_texts).toarray().astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = (
            mat / norms
        )  # unit-normalize so mat @ mat.T is cosine, same as GraphRetriever
        self._P = GraphRetriever._build_transition_matrix(mat, k_neighbors)

    def index(self, corpus) -> None:
        pass

    def search(self, query: Query, top_k: int) -> list:
        seeds = self._bm25_for_seeds.search(query, self.seed_depth)
        n = len(self._ids)
        v0 = np.zeros(n, dtype=np.float32)
        for doc_id, _ in seeds:
            v0[self._id_to_idx[doc_id]] = 1.0
        v0 /= v0.sum()
        r = v0.copy()
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            for _ in range(self.n_iter):
                r = (1 - self.damping) * v0 + self.damping * (r @ self._P)
        k = min(top_k, n)
        idx = np.argpartition(-r, k - 1)[:k]
        idx = idx[np.argsort(-r[idx])]
        return [(self._ids[i], float(r[i])) for i in idx]


def _diagnose_overlap(dense, other, qids, qtexts, qmat, depth=10, sample=60):
    """Average top-`depth` doc-id overlap between `dense` and `other` over a query
    sample -- quantifies how correlated a graph variant actually is with dense.
    1.0 = identical rankings (circularity concern confirmed); ~0 = independent.
    """
    n = min(sample, len(qids))
    overlaps = []
    for j in range(n):
        query = Query(qid=qids[j], text=qtexts[j], vector=qmat[j])
        d_top = {doc_id for doc_id, _ in dense.search(query, depth)}
        o_top = {doc_id for doc_id, _ in other.search(query, depth)}
        overlaps.append(len(d_top & o_top) / depth)
    return float(np.mean(overlaps))


def run_dataset(dsname: str, cache: EmbeddingCache) -> dict:
    ds = load_dataset(dsname)
    task = ds.tasks[0]  # BEIR sets are single-task; keeps this experiment small/fast
    doc_ids = list(task.corpus.keys())
    doc_texts = [task.corpus[i] for i in doc_ids]
    dmat = cache.embed(f"{dsname}/{task.task_id}/docs", doc_texts)
    qids = list(task.queries.keys())
    qtexts = [task.queries[q] for q in qids]
    qmat = cache.embed(f"{dsname}/{task.task_id}/q", qtexts)

    dense = DenseRetriever(dmat, doc_ids)
    bm25 = BM25Retriever()
    bm25.index(task.corpus)
    graph = GraphRetriever(doc_ids, dmat)
    lex_graph = LexicalGraphRetriever(doc_ids, doc_texts, bm25)

    overlap_dense_graph = _diagnose_overlap(dense, graph, qids, qtexts, qmat)
    overlap_dense_lexgraph = _diagnose_overlap(dense, lex_graph, qids, qtexts, qmat)
    print(
        f"[{dsname}] top-10 overlap with dense -- graph(dense-derived): "
        f"{overlap_dense_graph:.3f}  graph(lexical, decorrelated): {overlap_dense_lexgraph:.3f}"
    )

    configs = {
        "2leg-rrf": (["dense", "bm25"], RRFFusion(k=60)),
        "2leg-weighted": (["dense", "bm25"], WeightedFusion(vector_weight=0.5)),
        "3leg-rrf": (["dense", "bm25", "graph"], RRFFusion(k=60)),
        "3leg-weighted": (["dense", "bm25", "graph"], WeightedFusion()),
        "3leg-rrf-lex": (["dense", "bm25", "lex_graph"], RRFFusion(k=60)),
        "3leg-weighted-lex": (["dense", "bm25", "lex_graph"], WeightedFusion()),
    }
    legs = {"dense": dense, "bm25": bm25, "graph": graph, "lex_graph": lex_graph}

    results: dict = {}
    per_query: dict = {}
    for cfg_name, (leg_names, fusion) in configs.items():
        run: dict = {}
        for j, qid in enumerate(qids):
            query = Query(qid=qid, text=qtexts[j], vector=qmat[j])
            lists = [legs[ln].search(query, 100) for ln in leg_names]
            fused = fusion.fuse(lists, top_k=100)
            run[qid] = {
                doc_id: float(len(fused) - i) for i, (doc_id, _s) in enumerate(fused)
            }
        one_task_ds = Dataset(
            name=dsname, tasks=[task], has_time=False, label_source=""
        )
        m = evaluate(one_task_ds, {task.task_id: run})
        results[cfg_name] = m.mean("ndcg_cut_10")
        per_query[cfg_name] = m.per_query["ndcg_cut_10"]
        print(f"[{dsname}] {cfg_name}: nDCG@10={results[cfg_name]:.4f}")

    sig = {}
    for a, b in [
        ("3leg-weighted", "3leg-rrf"),
        ("2leg-weighted", "2leg-rrf"),
        ("3leg-weighted-lex", "3leg-rrf-lex"),
    ]:
        pb = paired_bootstrap(per_query[a], per_query[b], seed=7)
        sig[f"{a} vs {b}"] = pb
        print(
            f"[{dsname}] {a} vs {b}: delta={pb['delta']:+.4f} CI=[{pb['lo']:+.4f},{pb['hi']:+.4f}] "
            f"p={pb['p_value']:.3f} ranked={pb['significant']}"
        )
    return {
        "ndcg": results,
        "significance": sig,
        "dense_overlap": {
            "graph_dense_derived": overlap_dense_graph,
            "graph_lexical_decorrelated": overlap_dense_lexgraph,
        },
    }


def main():
    from .run import build_embedder

    emb, model = build_embedder("local")
    cache = EmbeddingCache(emb, model)
    out = {}
    for dsname in ["beir/scifact"]:
        out[dsname] = run_dataset(dsname, cache)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, "graph_leg_experiment.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
