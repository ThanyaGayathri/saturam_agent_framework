"""Does OpenIE hold up on real episodic memory, or only on clean Wikipedia prose?

SPIKE.md asserts that short, elliptical, coreference-heavy conversational turns should score
worse than 2Wiki's encyclopaedic prose. That was an *expectation* carried over from SM-R6,
never measured for OpenIE. PR #15's review asked for real episodic memory to be sampled
before SM-H4 commits to the KG substrate. This measures it.

Corpus: **LongMemEval-S** (Wu et al., ICLR'25) haystack sessions — real multi-session
assistant dialogue, the same source SM-R8 used, so it is third-party and non-circular. We
author nothing.

Gold reasoning chains do not exist for this corpus, so **gold-chain recall is not computable
here** and is not reported. What *is* comparable to the 2Wiki numbers, on identical code:

  - **grounding rate** — share of extracted triples whose subject AND object occur in the
    source text. A faithfulness proxy; catches invented entities.
  - **triple yield per 100 words** — extraction density.
  - **unresolved-reference rate** — share of extracted entities that are pronouns or
    speaker-relative phrases ("he", "it", "my wife", "the user"). These are entities that
    *cannot* be linked across passages, so they are dead weight in a KG: PPR cannot traverse
    them. This directly quantifies the coreference problem SM-R6 flagged.
  - **singleton rate** — entities appearing in exactly one passage, i.e. contributing no
    cross-passage connectivity, which is the whole point of the graph.

    PYTHONPATH=. python episodic_kg_quality.py --sessions 40 --tag lme
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List

from harness import kg_quality
from harness.dataset import load_eval_set
from harness.kg import KnowledgeGraph
from harness.llm import LLMClient, Usage, load_env
from harness.openie import Triple, extract_triples, normalize_entity

HERE = Path(__file__).resolve().parent

#: Pronouns and speaker-relative heads that cannot be resolved to a stable KG node.
_PRONOUNS = {
    "i",
    "me",
    "my",
    "mine",
    "myself",
    "you",
    "your",
    "yours",
    "he",
    "him",
    "his",
    "she",
    "her",
    "hers",
    "it",
    "its",
    "we",
    "us",
    "our",
    "ours",
    "they",
    "them",
    "their",
    "theirs",
    "this",
    "that",
    "these",
    "those",
    "who",
    "someone",
    "something",
    "the user",
    "the assistant",
    "user",
    "assistant",
}
_REL_HEAD = re.compile(r"^(my|your|his|her|our|their|the)\s+\w+")


def _unresolved(entity: str) -> bool:
    e = normalize_entity(entity)
    if not e:
        return True
    if e in _PRONOUNS:
        return True
    # "my wife", "their company" - relative to a speaker, not a nameable entity.
    if _REL_HEAD.match(e) and not any(w[0].isupper() for w in entity.split()):
        return True
    return False


def _session_text(session: List[Dict[str, str]]) -> str:
    return "\n".join(f"{t.get('role', '')}: {t.get('content', '')}" for t in session)


def _profile(triples_by_pid: Dict[str, List[Triple]], texts: Dict[str, str], label: str) -> Dict:
    kg = KnowledgeGraph()
    for pid, tr in triples_by_pid.items():
        kg.add_passage(pid, tr)
    all_t = [t for v in triples_by_pid.values() for t in v]
    ents = [e for t in all_t for e in (t.subject, t.object)]
    words = sum(len(v.split()) for v in texts.values())
    singles = sum(1 for k, pids in kg.entity_passages.items() if len(pids) == 1)
    ground = kg_quality.grounding_rate(triples_by_pid, texts)
    return {
        "corpus": label,
        "n_passages": len(triples_by_pid),
        "n_words": words,
        "n_triples": len(all_t),
        "triples_per_100w": round(100 * len(all_t) / words, 3) if words else 0.0,
        "n_entities": len(kg.entity_display),
        "grounding_rate": ground["grounding_rate"],
        "unresolved_entity_rate": round(sum(1 for e in ents if _unresolved(e)) / len(ents), 4) if ents else 0.0,
        "singleton_entity_rate": round(singles / max(len(kg.entity_display), 1), 4),
        "empty_extractions": sum(1 for v in triples_by_pid.values() if not v),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=40, help="LongMemEval sessions to extract")
    ap.add_argument("--wiki-passages", type=int, default=40, help="matched 2Wiki passages for the control")
    ap.add_argument("--tag", type=str, default="")
    args = ap.parse_args()

    load_env()
    llm, usage = LLMClient(), Usage()

    os.environ.setdefault("HF_HOME", str(HERE / "data" / "hf_cache" / "hf"))
    from huggingface_hub import hf_hub_download

    path = hf_hub_download("xiaowu0162/longmemeval", "longmemeval_s", repo_type="dataset")
    data = json.load(open(path, encoding="utf-8"))

    # Deterministic slice: first N distinct sessions across the first questions.
    ep_texts: Dict[str, str] = {}
    for item in data:
        for sid, sess in zip(item["haystack_session_ids"], item["haystack_sessions"]):
            if str(sid) not in ep_texts:
                ep_texts[str(sid)] = _session_text(sess)
            if len(ep_texts) >= args.sessions:
                break
        if len(ep_texts) >= args.sessions:
            break

    print(f"episodic: {len(ep_texts)} LongMemEval sessions")
    ep_triples = {pid: extract_triples(t, llm, usage) for pid, t in ep_texts.items()}
    ep = _profile(ep_triples, ep_texts, "LongMemEval-S (episodic dialogue)")

    # Control: same code, same model, 2Wiki passages (replayed free from cache).
    es = load_eval_set(HERE / "data" / "eval_set.json")
    wiki_texts = {p.pid: f"{p.title}. {p.text}" for p in es.passages[: args.wiki_passages]}
    wiki_triples = {pid: extract_triples(t, llm, usage) for pid, t in wiki_texts.items()}
    wiki = _profile(wiki_triples, wiki_texts, "2WikiMultihopQA (encyclopaedic prose)")

    print()
    keys = [
        "n_passages",
        "n_words",
        "n_triples",
        "triples_per_100w",
        "n_entities",
        "grounding_rate",
        "unresolved_entity_rate",
        "singleton_entity_rate",
        "empty_extractions",
    ]
    print(f"{'metric':26} {'2Wiki (control)':>18} {'LongMemEval':>18}")
    for k in keys:
        print(f"{k:26} {str(wiki[k]):>18} {str(ep[k]):>18}")

    report = {"episodic": ep, "wikipedia_control": wiki, "llm_usage": usage.to_dict()}
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out = HERE / "results" / f"episodic_kg_quality_{stamp}{tag}.json"
    out.write_text(json.dumps(report, indent=2))
    print("\nWrote", out, f"| LLM spend ${usage.to_dict()['usd_cost']:.4f}")


if __name__ == "__main__":
    main()
