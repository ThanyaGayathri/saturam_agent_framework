"""Parameter sweeps for the SM-R8 switch thresholds (fast; reuses cached embeddings).

Sweeps, over the already-embedded datasets:
  - RRF k in {10,30,60,100}           (option b)
  - weighted vector_weight in {0.3..0.7} (option c)
  - composite recency_weight in {0..1.0} (option e; LongMemEval only -- the set with time)

Writes results/sweeps_<stamp>.md + .json. Run after the main fusion pass so the
embeddings are cached (no Azure/CPU embedding cost here).

    python -m benchmarks.memory_retrieval.sweep --datasets beir/scifact beir/nfcorpus \
        longmemeval/longmemeval_s
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Dict, List

from .data import load_dataset
from .embcache import EmbeddingCache
from .harness import Option, run_case
from .pipeline import PipelineConfig
from .run import RESULTS_DIR, _configure_threads, build_embedder
from .scoring import CompositeScorer


def _ndcg(case) -> float:
    return case.metrics.mean("ndcg_cut_10")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--embedder", default="local")
    ap.add_argument("--emb-model", default=None)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    _configure_threads()
    embedder, model = build_embedder(args.embedder, args.emb_model)
    cache = EmbeddingCache(embedder, model)
    cfg = PipelineConfig(retrieve_depth=100, rerank_depth=100, final_k=100)

    datasets = [load_dataset(d) for d in args.datasets]
    out: Dict[str, dict] = {"embedder": model, "sweeps": {}}
    lines: List[str] = [f"# SM-R8 parameter sweeps (embedder={model})", ""]

    # 1) RRF k
    lines += [
        "## RRF k (option b)",
        "",
        "| dataset | " + " | ".join(f"k={k}" for k in (10, 30, 60, 100)) + " |",
        "| --- | --- | --- | --- | --- |",
    ]
    out["sweeps"]["rrf_k"] = {}
    for ds in datasets:
        row = []
        for k in (10, 30, 60, 100):
            c = run_case(
                ds, Option(f"b-rrf-k{k}", fusion_kind="rrf", rrf_k=k, config=cfg), cache
            )
            row.append(_ndcg(c))
        out["sweeps"]["rrf_k"][ds.name] = dict(zip((10, 30, 60, 100), row))
        lines.append(f"| {ds.name} | " + " | ".join(f"{v:.4f}" for v in row) + " |")
    lines.append("")

    # 2) weighted vector_weight
    weights = (0.3, 0.4, 0.5, 0.6, 0.7)
    lines += [
        "## Weighted fusion vector_weight (option c)",
        "",
        "| dataset | " + " | ".join(f"w={w}" for w in weights) + " |",
        "| --- |" + " --- |" * len(weights),
    ]
    out["sweeps"]["vector_weight"] = {}
    for ds in datasets:
        row = []
        for w in weights:
            c = run_case(
                ds,
                Option(f"c-w{w}", fusion_kind="weighted", vector_weight=w, config=cfg),
                cache,
            )
            row.append(_ndcg(c))
        out["sweeps"]["vector_weight"][ds.name] = dict(zip(weights, row))
        lines.append(f"| {ds.name} | " + " | ".join(f"{v:.4f}" for v in row) + " |")
    lines.append("")

    # 3) composite recency_weight (time-bearing datasets only)
    recs = (0.0, 0.1, 0.25, 0.5, 1.0)
    lines += [
        "## Composite recency_weight (option e; time-bearing sets)",
        "",
        "| dataset | " + " | ".join(f"rec={r}" for r in recs) + " |",
        "| --- |" + " --- |" * len(recs),
    ]
    out["sweeps"]["recency_weight"] = {}
    for ds in datasets:
        if not ds.has_time:
            continue
        row = []
        for r in recs:
            comp = CompositeScorer(w_relevance=1.0, w_recency=r, w_importance=0.0)
            c = run_case(
                ds,
                Option(
                    f"e-rec{r}", fusion_kind="rrf", rrf_k=60, composite=comp, config=cfg
                ),
                cache,
            )
            row.append(_ndcg(c))
        out["sweeps"]["recency_weight"][ds.name] = dict(zip(recs, row))
        lines.append(f"| {ds.name} | " + " | ".join(f"{v:.4f}" for v in row) + " |")
    lines.append("")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open(os.path.join(RESULTS_DIR, f"sweeps_{stamp}.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    with open(os.path.join(RESULTS_DIR, f"sweeps_{stamp}.md"), "w") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nWrote results/sweeps_{stamp}.md")


if __name__ == "__main__":
    main()
