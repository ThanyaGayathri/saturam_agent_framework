"""Dataset loaders for the SM-R4 benchmark.

Two evaluation sets:

  1. `synthetic`  – the hand-authored memory-tier set under `data/` (primary;
     representative of short episodic/preference/entity memories).
  2. `beir:<name>` – a slice of a public BEIR IR benchmark (external cross-check
     against published MTEB-style numbers). Requires the `datasets` package and
     network access at build time; the slice is capped for tractable CPU runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class EvalSet:
    name: str
    corpus: Dict[str, str]  # doc_id -> text
    queries: Dict[str, str]  # query_id -> text
    qrels: Dict[str, Dict[str, int]]  # query_id -> {doc_id: relevance}


def load_synthetic() -> EvalSet:
    corpus_raw = json.loads((DATA_DIR / "corpus.json").read_text())
    queries_raw = json.loads((DATA_DIR / "queries.json").read_text())
    qrels = json.loads((DATA_DIR / "qrels.json").read_text())
    corpus = {mid: v["text"] for mid, v in corpus_raw.items()}
    queries = {qid: v["text"] for qid, v in queries_raw.items()}
    return EvalSet("synthetic", corpus, queries, qrels)


def load_beir(
    name: str = "scifact",
    max_queries: int = 100,
    max_distractors: int = 2000,
    seed: int = 13,
) -> EvalSet:
    """Load a capped slice of a BEIR dataset from the HuggingFace hub.

    To keep CPU runtime bounded we keep every doc referenced by the sampled
    queries' qrels, then top up with random distractor docs up to
    `max_distractors`. This preserves recall/nDCG semantics for the sampled
    queries while avoiding embedding the full corpus.
    """
    import random

    try:
        from datasets import load_dataset
    except ImportError as e:
        raise ImportError(
            "`datasets` not installed; `pip install datasets` to use the BEIR slice"
        ) from e

    rng = random.Random(seed)

    corpus_ds = load_dataset(f"BeIR/{name}", "corpus", split="corpus")
    queries_ds = load_dataset(f"BeIR/{name}", "queries", split="queries")
    qrels_ds = load_dataset(f"BeIR/{name}-qrels", split="test")

    # Build qrels index
    qrels_all: Dict[str, Dict[str, int]] = {}
    for row in qrels_ds:
        qid = str(row["query-id"])
        did = str(row["corpus-id"])
        qrels_all.setdefault(qid, {})[did] = int(row["score"])

    # Sample queries that actually have relevance judgements
    judged_qids = [q for q in qrels_all if qrels_all[q]]
    rng.shuffle(judged_qids)
    sel_qids = set(judged_qids[:max_queries])

    queries = {
        str(r["_id"]): r["text"] for r in queries_ds if str(r["_id"]) in sel_qids
    }
    qrels = {qid: rels for qid, rels in qrels_all.items() if qid in queries}

    # Required docs = every judged doc for the selected queries
    required_docs = {did for rels in qrels.values() for did in rels}

    corpus: Dict[str, str] = {}
    distractor_pool: List[str] = []
    for r in corpus_ds:
        did = str(r["_id"])
        text = (r.get("title", "") + " " + r.get("text", "")).strip()
        if did in required_docs:
            corpus[did] = text
        else:
            distractor_pool.append(did)

    rng.shuffle(distractor_pool)
    corpus_index = {
        str(r["_id"]): (r.get("title", "") + " " + r.get("text", "")).strip()
        for r in corpus_ds
    }
    for did in distractor_pool[:max_distractors]:
        corpus[did] = corpus_index[did]

    return EvalSet(f"beir:{name}", corpus, queries, qrels)


def load(name: str, **kwargs) -> EvalSet:
    if name == "synthetic":
        return load_synthetic()
    if name.startswith("beir:"):
        return load_beir(name.split(":", 1)[1], **kwargs)
    raise ValueError(f"unknown dataset: {name}")
