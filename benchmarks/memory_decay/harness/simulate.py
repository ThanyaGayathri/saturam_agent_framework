"""The simulation loop.

A single chronological pass over merged events (queries + prune ticks) so that
reinforcement-on-access and pruning both see the same evolving state:

    for each event in time order:
        query tick -> rank the live store by strategy.score, record quality,
                      then reinforce (on_access) every memory injected into top-k
        prune tick -> drop every live memory the strategy deems prunable, record

"Access" == injection into the top-k retrieval context (see DECISION.md).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from harness.episode import Query, Stream
from harness.metrics import PruneMetrics, QueryMetrics, RunResult, age_bucket, ndcg_at_k
from harness.strategies import DecayStrategy


def _relevance_all(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of a query against every episode (all unit vectors)."""
    return matrix @ query_vec


def run_simulation(
    stream: Stream,
    strategy: DecayStrategy,
    k: int = 5,
    prune: bool = False,
    prune_interval_hours: float = 24.0 * 7.0,
    reinforce_policy: str = "all_topk",
) -> RunResult:
    """Run one strategy over one stream and return its metrics.

    Args:
        stream: The synthetic episode stream.
        strategy: The decay strategy to evaluate (reset internally).
        k: Retrieval depth (top-k) for quality metrics and reinforcement.
        prune: If True, run periodic prune ticks and measure pruning behaviour.
        prune_interval_hours: Cadence of prune ticks.
        reinforce_policy: What counts as "access" for reinforcement:
            - ``"all_topk"`` — reinforce every memory injected into context (the
              default modelled in DECISION §3).
            - ``"valid_only"`` — reinforce only the top-k items that are actually
              ground-truth relevant (the stricter rule §3 waves off); tests whether
              reinforcing stale co-topic items matters, especially under embeddings.

    Returns:
        A :class:`RunResult`.
    """
    strategy.reset()

    episodes = stream.episodes
    n = len(episodes)
    index_of = {e.memory_id: i for i, e in enumerate(episodes)}
    matrix = np.stack([e.vector for e in episodes]) if n else np.zeros((0, stream.dim))
    created = np.array([e.created_at for e in episodes]) if n else np.zeros(0)
    alive = np.ones(n, dtype=bool)

    # Precompute the latest time each memory is ground truth ("last needed"), used
    # for prune scoring: a memory pruned after its last-needed time forgets nothing.
    max_gt_time: Dict[str, float] = {}
    for q in stream.queries:
        for mid in q.gt_ids:
            if q.at > max_gt_time.get(mid, -1.0):
                max_gt_time[mid] = q.at

    # Members per topic, so we can tell which queries are "stale-capable" (a topic
    # with a superseded sibling present) — the only queries that can produce a
    # same-topic stale hit. Durable/first-appearance queries structurally cannot.
    topic_members: Dict[int, List[int]] = {}
    for i, e in enumerate(episodes):
        topic_members.setdefault(e.topic_id, []).append(i)

    # Strategy-agnostic "last surfaced" clock (age-since-access), tracked here so
    # the idle-age recall bucket measures decay-resistance uniformly across
    # strategies (decay acts on now - last_accessed_at, not fact calendar age).
    last_access: Dict[str, float] = {e.memory_id: e.created_at for e in episodes}

    # Accumulators.
    qm = QueryMetrics()
    age_hit: Dict[str, float] = {}
    age_cnt: Dict[str, int] = {}
    idle_hit: Dict[str, float] = {}
    idle_cnt: Dict[str, int] = {}
    stale_versioned_sum = 0.0
    stale_versioned_cnt = 0
    per_query_recall: Dict[str, float] = {}
    per_query_ndcg: Dict[str, float] = {}

    pm = PruneMetrics(enabled=prune)
    # Disposable-by-horizon universe for prune recall: anything not valid at the
    # horizon (superseded/expired) PLUS never-queried noise (FOREVER-valid but, per
    # DECISION §6, desirable to shed). This is the coherent "dead" set for both
    # precision and recall.
    dead_universe_ids = {
        e.memory_id
        for e in episodes
        if e.valid_until <= stream.horizon_hours or e.kind == "noise"
    }
    pruned_ids: List[Tuple[str, float]] = []  # (memory_id, prune_time)

    # Build the event timeline: queries + (optionally) prune ticks.
    events: List[Tuple[float, str, Optional[Query]]] = [
        (q.at, "query", q) for q in stream.queries
    ]
    if prune:
        tick = prune_interval_hours
        while tick <= stream.horizon_hours:
            events.append((tick, "prune", None))
            tick += prune_interval_hours
    events.sort(key=lambda ev: (ev[0], 0 if ev[1] == "query" else 1))

    for now, kind, payload in events:
        live_idx = np.nonzero(alive & (created <= now))[0]
        if kind == "query" and payload is not None:
            query = payload
            gt = set(query.gt_ids)
            if live_idx.size == 0:
                continue
            rel = _relevance_all(query.vector, matrix[live_idx])
            scores = np.array(
                [
                    strategy.score(episodes[gi], float(rel[j]), now)
                    for j, gi in enumerate(live_idx)
                ]
            )
            order = np.argsort(-scores)[:k]
            top_idx = [int(live_idx[o]) for o in order]

            hit_flags = [1 if episodes[gi].memory_id in gt else 0 for gi in top_idx]
            n_hit = sum(hit_flags)
            n_rel = len(gt)
            qm.precision_at_k += n_hit / k
            qm.recall_at_k += n_hit / n_rel if n_rel else 0.0
            ndcg_val = ndcg_at_k(hit_flags, n_rel, k)
            qm.ndcg_at_k += ndcg_val
            per_query_recall[query.query_id] = n_hit / n_rel if n_rel else 0.0
            per_query_ndcg[query.query_id] = ndcg_val
            mrr = 0.0
            for rank, flag in enumerate(hit_flags):
                if flag:
                    mrr = 1.0 / (rank + 1)
                    break
            qm.mrr += mrr

            # Stale hits: retrieved items on the query's topic that are superseded.
            stale = sum(
                episodes[gi].topic_id == query.topic_id
                and episodes[gi].is_stale_at(now)
                for gi in top_idx
            )
            qm.stale_hit_rate += stale / k
            qm.n_queries += 1

            # Restricted stale-hit: only over "stale-capable" queries — a superseded
            # same-topic sibling is present now — so the scale isn't diluted by
            # durable/first-appearance queries that can never produce a stale hit.
            same_topic_present = [
                mi
                for mi in topic_members.get(query.topic_id, [])
                if episodes[mi].created_at <= now
            ]
            if any(episodes[mi].is_stale_at(now) for mi in same_topic_present):
                stale_versioned_sum += stale / k
                stale_versioned_cnt += 1

            recall_val = n_hit / n_rel if n_rel else 0.0

            # Recall by fact calendar-age (transparency only — see metrics.py).
            valid_now = [episodes[gi] for gi in [index_of[mid] for mid in gt]]
            fact_age = (
                min((now - e.valid_from) for e in valid_now) if valid_now else 0.0
            )
            ab = age_bucket(fact_age)
            age_hit[ab] = age_hit.get(ab, 0.0) + recall_val
            age_cnt[ab] = age_cnt.get(ab, 0) + 1

            # Recall by idle-age (time since the current fact was last surfaced) —
            # the clock decay acts on; this is the decay-resistance metric.
            idle_age = min((now - last_access[mid]) for mid in gt) if gt else 0.0
            ib = age_bucket(idle_age)
            idle_hit[ib] = idle_hit.get(ib, 0.0) + recall_val
            idle_cnt[ib] = idle_cnt.get(ib, 0) + 1

            # Reinforce accessed memories (also advance the metric clock). Under
            # "valid_only", only the ground-truth-relevant hits count as access.
            for gi in top_idx:
                if (
                    reinforce_policy == "valid_only"
                    and episodes[gi].memory_id not in gt
                ):
                    continue
                strategy.on_access(episodes[gi], now)
                last_access[episodes[gi].memory_id] = now
        else:
            # Prune tick.
            for gi in live_idx:
                ep = episodes[int(gi)]
                if strategy.is_prunable(ep, now):
                    alive[int(gi)] = False
                    pruned_ids.append((ep.memory_id, now))
            pm.retained_curve.append(float(np.count_nonzero(alive & (created <= now))))

    # Finalise quality averages.
    nq = max(qm.n_queries, 1)
    qm.precision_at_k /= nq
    qm.recall_at_k /= nq
    qm.ndcg_at_k /= nq
    qm.mrr /= nq
    qm.stale_hit_rate /= nq
    qm.stale_hit_rate_versioned = (
        stale_versioned_sum / stale_versioned_cnt if stale_versioned_cnt else 0.0
    )
    qm.recall_by_age_bucket = {b: age_hit[b] / age_cnt[b] for b in sorted(age_hit)}
    qm.recall_by_idle_bucket = {b: idle_hit[b] / idle_cnt[b] for b in sorted(idle_hit)}

    # Finalise pruning metrics. Precision uses "never needed again as of prune time"
    # (need-based), so shedding never-queried noise counts as correct; false-forget
    # is its exact complement. Recall uses the disposable-by-horizon universe (which
    # includes noise), so precision and recall are coherent over consistent sets.
    if prune:
        pm.n_pruned = len(pruned_ids)
        correct = 0
        for mid, ptime in pruned_ids:
            if max_gt_time.get(mid, -1.0) < ptime:
                correct += 1  # never needed at/after prune -> correct to prune
            else:
                pm.false_forget += 1  # still needed later -> a real forget
        pm.prune_precision = correct / pm.n_pruned if pm.n_pruned else 0.0
        pruned_dead = sum(1 for mid, _ in pruned_ids if mid in dead_universe_ids)
        pm.prune_recall = (
            pruned_dead / len(dead_universe_ids) if dead_universe_ids else 0.0
        )
        pm.retained_final = int(np.count_nonzero(alive))

    return RunResult(
        strategy=strategy.name,
        quality=qm,
        pruning=pm,
        k=k,
        params=_params_of(strategy),
        per_query_recall=per_query_recall,
        per_query_ndcg=per_query_ndcg,
    )


def _params_of(strategy: DecayStrategy) -> Dict[str, float]:
    """Extract the numeric tuning parameters of a strategy for the report."""
    keys = (
        "alpha",
        "beta",
        "gamma",
        "base",
        "half_life_hours",
        "ttl_hours",
        "base_half_life_hours",
        "imp_factor",
        "s0_hours",
        "reinforce_gain_hours",
        "prune_threshold",
    )
    out: Dict[str, float] = {}
    for key in keys:
        val = getattr(strategy, key, None)
        if isinstance(val, (int, float)):
            out[key] = float(val)
    return out


async def arun_simulation(
    stream: Stream,
    strategy: DecayStrategy,
    k: int = 5,
    prune: bool = False,
    prune_interval_hours: float = 24.0 * 7.0,
    reinforce_policy: str = "all_topk",
) -> RunResult:
    """Async variant of :func:`run_simulation` (CPU-bound; runs inline)."""
    return run_simulation(
        stream=stream,
        strategy=strategy,
        k=k,
        prune=prune,
        prune_interval_hours=prune_interval_hours,
        reinforce_policy=reinforce_policy,
    )
