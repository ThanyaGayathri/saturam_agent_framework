"""Dataset loaders for the SM-R4 benchmark.

Evaluation sets:
  1. `synthetic_v2` — the independent, LLM-authored + blind-labeled memory set
     under `data/synthetic_v2/` (primary; graded qrels; ~180 queries).
  2. `beir:<name>` — an established, human-labeled IR benchmark from BEIR. Default
     is the FULL corpus (uncapped) for an honest external anchor; a capped slice
     is available for quick smoke runs.
  3. `synthetic_v1` — the retired PR #5 single-authored set, kept for provenance
     only (superseded; do not decide on it).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class EvalSet:
    name: str
    corpus: Dict[str, str]  # doc_id -> text
    queries: Dict[str, str]  # query_id -> text
    qrels: Dict[str, Dict[str, int]]  # query_id -> {doc_id: grade}


def _load_local(dirname: str, tag: str) -> EvalSet:
    d = DATA_DIR / dirname
    corpus_raw = json.loads((d / "corpus.json").read_text())
    queries_raw = json.loads((d / "queries.json").read_text())
    qrels = json.loads((d / "qrels.json").read_text())
    corpus = {mid: v["text"] for mid, v in corpus_raw.items()}
    queries = {qid: v["text"] for qid, v in queries_raw.items()}
    return EvalSet(tag, corpus, queries, qrels)


def load_synthetic_v2() -> EvalSet:
    return _load_local("synthetic_v2", "synthetic_v2")


def load_synthetic_v1() -> EvalSet:
    # retired PR #5 set (files live under data/synthetic_v1/ after the reorg)
    return _load_local("synthetic_v1", "synthetic_v1")


def load_beir(
    name: str = "scifact",
    full: bool = True,
    max_queries: int = 100,
    max_distractors: int = 2000,
    seed: int = 13,
) -> EvalSet:
    """Load a BEIR dataset from the HuggingFace hub.

    full=True (default): the entire corpus + all judged test queries (uncapped,
    honest, comparable to published numbers). Use only for CPU-tractable sets
    (SciFact ~5k, NFCorpus ~3.6k, ArguAna ~8.6k docs).

    full=False: a capped slice (all judged docs for a random query sample, topped
    up with random distractors) for quick smoke runs only.
    """
    import random

    try:
        from datasets import load_dataset
    except ImportError as e:
        raise ImportError(
            "`datasets` not installed; `pip install datasets` to use BEIR sets"
        ) from e

    corpus_ds = load_dataset(f"BeIR/{name}", "corpus", split="corpus")
    queries_ds = load_dataset(f"BeIR/{name}", "queries", split="queries")
    qrels_ds = load_dataset(f"BeIR/{name}-qrels", split="test")

    qrels_all: Dict[str, Dict[str, int]] = {}
    for row in qrels_ds:
        qrels_all.setdefault(str(row["query-id"]), {})[str(row["corpus-id"])] = int(
            row["score"]
        )
    judged_qids = [q for q in qrels_all if qrels_all[q]]

    def doc_text(r) -> str:
        return (r.get("title", "") + " " + r.get("text", "")).strip()

    if full:
        qrels = {q: qrels_all[q] for q in judged_qids}
        queries = {
            str(r["_id"]): r["text"] for r in queries_ds if str(r["_id"]) in qrels
        }
        corpus = {str(r["_id"]): doc_text(r) for r in corpus_ds}
        return EvalSet(f"beir:{name}", corpus, queries, qrels)

    # capped slice
    rng = random.Random(seed)
    rng.shuffle(judged_qids)
    sel = set(judged_qids[:max_queries])
    queries = {str(r["_id"]): r["text"] for r in queries_ds if str(r["_id"]) in sel}
    qrels = {q: qrels_all[q] for q in queries}
    required = {d for rels in qrels.values() for d in rels}
    corpus_index = {str(r["_id"]): doc_text(r) for r in corpus_ds}
    corpus = {d: corpus_index[d] for d in required if d in corpus_index}
    pool = [d for d in corpus_index if d not in required]
    rng.shuffle(pool)
    for d in pool[:max_distractors]:
        corpus[d] = corpus_index[d]
    return EvalSet(f"beir:{name}:capped", corpus, queries, qrels)


def load(name: str, **kwargs) -> EvalSet:
    if name in ("synthetic_v2", "synthetic"):
        return load_synthetic_v2()
    if name == "synthetic_v1":
        return load_synthetic_v1()
    if name.startswith("beir:"):
        return load_beir(name.split(":", 1)[1], **kwargs)
    raise ValueError(f"unknown dataset: {name}")
