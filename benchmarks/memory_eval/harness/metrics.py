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

- all_hops_hit@k = 1 iff E ⊆ top-k, over MULTI-HOP probes only (|E| >= 2; else
                   None). The strict "did we retrieve the whole hop chain" signal
                   (SM-R9); hit@k/recall@k reward partial retrieval, this does not.

Probes with no evidence (DMR-thin, abstention) return None and are EXCLUDED from
retrieval means — never scored as 0, which would be a fabricated penalty.

NOTE (graph/PPR): these are lexical-overlap retrieval metrics over turn-ids. A
graph-traversal / PPR retrieval path and a bridge-passage graph dataset (the
SM-R9 / SM-R10-SS12.1(c) deferral) are scoped to SM-X7 behind the same seam — see
findings/03. ``all_hops_hit@k`` is the metric that validation will report.

QA CORRECTNESS: a ``Judge`` verdict per probe. accuracy = mean(correct). Reported
overall, per canonical category, and with a Wilson 95% CI so close options are
not ranked inside noise (skill §5).

TEMPORAL: QA accuracy restricted to categories {temporal, knowledge_update,
as_of_t}.

BI-TEMPORAL ("true at T"): QA accuracy on {as_of_t, knowledge_update} PLUS a
two-sided as-of leak check evaluated across the TOP-K (not top-1 alone). SM-R10
canonical: a fact is true at T iff ``t_valid <= T < t_invalid`` (BOTH bounds
required; SPEC §7 pitfall). For any probe carrying an ``as_of`` = T, EVERY top-k
retrieved turn must be ``valid_at(T)``. ``as_of_leak_rate`` = fraction of probes
with ANY invalid top-k turn (future, or stale-superseded ``t_invalid <= T``);
``as_of_top1_leak_rate`` keeps the head-only signal; ``as_of_first_leak_rank`` is
the shallowest leaking rank. Lower is better. (Top-1-only would miss a stale fact
returned at rank 2..k — the same failure one rank down.)

DECAY: for a decay probe naming a stale turn D (``decay_target_turn_id``),
``decay_suppression@k`` = fraction of decay probes where D is NOT in top-k
(higher is better); ``decay_outranked`` = fraction where every gold evidence turn
is ranked above D (or D absent).

LATENCY: mean / p50 / p95 / max over per-op wall-clock samples, for ingest and
query (see stats.summarize; p95 matches the seed PerformanceEval convention).

COST: measured tokens x price table (see usage.UsageStats.cost_usd).

MEMORY FOOTPRINT (labelless consolidation signal): {items, tokens} = the provider's
stored-memory size. SM-R7's ``composite_quality`` is fully label-gated (needs
``TargetFact``s) so it is not run here; footprint is the reference-free companion the
composite docstring says to read alongside — a consolidating provider shrinks it vs a
raw store. Reported, not gated (no ratified SLO yet).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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


def all_hops_hit_at_k(evidence_ids: Sequence[str], retrieved_ids: Sequence[str], k: int) -> Optional[float]:
    """Multi-hop retrieval metric (SM-R9's discriminative signal).

    ``hit@k`` fires if ANY one supporting turn is retrieved; ``recall@k`` is
    fractional. Neither captures the multi-hop requirement that ALL bridge turns
    be present to answer. ``all_hops_hit@k`` = 1 iff *every* gold ``evidence_id``
    is in top-k, else 0 — the strict "did we retrieve the whole hop chain" score.
    Only defined for multi-hop probes (>= 2 evidence ids); single-hop -> None.
    """
    evidence = set(evidence_ids)
    if len(evidence) < 2:
        return None
    return 1.0 if evidence <= set(retrieved_ids[:k]) else 0.0


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
    footprint: Optional[Dict[str, int]] = None,
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
    # Multi-hop retrieval: all bridge turns present in top-k (SM-R9's signal). Over
    # multi-hop probes only (>= 2 gold evidence ids); single-hop returns None.
    all_hops = _mean_opt([all_hops_hit_at_k(r.probe.evidence_ids, r.retrieved_ids, k) for r in records])
    n_multihop = sum(1 for r in records if len(set(r.probe.evidence_ids)) >= 2)

    # Bi-temporal.
    bt_records = [r for r in records if r.probe.category in {"as_of_t", "knowledge_update"}]
    bitemporal_acc = _acc(bt_records)
    # Two-sided "true at T" leak (SM-R10: valid iff t_valid <= T < t_invalid; BOTH
    # bounds required — future OR stale-superseded). Evaluated across the WHOLE
    # top-k, not top-1 alone: a stale/superseded fact returned at rank 2..k is the
    # same failure one rank down, so a probe leaks if ANY top-k turn is invalid at
    # T. ``as_of_top1_leak_rate`` keeps the head-only signal; ``as_of_first_leak_rank``
    # (0-indexed) is the shallowest leaking rank seen. Applies to every probe that
    # carries an as_of (as_of_t AND knowledge_update).
    as_of_records = [r for r in records if r.probe.as_of is not None]
    leaks_topk = 0
    leaks_top1 = 0
    leak_measured = 0
    first_leak_ranks: List[int] = []
    for r in as_of_records:
        T = r.probe.as_of or 0
        present: List[Tuple[int, Turn]] = []
        for i, rid in enumerate(r.retrieved_ids[:k]):
            turn = turn_index.get(rid)
            if turn is not None:
                present.append((i, turn))
        if not present:
            continue
        leak_measured += 1
        if not present[0][1].valid_at(T):
            leaks_top1 += 1
        invalid_ranks = [i for i, t in present if not t.valid_at(T)]
        if invalid_ranks:
            leaks_topk += 1
            first_leak_ranks.append(invalid_ranks[0])
    as_of_leak_rate = (leaks_topk / leak_measured) if leak_measured else None
    as_of_top1_leak_rate = (leaks_top1 / leak_measured) if leak_measured else None
    as_of_first_leak_rank = (sum(first_leak_ranks) / len(first_leak_ranks)) if first_leak_ranks else None

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
        "retrieval": {
            "recall_at_k": recall,
            "hit_at_k": hit,
            "ndcg_at_k": ndcg,
            "all_hops_hit_at_k": all_hops,
            "n_multihop": n_multihop,
        },
        "bitemporal": {
            "accuracy": bitemporal_acc,
            "as_of_leak_rate": as_of_leak_rate,
            "as_of_top1_leak_rate": as_of_top1_leak_rate,
            "as_of_first_leak_rank": as_of_first_leak_rank,
        },
        "decay": decay,
        # Labelless consolidation signal: store size (items + tokens). SM-R7's
        # composite_quality is label-gated and not run here; this footprint is the
        # reference-free companion it explicitly says to read alongside.
        "memory_footprint": footprint,
        "latency_ms": {
            "ingest": summarize(list(ingest_ms or [])),
            "query": summarize([r.query_ms for r in records]),
            "answer": summarize([r.answer_ms for r in records]),
        },
        "cost": (usage.to_dict() if usage else None),
    }
