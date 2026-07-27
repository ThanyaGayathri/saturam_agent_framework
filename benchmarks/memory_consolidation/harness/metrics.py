"""Consolidation-quality metrics (AC #4 — the measurable quality bar).

Two families:

1. Label-based metrics (synthetic dataset, where we planted the answer):
   - fact recall / precision      : did we keep the right facts, without noise?
   - dedup score                  : did we collapse duplicates to one fact?
   - conflict-resolution accuracy : did 'keep newer' retire the stale fact?
   - provenance precision/recall  : do cited source episodes match the gold set,
                                     and are all cited ids real?
   - memory size                  : active fact count + token footprint.

2. QA correctness (works on any dataset, incl. LOCOMO) via retrieval + LLM judge.

The headline "consolidation quality" score reported in RESEARCH.md is a weighted
blend of recall, precision, dedup, conflict and provenance — defined in
``composite_quality`` so the definition is explicit and reproducible.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import re

from egpt.utils.tokens import count_text_tokens

from harness.llm import LLMClient
from harness.schemas import SemanticFact, TargetFact, UsageStats
from harness.strategies.base import lexical_similarity

MATCH_THRESHOLD = 0.6

# Stopwords stripped before content-overlap matching so paraphrases with extra
# filler words ("Priya works as a software engineer at a fintech startup" vs the
# gold "Priya is a software engineer") still match on their content tokens.
_STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "her",
    "his",
    "their",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "with",
    "as",
    "that",
    "this",
    "it",
    "she",
    "he",
    "they",
    "has",
    "have",
    "had",
    "now",
    "still",
    "always",
    "usually",
    "currently",
    "recently",
    "said",
    "says",
    "mentioned",
    "shared",
    "talked",
    "about",
    "who",
    "which",
    "does",
    "do",
}
_WORD = re.compile(r"[a-z0-9]+")


def _content(text: str) -> set:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def _overlap(statement: str, surface: str) -> float:
    """Overlap coefficient over content tokens: |A n B| / min(|A|, |B|).

    Rewards the shorter (gold) string being mostly contained in the fact,
    tolerating extra words in a verbose LLM statement. Requires >=2 shared
    content tokens to avoid trivial single-word matches.
    """
    a, b = _content(statement), _content(surface)
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if shared < 2:
        return 0.0
    return shared / min(len(a), len(b))


def _best_match(statement: str, target: TargetFact) -> float:
    return max((_overlap(statement, sf) for sf in target.surface_forms()), default=0.0)


def match_facts_to_targets(
    facts: List[SemanticFact],
    targets: List[TargetFact],
    threshold: float = MATCH_THRESHOLD,
) -> Tuple[Dict[str, List[SemanticFact]], List[SemanticFact]]:
    """Map each target -> active facts that express it; return also unmatched facts."""
    active = [f for f in facts if f.is_active]
    by_target: Dict[str, List[SemanticFact]] = {t.target_id: [] for t in targets}
    unmatched: List[SemanticFact] = []
    for f in active:
        best_t, best_s = None, 0.0
        for t in targets:
            s = _best_match(f.statement, t)
            if s > best_s:
                best_t, best_s = t, s
        if best_t is not None and best_s >= threshold:
            by_target[best_t.target_id].append(f)
        else:
            unmatched.append(f)
    return by_target, unmatched


_ASSERT_SYSTEM = (
    "You decide whether a statement is CURRENTLY true according to a user's "
    "memory facts. Account for change over time: if the facts show the situation "
    "changed (moved, switched, no longer, got promoted), judge by the LATEST "
    "state, not the earlier one. Reply with ONLY one word: YES, NO, or UNKNOWN."
)


def _assert_prompt(target: TargetFact, facts: List[SemanticFact]) -> str:
    ctx = "\n".join(f"- {f.statement}" for f in _retrieve(facts, target.statement, k=10))
    return f"Memory facts:\n{ctx}\n\nStatement: {target.statement}\nIs this statement currently true?"


def target_asserted(target: TargetFact, facts: List[SemanticFact], llm: LLMClient, usage: UsageStats) -> bool:
    verdict = llm.complete(_ASSERT_SYSTEM, _assert_prompt(target, facts), usage)
    return verdict.strip().upper().startswith("YES")


async def atarget_asserted(target: TargetFact, facts: List[SemanticFact], llm: LLMClient, usage: UsageStats) -> bool:
    verdict = await llm.acomplete(_ASSERT_SYSTEM, _assert_prompt(target, facts), usage)
    return verdict.strip().upper().startswith("YES")


def label_metrics(
    facts: List[SemanticFact],
    targets: List[TargetFact],
    episode_ids: set,
    asserted: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Compute label metrics.

    ``asserted`` optionally maps target_id -> bool from an LLM assertion judge
    (see ``judge_targets``). When provided, recall and conflict-resolution use it
    (robust to negation/recency); otherwise they fall back to the lexical mapping.
    Precision/dedup/provenance always use the lexical fact->target mapping.
    """
    by_target, unmatched = match_facts_to_targets(facts, targets)
    expected = [t for t in targets if t.should_be_active]
    retired_targets = [t for t in targets if not t.should_be_active]
    active = [f for f in facts if f.is_active]

    def is_present(t: TargetFact) -> bool:
        if asserted is not None and t.target_id in asserted:
            return asserted[t.target_id]
        return bool(by_target[t.target_id])

    # Recall: expected targets currently asserted by memory.
    covered = [t for t in expected if is_present(t)]
    recall = len(covered) / len(expected) if expected else 1.0

    # Precision: active facts that lexically match some expected target.
    good = sum(len(by_target[t.target_id]) for t in expected)
    precision = good / len(active) if active else 1.0

    # Dedup: for lexically-covered expected targets, ideal is exactly 1 fact each.
    lex_covered = [t for t in expected if by_target[t.target_id]]
    dup_counts = [len(by_target[t.target_id]) for t in lex_covered]
    dedup_score = (len(lex_covered) / sum(dup_counts)) if sum(dup_counts) else 1.0
    facts_per_target = (sum(dup_counts) / len(lex_covered)) if lex_covered else 0.0

    # Conflict resolution: winner currently asserted, loser NOT asserted.
    conflict_pairs = [t for t in targets if t.supersedes]
    resolved = 0
    for t in conflict_pairs:
        loser = next((x for x in targets if x.target_id == t.supersedes), None)
        winner_active = is_present(t)
        loser_gone = (not is_present(loser)) if loser else True
        if winner_active and loser_gone:
            resolved += 1
    conflict_accuracy = resolved / len(conflict_pairs) if conflict_pairs else None

    # Provenance: cited source episodes vs gold, over matched facts.
    prov_p, prov_r, prov_valid, n = 0.0, 0.0, 0.0, 0
    for t in expected:
        gold = set(t.source_episode_ids)
        for f in by_target[t.target_id]:
            cited = set(f.source_episode_ids)
            if not gold:
                continue
            tp = len(cited & gold)
            prov_p += tp / len(cited) if cited else 0.0
            prov_r += tp / len(gold) if gold else 0.0
            prov_valid += len(cited & episode_ids) / len(cited) if cited else 1.0
            n += 1
    provenance_precision = prov_p / n if n else None
    provenance_recall = prov_r / n if n else None
    provenance_validity = prov_valid / n if n else None

    return {
        "fact_recall": round(recall, 4),
        "fact_precision": round(precision, 4),
        "dedup_score": round(dedup_score, 4),
        "facts_per_covered_target": round(facts_per_target, 3),
        "conflict_accuracy": None if conflict_accuracy is None else round(conflict_accuracy, 4),
        "provenance_precision": None if provenance_precision is None else round(provenance_precision, 4),
        "provenance_recall": None if provenance_recall is None else round(provenance_recall, 4),
        "provenance_validity": None if provenance_validity is None else round(provenance_validity, 4),
        "active_fact_count": len(active),
        "noise_fact_count": len(unmatched),
        "memory_tokens": sum(count_text_tokens(f.statement) for f in active),
        "n_expected_targets": len(expected),
        "n_retired_targets": len(retired_targets),
    }


def judge_targets(
    targets: List[TargetFact], facts: List[SemanticFact], llm: LLMClient, usage: UsageStats
) -> Dict[str, bool]:
    """LLM assertion judge for every target -> {target_id: currently_true}. Sync."""
    return {t.target_id: target_asserted(t, facts, llm, usage) for t in targets}


async def ajudge_targets(
    targets: List[TargetFact], facts: List[SemanticFact], llm: LLMClient, usage: UsageStats
) -> Dict[str, bool]:
    """Async variant of ``judge_targets``."""
    out: Dict[str, bool] = {}
    for t in targets:
        out[t.target_id] = await atarget_asserted(t, facts, llm, usage)
    return out


def composite_quality(m: Dict[str, Any]) -> float:
    """Single headline store-quality score in [0, 1]. Weights per RESEARCH.md.

    Provenance is weighted 0.35 total (recall + precision) to reflect SM-R7's
    provenance-first objective; provenance *precision* is what exposes lossy
    whole-blob summarization (a summary that cites every episode scores ~0).

    NOTE (documented in RESEARCH.md): this score does NOT capture memory-growth /
    scale. On a small corpus a no-consolidation baseline flatters itself here;
    read the score alongside footprint and the LOCOMO scale results.
    """

    def g(key: str, default: float) -> float:
        v = m.get(key)
        return default if v is None else float(v)

    return round(
        0.20 * g("fact_recall", 0.0)
        + 0.15 * g("fact_precision", 0.0)
        + 0.15 * g("dedup_score", 0.0)
        + 0.15 * g("conflict_accuracy", 1.0)
        + 0.15 * g("provenance_recall", 0.0)
        + 0.20 * g("provenance_precision", 0.0),
        4,
    )


# --------------------------------------------------------------------------- #
# QA correctness (retrieval + LLM judge) — used for LOCOMO / external validity.
# --------------------------------------------------------------------------- #
_QA_SYSTEM = (
    "Answer the question using ONLY the provided memory facts. "
    "If the facts do not contain the answer, reply exactly 'I don't know'. "
    "Be concise."
)
_JUDGE_SYSTEM = (
    "You grade an answer against the gold answer. Reply ONLY 'YES' if the answer "
    "is correct/equivalent to the gold answer, otherwise 'NO'."
)


# Retrieval budget for QA / assertion judging. Deliberately large so that on the
# small synthetic memories the *whole* consolidated store is handed to the reader
# — this isolates consolidation quality (did we KEEP the answer, without lossy
# distortion?) from retrieval quality, which is a separate SM-R3/SM-R13 concern.
RETRIEVE_K = 40


def _retrieve(facts: List[SemanticFact], question: str, k: int = RETRIEVE_K) -> List[SemanticFact]:
    active = [f for f in facts if f.is_active]
    if len(active) <= k:
        return active
    scored = sorted(active, key=lambda f: lexical_similarity(question, f.statement), reverse=True)
    return scored[:k]


def qa_accuracy(
    facts: List[SemanticFact],
    qa_pairs: List[Dict[str, str]],
    llm: LLMClient,
    usage: UsageStats,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Retrieve facts, answer with the LLM, judge against gold. Sync."""
    pairs = qa_pairs[:limit] if limit else qa_pairs
    correct = 0
    for qa in pairs:
        ctx = "\n".join(f"- {f.statement}" for f in _retrieve(facts, qa["question"]))
        ans = llm.complete(_QA_SYSTEM, f"Facts:\n{ctx}\n\nQuestion: {qa['question']}", usage)
        verdict = llm.complete(
            _JUDGE_SYSTEM,
            f"Question: {qa['question']}\nGold: {qa['answer']}\nAnswer: {ans}",
            usage,
        )
        if verdict.strip().upper().startswith("YES"):
            correct += 1
    n = len(pairs)
    return {"qa_correct": correct, "qa_total": n, "qa_accuracy": round(correct / n, 4) if n else None}


async def aqa_accuracy(
    facts: List[SemanticFact],
    qa_pairs: List[Dict[str, str]],
    llm: LLMClient,
    usage: UsageStats,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Async variant of ``qa_accuracy``."""
    pairs = qa_pairs[:limit] if limit else qa_pairs
    correct = 0
    for qa in pairs:
        ctx = "\n".join(f"- {f.statement}" for f in _retrieve(facts, qa["question"]))
        ans = await llm.acomplete(_QA_SYSTEM, f"Facts:\n{ctx}\n\nQuestion: {qa['question']}", usage)
        verdict = await llm.acomplete(
            _JUDGE_SYSTEM,
            f"Question: {qa['question']}\nGold: {qa['answer']}\nAnswer: {ans}",
            usage,
        )
        if verdict.strip().upper().startswith("YES"):
            correct += 1
    n = len(pairs)
    return {"qa_correct": correct, "qa_total": n, "qa_accuracy": round(correct / n, 4) if n else None}
