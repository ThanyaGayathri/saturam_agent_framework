"""Memory eval metrics — exact definitions.

Every metric is defined here precisely so the plan is not prose. Inputs are
provider-agnostic (lists of ids / verdicts), so any ``MemoryProvider`` scores the
same way.

RETRIEVAL (per probe, over its gold ``evidence_ids`` E and the provider's ranked
``retrieved_ids`` R; top-k := R[:k]):

- recall@k   = |E ∩ top-k| / |E|                         (None if E is empty)
- hit@k      = 1 if E ∩ top-k else 0                      (None if E is empty)
- nDCG@k     = DCG@k / IDCG@k, binary relevance rel_i∈{0,1}
               DCG@k  = Σ_{i=0}^{k-1} rel_i / log2(i+2)
               IDCG@k = Σ_{i=0}^{min(|E|,k)-1} 1 / log2(i+2)

Probes with no evidence (DMR-thin, abstention) return None and are EXCLUDED from
retrieval means — never scored as 0, which would be a fabricated penalty.

QA CORRECTNESS: a ``Judge`` verdict per probe. accuracy = mean(correct). Reported
overall, per canonical category, and with a Wilson 95% CI so close options are
not ranked inside noise (skill §5).

TEMPORAL: QA accuracy restricted to categories {temporal, knowledge_update,
as_of_t}.

BI-TEMPORAL ("true at T"): QA accuracy on {as_of_t, knowledge_update} PLUS an
as-of leak check — for an ``as_of_t`` probe asked at time T, the top-1 retrieved
turn must be valid at T (timestamp <= T). ``as_of_leak_rate`` = fraction whose
top-1 retrieved turn post-dates T (lower is better).

DECAY: for a decay probe naming a stale turn D (``decay_target_turn_id``),
``decay_suppression@k`` = fraction of decay probes where D is NOT in top-k
(higher is better); ``decay_outranked`` = fraction where every gold evidence turn
is ranked above D (or D absent).

LATENCY: mean / p50 / p95 / max over per-op wall-clock samples, for ingest and
query (see stats.summarize; p95 matches the seed PerformanceEval convention).

COST: measured tokens x price table (see usage.UsageStats.cost_usd).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from harness.judge import JudgeResult
from harness.schemas import TEMPORAL_CATEGORIES, Probe, Turn
from harness.stats import proportion_with_ci, summarize
from harness.usage import UsageStats


# --------------------------------------------------------------------------- #
# Pure retrieval metrics.
# --------------------------------------------------------------------------- #
def recall_at_k(evidence_ids: Sequence[str], retrieved_ids: Sequence[str], k: int) -> Optional[float]:
    evidence = set(evidence_ids)
    if not evidence:
        return None
    topk = set(retrieved_ids[:k])
    return len(evidence & topk) / len(evidence)


def hit_at_k(evidence_ids: Sequence[str], retrieved_ids: Sequence[str], k: int) -> Optional[float]:
    evidence = set(evidence_ids)
    if not evidence:
        return None
    return 1.0 if evidence & set(retrieved_ids[:k]) else 0.0


def ndcg_at_k(evidence_ids: Sequence[str], retrieved_ids: Sequence[str], k: int) -> Optional[float]:
    evidence = set(evidence_ids)
    if not evidence:
        return None
    dcg = 0.0
    for i, rid in enumerate(retrieved_ids[:k]):
        if rid in evidence:
            dcg += 1.0 / math.log2(i + 2)
    ideal_hits = min(len(evidence), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else None


# --------------------------------------------------------------------------- #
# Per-probe record the runner fills, then aggregate() turns into a report.
# --------------------------------------------------------------------------- #
@dataclass
class ProbeRecord:
    probe: Probe
    retrieved_ids: List[str]  # ranked ids returned by provider.query
    answer: str
    verdict: JudgeResult
    query_ms: float = 0.0
    answer_ms: float = 0.0


def _acc(records: Sequence[ProbeRecord]) -> Optional[dict]:
    if not records:
        return None
    successes = sum(1 for r in records if r.verdict.correct)
    return proportion_with_ci(successes, len(records))


def _mean_opt(values: Sequence[Optional[float]]) -> Optional[dict]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return {"value": sum(present) / len(present), "n": len(present)}


def aggregate(
    records: Sequence[ProbeRecord],
    *,
    k: int,
    turn_index: Optional[Dict[str, Turn]] = None,
    ingest_ms: Optional[Sequence[float]] = None,
    usage: Optional[UsageStats] = None,
    judge_name: str = "stub",
) -> dict:
    """Turn per-probe records into the full metric report (all definitions above)."""
    turn_index = turn_index or {}

    # QA correctness: overall + per canonical category.
    overall = _acc(records)
    by_cat: Dict[str, Optional[dict]] = {}
    cats = sorted({r.probe.category for r in records})
    for cat in cats:
        by_cat[cat] = _acc([r for r in records if r.probe.category == cat])

    temporal = _acc([r for r in records if r.probe.category in TEMPORAL_CATEGORIES])

    # Retrieval means over probes that HAVE evidence.
    recall = _mean_opt([recall_at_k(r.probe.evidence_ids, r.retrieved_ids, k) for r in records])
    hit = _mean_opt([hit_at_k(r.probe.evidence_ids, r.retrieved_ids, k) for r in records])
    ndcg = _mean_opt([ndcg_at_k(r.probe.evidence_ids, r.retrieved_ids, k) for r in records])

    # Bi-temporal.
    bt_records = [r for r in records if r.probe.category in {"as_of_t", "knowledge_update"}]
    bitemporal_acc = _acc(bt_records)
    as_of_records = [r for r in records if r.probe.category == "as_of_t" and r.probe.as_of is not None]
    leaks = 0
    leak_measured = 0
    for r in as_of_records:
        if not r.retrieved_ids:
            continue
        top1 = turn_index.get(r.retrieved_ids[0])
        if top1 is None:
            continue
        leak_measured += 1
        if top1.timestamp > (r.probe.as_of or 0):
            leaks += 1
    as_of_leak_rate = (leaks / leak_measured) if leak_measured else None

    # Decay.
    decay_records = [r for r in records if r.probe.decay_target_turn_id]
    suppressed = 0
    outranked = 0
    for r in decay_records:
        d = r.probe.decay_target_turn_id
        if d is None:  # decay_records already filters truthy; narrows Optional for typing
            continue
        topk = r.retrieved_ids[:k]
        if d not in topk:
            suppressed += 1
        # every gold evidence ranked above D (or D absent from the full ranking)
        pos = {rid: i for i, rid in enumerate(r.retrieved_ids)}
        d_pos = pos.get(d, math.inf)
        ev_positions = [pos.get(e, math.inf) for e in r.probe.evidence_ids]
        if r.probe.evidence_ids and all(ep < d_pos for ep in ev_positions):
            outranked += 1
    decay = None
    if decay_records:
        decay = {
            "n": len(decay_records),
            "suppression_at_k": suppressed / len(decay_records),
            "outranked": outranked / len(decay_records),
        }

    return {
        "k": k,
        "judge": judge_name,
        "n_probes": len(records),
        "qa_accuracy": overall,
        "qa_accuracy_by_category": by_cat,
        "temporal_accuracy": temporal,
        "retrieval": {"recall_at_k": recall, "hit_at_k": hit, "ndcg_at_k": ndcg},
        "bitemporal": {
            "accuracy": bitemporal_acc,
            "as_of_leak_rate": as_of_leak_rate,
        },
        "decay": decay,
        "latency_ms": {
            "ingest": summarize(list(ingest_ms or [])),
            "query": summarize([r.query_ms for r in records]),
            "answer": summarize([r.answer_ms for r in records]),
        },
        "cost": (usage.to_dict() if usage else None),
    }
