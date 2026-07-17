"""Blind graded relevance labeling for the synthetic-v2 set (SM-R4).

Separates label authorship from query authorship. For each query, the judge (a
fresh LLM pass) rates its own-persona memories on a 0-2 graded scale WITHOUT
being told which memory the generator intended — candidate ids are anonymized and
shuffled, so the judge cannot copy the generator's choice. Cross-persona memories
are non-relevant by construction (personas are disjoint) and left unlabeled (0).

Grades: 2 = directly answers the query; 1 = partially/related; 0 = not relevant.

Output:
    <dir>/qrels.json         qid -> {gid: grade}   (grade >= 1 only)
    <dir>/label_audit.json   per-query judge view for spot-checking

Usage:
    set -a; . <scratchpad>/azure_llm.env; set +a
    python data/label_relevance.py --dir data/synthetic_v2_sample
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from harness.llm import LLM, preflight

JUDGE_SYS = (
    "You are a careful relevance judge for a personal-memory retrieval system. Given a user "
    "query and a list of candidate memories, rate how well EACH candidate answers the query on "
    "this scale: 2 = directly and specifically answers it; 1 = partially relevant or related but "
    "not a direct answer; 0 = not relevant. Judge only on meaning. It is normal for most "
    'candidates to be 0. Return strict JSON only: {"ratings": {"c1": 0, "c2": 2, ...}}.'
)

JUDGE_USER_TMPL = "Query: {query}\n\nCandidates:\n{cands}\n\nRate every candidate id."


def label_query(
    llm: LLM, query_text: str, cand_gids: list, corpus: dict, rng: random.Random
) -> dict:
    # anonymize + shuffle so the judge can't infer the generator's intended id
    order = cand_gids[:]
    rng.shuffle(order)
    alias = {f"c{i + 1}": gid for i, gid in enumerate(order)}
    cands = "\n".join(f"  {a}: {corpus[gid]['text']}" for a, gid in alias.items())
    ratings = llm.rate_batch(
        JUDGE_SYS, JUDGE_USER_TMPL.format(query=query_text, cands=cands)
    )
    # map aliases back to gids
    graded = {}
    for a, gid in alias.items():
        g = int(ratings.get(a, 0))
        if g > 0:
            graded[gid] = min(2, max(0, g))
    return graded


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/synthetic_v2_sample")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    missing = preflight()
    if missing:
        raise SystemExit(f"missing Azure env vars: {missing}")

    d = Path(args.dir)
    corpus = json.loads((d / "corpus.json").read_text())
    queries = json.loads((d / "queries.json").read_text())

    # group corpus gids by persona for the candidate pool
    by_persona: dict = {}
    for gid, m in corpus.items():
        by_persona.setdefault(m["persona"], []).append(gid)

    rng = random.Random(args.seed)
    llm = LLM()
    qrels: dict = {}
    audit: dict = {}
    for qid, q in queries.items():
        cands = by_persona.get(q["persona"], [])
        graded = label_query(llm, q["text"], cands, corpus, rng)
        qrels[qid] = graded
        audit[qid] = {
            "query": q["text"],
            "mode": q["mode"],
            "intended": q["intended"],
            "judge_positive": graded,
            "intended_matches_judge": all(graded.get(g, 0) >= 1 for g in q["intended"])
            if q["intended"]
            else None,
        }
        print(f"[label] {qid}: {len(graded)} positive of {len(cands)} candidates")

    (d / "qrels.json").write_text(json.dumps(qrels, indent=2, sort_keys=True) + "\n")
    (d / "label_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )

    # quick agreement stat: how often the generator's intended id got judge grade>=1
    checkable = [a for a in audit.values() if a["intended_matches_judge"] is not None]
    agree = sum(1 for a in checkable if a["intended_matches_judge"])
    print(
        f"[label] wrote {len(qrels)} qrels. generator/judge agreement on intended: "
        f"{agree}/{len(checkable)}"
    )


if __name__ == "__main__":
    main()
