"""Eval-corpus builder for the multi-hop spike.

Source: ``voidful/2WikiMultihopQA`` (validation split) — an *established*,
third-party multi-hop QA benchmark (Ho et al., COLING 2020). We do not author the
questions, the passages, or the gold labels, which keeps the eval non-circular
(§5 data-quality bar): authors = dataset creators, labels = dataset gold.

Each source row gives us, per question:
  - ``question`` / ``answer``            — the multi-hop query + short gold answer
  - ``context``: list[[title, [sents]]]  — 10 candidate passages (2 gold + 8 distractors)
  - ``supporting_facts``: [[title, i]]   — gold supporting sentences → gold passage titles
  - ``evidences``: [[subj, rel, obj]]    — gold reasoning chain (KG triples)
  - ``type``: compositional|inference|bridge_comparison|comparison

We build a **single pooled corpus** across the sampled questions (passages deduped
by title) — the faithful HippoRAG setting and the honest test for a shared memory
store: every query must find its two bridge passages among *all* passages, not
just its own 10. Genuinely-multi-hop *chaining* types (compositional, inference,
bridge_comparison) are the headline; ``comparison`` (two parallel facts, no bridge)
is included as a labelled contrast stratum.

Deterministic: the sample is fixed by ``--seed`` and a stratified quota.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List

# Chaining (bridge) types = "genuinely multi-hop"; comparison = parallel-fact contrast.
CHAINING_TYPES = ("compositional", "inference", "bridge_comparison")
DEFAULT_QUOTA: Dict[str, int] = {
    "compositional": 12,
    "inference": 6,
    "bridge_comparison": 6,
    "comparison": 6,
}


@dataclass
class Passage:
    pid: str
    title: str
    text: str


@dataclass
class EvalItem:
    qid: str
    question: str
    answer: str
    qtype: str
    gold_titles: List[str]  # gold supporting passage titles (the bridge hops)
    evidences: List[List[str]]  # gold [subj, rel, obj] reasoning chain


@dataclass
class EvalSet:
    name: str
    seed: int
    passages: List[Passage] = field(default_factory=list)
    items: List[EvalItem] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "seed": self.seed,
            "n_passages": len(self.passages),
            "n_items": len(self.items),
            "passages": [asdict(p) for p in self.passages],
            "items": [asdict(i) for i in self.items],
        }

    @staticmethod
    def from_dict(d: Dict) -> "EvalSet":
        es = EvalSet(name=d["name"], seed=d["seed"])
        es.passages = [Passage(**p) for p in d["passages"]]
        es.items = [EvalItem(**i) for i in d["items"]]
        return es


def _jl(raw):
    """Decode a 2Wiki field that is *double* JSON-encoded.

    Titles arrive as e.g. '"\\u00c6lfgar, Earl of Mercia"' and each passage's
    sentences arrive as a JSON *string* '["sent one", "sent two"]' rather than a
    list. ``json.loads`` unwraps one layer (fixing the \\uXXXX escapes and turning
    the sentence string into a real list). Falls back to the raw value if it is
    already decoded, so the loader is robust to dataset-version changes.
    """
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _clean_title(raw: str) -> str:
    decoded = _jl(raw)
    return decoded.strip() if isinstance(decoded, str) else str(decoded).strip()


def _passage_text(sents_raw) -> str:
    sents = _jl(sents_raw)
    if isinstance(sents, str):
        sents = [sents]
    return " ".join(str(s).strip() for s in sents).strip()


def _sample_rows(seed: int, quota: Dict[str, int], scan_limit: int) -> List[dict]:
    """Stratified, seeded sample from the validation split.

    Reservoir-free: we scan the (shuffled-by-seed) split and take the first ``quota``
    rows of each type. Deterministic for a given seed + dataset version.
    """
    from datasets import load_dataset

    rows = list(load_dataset("voidful/2WikiMultihopQA", split="validation", streaming=True).take(scan_limit))
    rng = random.Random(seed)
    rng.shuffle(rows)
    picked: List[dict] = []
    remaining = dict(quota)
    for r in rows:
        t = r["type"]
        if remaining.get(t, 0) > 0:
            picked.append(r)
            remaining[t] -= 1
        if all(v == 0 for v in remaining.values()):
            break
    missing = {k: v for k, v in remaining.items() if v > 0}
    if missing:
        raise RuntimeError(f"Could not fill quota {missing} within scan_limit={scan_limit}; raise --scan-limit.")
    return picked


def build_eval_set(seed: int, quota: Dict[str, int], scan_limit: int = 4000, name: str = "2wiki-multihop") -> EvalSet:
    rows = _sample_rows(seed, quota, scan_limit)
    es = EvalSet(name=name, seed=seed)
    title_to_pid: Dict[str, str] = {}

    def _ensure_passage(title: str, sents: List[str]) -> str:
        key = _clean_title(title)
        if key not in title_to_pid:
            pid = f"p{len(title_to_pid):04d}"
            title_to_pid[key] = pid
            es.passages.append(Passage(pid=pid, title=key, text=_passage_text(sents)))
        return title_to_pid[key]

    for r in rows:
        # Register every candidate passage into the pooled corpus (dedup by title).
        for title, sents in r["context"]:
            _ensure_passage(title, sents)
        gold_titles = sorted({_clean_title(t) for t, _ in r["supporting_facts"]})
        es.items.append(
            EvalItem(
                qid=r["_id"],
                question=r["question"],
                answer=r["answer"],
                qtype=r["type"],
                gold_titles=gold_titles,
                evidences=[list(e) for e in r["evidences"]],
            )
        )
    return es


def load_eval_set(path: Path) -> EvalSet:
    return EvalSet.from_dict(json.loads(Path(path).read_text()))


def summarize(es: EvalSet) -> Dict:
    from collections import Counter

    by_type = Counter(i.qtype for i in es.items)
    hops = Counter(len(i.gold_titles) for i in es.items)
    chaining = sum(1 for i in es.items if i.qtype in CHAINING_TYPES)
    return {
        "n_items": len(es.items),
        "n_passages": len(es.passages),
        "by_type": dict(by_type),
        "gold_hops_per_q": dict(hops),
        "chaining_items": chaining,
        "comparison_items": len(es.items) - chaining,
        "avg_distractors": round(len(es.passages) / max(len(es.items), 1), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the seeded 2Wiki multi-hop eval set.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scan-limit", type=int, default=4000)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "eval_set.json")
    ap.add_argument("--quota", type=str, default="", help="JSON dict overriding the per-type quota")
    args = ap.parse_args()

    quota = json.loads(args.quota) if args.quota else DEFAULT_QUOTA
    es = build_eval_set(seed=args.seed, quota=quota)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(es.to_dict(), ensure_ascii=False, indent=2))
    print("Wrote", args.out)
    print(json.dumps(summarize(es), indent=2))


if __name__ == "__main__":
    main()
