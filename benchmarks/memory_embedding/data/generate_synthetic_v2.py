"""Generate the independent synthetic memory-retrieval set (SM-R4 v2).

De-circularization: the *content* is authored by an LLM (Azure gpt-5.x), not by
the harness author, in two separate passes (memories, then queries). Relevance
labels are produced by a SEPARATE blind judge pass (`label_relevance.py`), so the
party that writes the harness does not also write the ground truth.

Design:
  - N personas, each an invented realistic person with atomic memories across
    types (preference / episodic / entity / task / relationship). Some memories
    are deliberately close to each other to create natural hard negatives.
  - Queries are generated per persona, each tagged with a challenge mode and the
    memory id(s) the generator *intended* — but "intended" is provenance only;
    the authoritative graded labels come from the blind judge.
  - All personas are pooled into ONE corpus, so retrieval must discriminate a
    query's own-persona memories from other personas' (cross-persona distractors).

Output (committed as the eval set of record; generation is not deterministic, so
the JSON files are the artifact — the prompts/script are committed for provenance):
    <out>/corpus.json    gid -> {text, type, persona}
    <out>/queries.json   qid -> {text, mode, persona, intended: [gid,...]}
    <out>/personas.json  persona -> sketch

Usage:
    set -a; . <scratchpad>/azure_llm.env; set +a
    python data/generate_synthetic_v2.py --personas 3 --mem-per 12 --q-per 6 \
        --out data/synthetic_v2_sample
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from harness.llm import LLM, preflight

MEM_SYS = (
    "You create realistic test data for an EPISODIC PERSONAL MEMORY retrieval benchmark. "
    "Invent ONE realistic person and a set of short, atomic memory facts about them "
    "(one fact per item), the kind an AI assistant would store about a user over time. "
    "Cover a spread of types: preference, episodic (dated/temporal events), entity "
    "(facts about people/things in their life), task (commitments/todos), relationship. "
    "Requirements that make this a genuine EPISODIC test (not generic fact QA):\n"
    "  - Include several DATED episodic events on the SAME topic at DIFFERENT dates, so a "
    "'most recent / last / previous' query has exactly one correct-time answer.\n"
    "  - Include 2-3 hard-negative PAIRS: two memories close in wording/topic but with "
    "different or opposite answers (one is the trap for a query about the other).\n"
    "  - Include 1-2 CONTRADICTION / belief-revision pairs: an earlier dated fact and a later "
    "dated fact that SUPERSEDES it (moved city, changed job/tool/preference). The later one is "
    "the current truth; the earlier one is now stale.\n"
    "Vary phrasing. Return strict JSON only."
)

MEM_USER_TMPL = (
    "Create persona #{idx}. Make them CLEARLY DISTINCT from every persona already created "
    "below: different occupation, life stage, region, and interests. Avoid any occupation or "
    "domain already used.\n"
    "Already created personas (do NOT resemble these):\n{prior}\n\n"
    "Produce exactly {n} atomic memories, satisfying the episodic/hard-negative/contradiction "
    "requirements.\n"
    'JSON schema: {{"persona": "one-sentence sketch", '
    '"memories": [{{"type": "preference|episodic|entity|task|relationship", "text": "..."}}]}}'
)

Q_SYS = (
    "You write natural user QUERIES for an episodic personal-memory retrieval benchmark. Given a "
    "person's stored memories (each with an id), write queries a user might ask that retrieve "
    "specific memory/memories. Exercise these modes:\n"
    "  - paraphrase: reworded ask for a fact.\n"
    "  - temporal: 'most recent / last / previous / when' over same-topic dated events — exactly "
    "ONE correct-time event answers it.\n"
    "  - coreference: refers to a person/thing by role or pronoun, not name.\n"
    "  - hard_negative: close in wording to a WRONG memory (the trap); only the correct memory "
    "answers it.\n"
    "  - contradiction: asks the CURRENT value of something that changed; only the latest "
    "memory answers it, the superseded one is wrong.\n"
    "  - multi_hop: needs two memories combined.\n"
    "Provide provenance so labels can be audited: `intended` = the id(s) that genuinely answer "
    "it; for hard_negative, `trap` = the wrong-but-similar id(s); for contradiction, "
    "`superseded` = the stale id(s). Keep queries realistic and concise. Return strict JSON only."
)

Q_USER_TMPL = (
    "Person: {persona}\nMemories:\n{mem_block}\n\n"
    "Write exactly {n} queries spread across ALL modes (include >=2 temporal, >=2 hard_negative, "
    ">=1 contradiction where the memories allow). \n"
    'JSON schema: {{"queries": [{{"mode": "...", "text": "...", "intended": ["id", ...], '
    '"trap": ["id", ...], "superseded": ["id", ...]}}]}}'
)


def gen_persona(llm: LLM, idx: int, n_mem: int, prior: list) -> dict:
    prior_block = "\n".join(f"  - {p}" for p in prior) if prior else "  (none yet)"
    return llm.json(MEM_SYS, MEM_USER_TMPL.format(idx=idx, n=n_mem, prior=prior_block))


def gen_queries(llm: LLM, persona: str, mems: dict, n_q: int) -> list:
    mem_block = "\n".join(f"  {mid}: {m['text']}" for mid, m in mems.items())
    out = llm.json(
        Q_SYS, Q_USER_TMPL.format(persona=persona, mem_block=mem_block, n=n_q)
    )
    return out.get("queries", [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", type=int, default=3)
    ap.add_argument("--mem-per", type=int, default=12)
    ap.add_argument("--q-per", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/synthetic_v2_sample")
    args = ap.parse_args()

    missing = preflight()
    if missing:
        raise SystemExit(f"missing Azure env vars: {missing}")

    random.seed(args.seed)
    llm = LLM()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    corpus: dict = {}
    queries: dict = {}
    personas: dict = {}

    for p in range(1, args.personas + 1):
        pid = f"p{p}"
        print(f"[gen] persona {pid} ...")
        data = gen_persona(llm, p, args.mem_per, list(personas.values()))
        personas[pid] = data.get("persona", "")
        local = {}
        for i, m in enumerate(data.get("memories", []), 1):
            gid = f"{pid}_m{i:02d}"
            corpus[gid] = {
                "text": m["text"].strip(),
                "type": m.get("type", "entity"),
                "persona": pid,
            }
            local[gid] = corpus[gid]

        print(f"[gen] persona {pid} queries ...")
        qs = gen_queries(llm, personas[pid], local, args.q_per)
        for j, q in enumerate(qs, 1):
            qid = f"{pid}_q{j:02d}"

            def _ids(key):
                return [g for g in q.get(key, []) if g in local]

            queries[qid] = {
                "text": q["text"].strip(),
                "mode": q.get("mode", "paraphrase"),
                "persona": pid,
                "intended": _ids("intended"),
                "trap": _ids("trap"),
                "superseded": _ids("superseded"),
            }

    (out / "corpus.json").write_text(
        json.dumps(corpus, indent=2, sort_keys=True) + "\n"
    )
    (out / "queries.json").write_text(
        json.dumps(queries, indent=2, sort_keys=True) + "\n"
    )
    (out / "personas.json").write_text(
        json.dumps(personas, indent=2, sort_keys=True) + "\n"
    )
    print(f"[gen] wrote {len(corpus)} memories, {len(queries)} queries -> {out}")


if __name__ == "__main__":
    main()
