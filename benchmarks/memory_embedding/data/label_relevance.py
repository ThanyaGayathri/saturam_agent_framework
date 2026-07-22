"""Mode-aware blind graded labeling + provenance audit (SM-R4 v2).

Two mechanisms, addressing the review of the first v2 labeling:

1. **Mode-aware BLIND judge.** A separate LLM pass grades each query's own-persona
   candidates 0-2, but the rubric is now specialised per query mode so that
   hard-negative traps, wrong-time events, and superseded facts are graded 0 —
   not "1 = related." Candidate ids are still anonymized + shuffled (the judge
   never sees which memory the generator intended).

2. **Provenance audit.** The generator emits, per query, the `intended` answer(s),
   the `trap` (wrong-but-similar) ids for hard_negative, and the `superseded` ids
   for contradiction. After judging we deterministically enforce the design:
     - trap / superseded ids are forced to grade 0 (fixes inversions like the old
       p8_q07 where a planted trap had been labeled relevant);
     - for the single-answer modes (hard_negative / temporal / contradiction) the
       qrel is the intended answer(s) only (grade 2), and a query whose judge did
       NOT independently rate the intended answer relevant is DROPPED as ambiguous
       (reported), rather than shipped with a dubious label;
     - graded modes (paraphrase / coreference / multi_hop) keep the judge's graded
       relevance, minus any trap/superseded, with the intended answer guaranteed
       present.

Output:
    <dir>/qrels.json         qid -> {gid: grade}   (grade >= 1 only)
    <dir>/label_audit.json   per-query judge view + corrections for spot-checking
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from harness.llm import LLM, preflight

STRICT_MODES = {"hard_negative", "temporal", "contradiction"}

MODE_RUBRIC = {
    "hard_negative": (
        "This query is close in wording to a WRONG memory (a trap). A memory that shares "
        "topic/words but gives a different or opposite answer is NOT relevant (0). Only the "
        "memory that directly and correctly answers the query is relevant (2)."
    ),
    "temporal": (
        "This asks about a specific time (most recent / last / previous). ONLY the event at the "
        "matching time is relevant (2); other same-topic events at other times are NOT relevant (0)."
    ),
    "contradiction": (
        "The user's situation changed over time. ONLY the latest/current fact is relevant (2); "
        "the earlier, now-superseded fact is NOT relevant (0)."
    ),
    "default": (
        "2 = directly and specifically answers it; 1 = genuinely partial answer; 0 = merely "
        "related topic or not relevant. Do not give 1 to a memory that is only topically similar."
    ),
}

JUDGE_SYS = (
    "You are a careful relevance judge for an episodic personal-memory retrieval system. Grade "
    "how well EACH candidate answers the query. Judge only on meaning. Most candidates are 0. "
    'Return strict JSON only: {"ratings": {"c1": 0, "c2": 2, ...}}.'
)

JUDGE_USER_TMPL = "Query ({mode}): {query}\nRubric: {rubric}\n\nCandidates:\n{cands}\n\nRate every candidate id."


def label_query(llm, query_text, mode, cand_gids, corpus, rng):
    order = cand_gids[:]
    rng.shuffle(order)
    alias = {f"c{i + 1}": gid for i, gid in enumerate(order)}
    cands = "\n".join(f"  {a}: {corpus[gid]['text']}" for a, gid in alias.items())
    rubric = MODE_RUBRIC.get(mode, MODE_RUBRIC["default"])
    ratings = llm.rate_batch(
        JUDGE_SYS,
        JUDGE_USER_TMPL.format(mode=mode, query=query_text, rubric=rubric, cands=cands),
    )
    graded = {}
    for a, gid in alias.items():
        g = int(ratings.get(a, 0))
        if g > 0:
            graded[gid] = min(2, max(0, g))
    return graded


def audit(mode, intended, trap, superseded, judge_graded):
    """Apply provenance to the judge's grades. Returns (qrel, record)."""
    neg = set(trap) | set(superseded)
    # a trap / superseded memory is authoritative-negative: it cannot also be the
    # answer, even if the generator inconsistently listed it under `intended`.
    intended = [g for g in intended if g not in neg]
    graded = {g: v for g, v in judge_graded.items() if g not in neg}
    corrections = [f"forced {g}->0" for g in neg if g in judge_graded]

    judge_backed_intended = [g for g in intended if judge_graded.get(g, 0) >= 1]

    if mode in STRICT_MODES:
        # single known correct answer; drop if the judge didn't independently back it
        if not intended or not judge_backed_intended:
            return None, {
                "dropped": True,
                "reason": "judge did not back intended",
                "corrections": corrections,
            }
        qrel = {g: 2 for g in intended}
    else:
        # graded modes: keep judge grades, guarantee the intended answer is present
        qrel = graded
        for g in intended:
            qrel.setdefault(g, 2)
        if not qrel:
            return None, {
                "dropped": True,
                "reason": "no positive after audit",
                "corrections": corrections,
            }
    record = {
        "corrections": corrections,
        "judge_backed_intended": bool(judge_backed_intended),
    }
    return qrel, record


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/synthetic_v2")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    missing = preflight()
    if missing:
        raise SystemExit(f"missing Azure env vars: {missing}")

    d = Path(args.dir)
    corpus = json.loads((d / "corpus.json").read_text())
    queries = json.loads((d / "queries.json").read_text())

    by_persona: dict = {}
    for gid, m in corpus.items():
        by_persona.setdefault(m["persona"], []).append(gid)

    rng = random.Random(args.seed)
    llm = LLM()
    qrels: dict = {}
    audit_log: dict = {}
    dropped = 0
    corrected = 0
    for qid, q in queries.items():
        cands = by_persona.get(q["persona"], [])
        judged = label_query(llm, q["text"], q["mode"], cands, corpus, rng)
        qrel, rec = audit(
            q["mode"],
            q.get("intended", []),
            q.get("trap", []),
            q.get("superseded", []),
            judged,
        )
        rec.update(
            {
                "query": q["text"],
                "mode": q["mode"],
                "intended": q.get("intended", []),
                "judge_positive": judged,
            }
        )
        audit_log[qid] = rec
        corrected += len(rec.get("corrections", []))
        if qrel is None:
            dropped += 1
            print(f"[label] {qid} ({q['mode']}): DROPPED — {rec['reason']}")
            continue
        qrels[qid] = qrel
        print(
            f"[label] {qid} ({q['mode']}): {len(qrel)} positive "
            f"{'[' + ','.join(rec['corrections']) + ']' if rec['corrections'] else ''}"
        )

    (d / "qrels.json").write_text(json.dumps(qrels, indent=2, sort_keys=True) + "\n")
    (d / "label_audit.json").write_text(
        json.dumps(audit_log, indent=2, sort_keys=True) + "\n"
    )
    print(
        f"[label] kept {len(qrels)} queries, dropped {dropped}, "
        f"provenance corrections {corrected}"
    )


if __name__ == "__main__":
    main()
