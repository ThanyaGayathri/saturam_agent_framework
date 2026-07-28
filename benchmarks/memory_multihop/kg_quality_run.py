"""Measure OpenIE KG quality against 2WikiMultihopQA's gold `evidences` chains.

Answers the PR #15 review point that `0/285 extraction failures` is JSON-parse success,
not triple quality. See `harness/kg_quality.py` for what is and is not measurable
(recall: yes; precision against `evidences`: no — the gold is chain-only).

Two modes:

    # verify the scorer itself — no LLM, no credentials, no network
    PYTHONPATH=. python kg_quality_run.py --self-test

    # measure the real KG (needs Azure creds in .env, or a warm results/llm_cache)
    PYTHONPATH=. python kg_quality_run.py --tag full30
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List

from harness import kg_quality
from harness.dataset import load_eval_set
from harness.kg import KnowledgeGraph
from harness.llm import LLMClient, Usage, load_env
from harness.openie import Triple, extract_triples

HERE = Path(__file__).resolve().parent


def _self_test() -> int:
    """Validate the scorer on a hand-built fixture — proves it works without an LLM.

    Fixture: two passages, one gold 2-hop chain. Extraction recovers hop 1 with a
    different relation wording and a longer surface form for the entity, and recovers
    hop 2's entities but *not* the edge between them.
    """
    triples_by_pid = {
        "p1": [Triple("Aethelgifu", "mother", "Queen Ealhswith")],
        "p2": [
            Triple("Queen Ealhswith", "resided in", "Wessex"),
            Triple("Mercia", "is a", "kingdom"),
        ],
    }
    texts = {
        "p1": "Aethelgifu was the daughter of Queen Ealhswith.",
        "p2": "Queen Ealhswith resided in Wessex. Mercia is a kingdom.",
    }
    kg = KnowledgeGraph()
    for pid, tr in triples_by_pid.items():
        kg.add_passage(pid, tr)
    kg.finalize({}, synonym_tau=0.85)

    gold = [
        ["Aethelgifu", "mother", "Ealhswith"],
        ["Ealhswith", "place of birth", "Mercia"],
    ]
    rels = kg_quality.index_relations(triples_by_pid)
    score = kg_quality.score_chain_recall(gold, kg, rels)

    failures: List[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # Hop 1: 'Ealhswith' must match the longer surface 'queen ealhswith'; edge exists.
    # Hop 2: both entities exist but no edge connects them -> entity hit, no edge hit.
    check("n_gold", score.n_gold, 2)
    check("entity_hits", score.entity_hits, 2)
    check("edge_hits", score.edge_hits, 1)
    # Relation differs ('mother' extracted vs 'mother' gold) -> strict triple hit on hop 1.
    check("triple_hits", score.triple_hits, 1)
    check("n_missing_edges", len(score.missing_edges), 1)

    ground = kg_quality.grounding_rate(triples_by_pid, texts)
    # 'Mercia is a kingdom' is grounded; all three extracted triples appear in text.
    check("grounding n_triples", ground["n_triples"], 3)
    check("grounded", ground["grounded"], 3)

    agg = kg_quality.aggregate_scores([score])
    check("full_chain_recall", agg["full_chain_recall"], 0.0)  # 1 of 2 hops -> chain broken

    if failures:
        print("SELF-TEST FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("[self-test] scorer OK:", json.dumps(score.to_dict()))
    print("[self-test] grounding:", json.dumps(ground))
    print("[self-test] aggregate:", json.dumps(agg))
    print("SELF-TEST OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="verify the scorer, no LLM needed")
    ap.add_argument("--eval", type=Path, default=HERE / "data" / "eval_set.json")
    ap.add_argument("--tag", type=str, default="")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    load_env()
    es = load_eval_set(args.eval)
    llm = LLMClient()
    usage = Usage()

    # One OpenIE pass over the corpus (replayed free from results/llm_cache if warm).
    triples_by_pid: Dict[str, List[Triple]] = {}
    kg = KnowledgeGraph()
    for p in es.passages:
        tr = extract_triples(f"{p.title}. {p.text}", llm, usage)
        triples_by_pid[p.pid] = tr
        kg.add_passage(p.pid, tr)
    kg.finalize({}, synonym_tau=0.85)  # synonymy needs vectors; omitted for a pure-OpenIE read

    rels = kg_quality.index_relations(triples_by_pid)
    per_q = []
    scores = []
    for it in es.items:
        s = kg_quality.score_chain_recall(it.evidences, kg, rels)
        scores.append(s)
        per_q.append({"qid": it.qid, "qtype": it.qtype, **s.to_dict()})

    texts = {p.pid: f"{p.title}. {p.text}" for p in es.passages}
    report = {
        "aggregate": kg_quality.aggregate_scores(scores),
        "grounding": kg_quality.grounding_rate(triples_by_pid, texts),
        "llm_usage": usage.to_dict(),
        "per_question": per_q,
    }
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out = HERE / "results" / f"kg_quality_{stamp}{tag}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report["aggregate"], indent=2))
    print(json.dumps(report["grounding"], indent=2))
    print("Wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
