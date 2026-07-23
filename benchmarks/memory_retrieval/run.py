"""CLI: sweep the retrieval options across datasets, save results + summary.

Options (issue's "Options to evaluate"):
    a  vector-only baseline           (dense)
    b  hybrid + RRF                    (dense + bm25, RRF k=60)
    c  hybrid + weighted fusion        (dense + bm25, vector_weight=0.5)
    d  hybrid + RRF + cross-encoder    (b + self-hosted reranker, depth 50 -> 10)
    e  hybrid + RRF + composite time   (b + relevance+recency rescoring)

Usage:
    python -m benchmarks.memory_retrieval.run \
        --datasets beir/scifact beir/nfcorpus beir/fiqa longmemeval/longmemeval_s \
        --options a b c d e --reranker st-minilm --seed 7

Results (raw JSON + markdown) land in results/ tagged with a UTC timestamp + tag.
Each case is written as it finishes so a long run's partial results persist.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from dotenv import load_dotenv

from .data import load_dataset
from .embcache import EmbeddingCache
from .harness import Option, run_case
from .metrics import paired_bootstrap
from .pipeline import PipelineConfig
from .scoring import CompositeScorer

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def _configure_threads() -> None:
    """CPU cross-encoder inference is thread-bound; use all physical cores unless
    the caller overrode OMP_NUM_THREADS."""
    try:
        import torch

        n = int(os.environ.get("OMP_NUM_THREADS", "0")) or (os.cpu_count() or 1)
        torch.set_num_threads(n)
    except Exception:
        pass


def build_embedder(kind: str, model: Optional[str] = None):
    """Dense embedder behind the egpt `Embedder` abstraction.

    - `local`  : egpt `SentenceTransformerEmbedder` (offline, no rate limits, matches
                 air-gapped Mode A). Default.
    - `azure`  : egpt `AzureOpenAIEmbedder` (Mode B). Usable for the smaller BEIR sets;
                 the deployment's TPM limit makes the long-doc LongMemEval set impractical.
    """
    if kind == "azure":
        load_dotenv(".env")
        from egpt.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        model = model or "text-embedding-3-small"
        emb = AzureOpenAIEmbedder(
            id=model,
            azure_deployment=model,
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_EMB_API_VERSION", "2024-10-21"),
            client_params={"timeout": 30.0, "max_retries": 5},
        )
        return emb, f"azure:{model}"
    if kind == "local":
        from sentence_transformers import SentenceTransformer

        from egpt.knowledge.embedder.sentence_transformer import (
            SentenceTransformerEmbedder,
        )

        model = model or "sentence-transformers/all-MiniLM-L6-v2"
        # Force CPU: this Intel host's MPS pool OOMs on long LongMemEval session docs.
        st = SentenceTransformer(model, device="cpu")
        emb = SentenceTransformerEmbedder(
            id=model, sentence_transformer_client=st, normalize_embeddings=True
        )
        emb.dimensions = st.get_sentence_embedding_dimension()
        emb.batch_size = 32
        return emb, f"local:{model.split('/')[-1]}"
    raise ValueError(f"unknown embedder {kind!r}")


def build_options(
    kinds: List[str],
    reranker_name: str,
    cfg: PipelineConfig,
    rrf_k: int,
    vector_weight: float,
    recency_weight: float,
) -> List[Option]:
    reranker = None
    if "d" in kinds:
        from .rerank import build_reranker

        reranker = build_reranker(reranker_name)
    opts: List[Option] = []
    for k in kinds:
        if k == "a":
            opts.append(
                Option("a-vector", use_bm25=False, fusion_kind="none", config=cfg)
            )
        elif k == "b":
            opts.append(Option("b-rrf", fusion_kind="rrf", rrf_k=rrf_k, config=cfg))
        elif k == "c":
            opts.append(
                Option(
                    "c-weighted",
                    fusion_kind="weighted",
                    vector_weight=vector_weight,
                    config=cfg,
                )
            )
        elif k == "d":
            opts.append(
                Option(
                    f"d-rerank[{reranker_name}]",
                    fusion_kind="rrf",
                    rrf_k=rrf_k,
                    reranker=reranker,
                    config=cfg,
                )
            )
        elif k == "e":
            comp = CompositeScorer(
                w_relevance=1.0, w_recency=recency_weight, w_importance=0.0
            )
            opts.append(
                Option(
                    "e-composite",
                    fusion_kind="rrf",
                    rrf_k=rrf_k,
                    composite=comp,
                    config=cfg,
                )
            )
        else:
            raise ValueError(f"unknown option {k!r}")
    return opts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--options", nargs="+", default=["a", "b", "c", "d", "e"])
    ap.add_argument("--reranker", default="st-minilm")
    ap.add_argument("--embedder", default="local", choices=["local", "azure"])
    ap.add_argument("--emb-model", default=None, help="override embedder model id")
    ap.add_argument("--retrieve-depth", type=int, default=100)
    ap.add_argument("--rerank-depth", type=int, default=50)
    ap.add_argument("--final-k", type=int, default=100)
    ap.add_argument("--rrf-k", type=int, default=60)
    ap.add_argument("--vector-weight", type=float, default=0.5)
    ap.add_argument("--recency-weight", type=float, default=0.5)
    ap.add_argument("--max-tasks", type=int, default=None)
    ap.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="cap queries per task (for bounded rerank/sweep runs)",
    )
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tag", default="matrix")
    args = ap.parse_args()

    _configure_threads()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"{stamp}_{args.tag}"

    embedder, model = build_embedder(args.embedder, args.emb_model)
    cache = EmbeddingCache(embedder, model)
    cfg = PipelineConfig(
        retrieve_depth=args.retrieve_depth,
        rerank_depth=args.rerank_depth,
        final_k=args.final_k,
    )
    options = build_options(
        args.options,
        args.reranker,
        cfg,
        args.rrf_k,
        args.vector_weight,
        args.recency_weight,
    )

    meta = {
        "tag": tag,
        "seed": args.seed,
        "embedding_model": model,
        "reranker": args.reranker,
        "retrieve_depth": args.retrieve_depth,
        "rerank_depth": args.rerank_depth,
        "rrf_k": args.rrf_k,
        "vector_weight": args.vector_weight,
        "recency_weight": args.recency_weight,
        "max_tasks": args.max_tasks,
        "max_queries": args.max_queries,
        "host": platform.platform(),
        "machine": platform.machine(),
        "note": "latencies are Intel-Mac CPU, indicative; query-embed latency excluded (option-invariant)",
    }
    results: Dict[str, Dict[str, dict]] = {}
    per_query_store: Dict[str, Dict[str, Dict[str, float]]] = {}
    json_path = os.path.join(RESULTS_DIR, f"results_{tag}.json")

    for dsname in args.datasets:
        ds = load_dataset(dsname)
        results.setdefault(dsname, {})
        per_query_store.setdefault(dsname, {})
        for opt in options:
            t0 = time.perf_counter()
            case = run_case(
                ds, opt, cache, max_tasks=args.max_tasks, max_queries=args.max_queries
            )
            elapsed = time.perf_counter() - t0
            summ = case.summary()
            results[dsname][opt.name] = {"summary": summ, "wall_s": round(elapsed, 1)}
            per_query_store[dsname][opt.name] = case.metrics.per_query["ndcg_cut_10"]
            print(
                f"[{dsname}] {opt.name}: nDCG@10={summ['ndcg@10']:.4f} "
                f"R@10={summ['recall@10']:.4f} R@100={summ['recall@100']:.4f} "
                f"MRR={summ['mrr']:.4f} p50={summ['lat_p50_ms']:.1f}ms p95={summ['lat_p95_ms']:.1f}ms "
                f"(n={summ['n']}, {elapsed:.0f}s)"
            )
            # persist after each case
            with open(json_path, "w") as fh:
                json.dump({"meta": meta, "results": results}, fh, indent=2)

    _write_summary(tag, meta, results, per_query_store)
    print(f"\nWrote {json_path}")


def _write_summary(tag: str, meta: dict, results: dict, per_query: dict) -> None:
    lines: List[str] = [f"# SM-R8 results — {tag}", ""]
    lines.append(
        f"- embedding model: `{meta['embedding_model']}` | reranker: `{meta['reranker']}` "
        f"| RRF k={meta['rrf_k']} | vector_weight={meta['vector_weight']} "
        f"| rerank_depth={meta['rerank_depth']}"
    )
    lines.append(f"- host: {meta['host']} ({meta['machine']}); {meta['note']}")
    lines.append("")
    for dsname, byopt in results.items():
        lines.append(f"## {dsname}")
        lines.append("")
        lines.append(
            "| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for opt, row in byopt.items():
            s = row["summary"]
            lines.append(
                f"| {opt} | {s['ndcg@10']:.4f} | {s['recall@10']:.4f} | {s['recall@50']:.4f} "
                f"| {s['recall@100']:.4f} | {s['mrr']:.4f} | {s['lat_p50_ms']:.1f} "
                f"| {s['lat_p95_ms']:.1f} | {int(s['n'])} |"
            )
        lines.append("")
        # paired significance on nDCG@10: vs vector-only baseline, plus the key
        # fusion/rerank pairwise (weighted vs RRF, rerank vs RRF, composite vs RRF).
        pq = per_query[dsname]

        def _find(prefix: str):
            return next((o for o in byopt if o.startswith(prefix)), None)

        base = _find("a-")
        pairs = []
        if base:
            for opt in byopt:
                if opt != base:
                    pairs.append((opt, base))
        b, c, d, e = _find("b-"), _find("c-"), _find("d-"), _find("e-")
        for x, y in [(c, b), (d, b), (e, b)]:
            if x and y:
                pairs.append((x, y))
        if pairs:
            lines.append(
                "Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):"
            )
            lines.append("")
            lines.append("| comparison | Δ nDCG@10 | 95% CI | p | ranked? |")
            lines.append("| --- | --- | --- | --- | --- |")
            for x, y in pairs:
                if x not in pq or y not in pq:
                    continue
                pbv = paired_bootstrap(pq[x], pq[y], seed=meta["seed"])
                ranked = "yes" if pbv["significant"] else "tie"
                lines.append(
                    f"| {x} vs {y} | {pbv['delta']:+.4f} | [{pbv['lo']:+.4f}, {pbv['hi']:+.4f}] "
                    f"| {pbv['p_value']:.3f} | {ranked} |"
                )
            lines.append("")
    path = os.path.join(RESULTS_DIR, f"summary_{tag}.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
