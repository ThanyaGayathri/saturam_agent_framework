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
) -> RunResult:
    """Run one strategy over one stream and return its metrics.

    Args:
        stream: The synthetic episode stream.
        strategy: The decay strategy to evaluate (reset internally).
        k: Retrieval depth (top-k) for quality metrics and reinforcement.
        prune: If True, run periodic prune ticks and measure pruning behaviour.
        prune_interval_hours: Cadence of prune ticks.

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

    # Precompute the latest time each memory is ground truth (for prune scoring).
    max_gt_time: Dict[str, float] = {}
    for q in stream.queries:
        for mid in q.gt_ids:
            if q.at > max_gt_time.get(mid, -1.0):
                max_gt_time[mid] = q.at

    # Accumulators.
    qm = QueryMetrics()
    bucket_hit: Dict[str, float] = {}
    bucket_cnt: Dict[str, int] = {}

    pm = PruneMetrics(enabled=prune)
    truly_dead_universe = sum(
        1 for e in episodes if e.valid_until <= stream.horizon_hours
    )
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
            qm.ndcg_at_k += ndcg_at_k(hit_flags, n_rel, k)
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

            # Recall by fact-age bucket (how old the current valid fact is).
            valid_now = [episodes[gi] for gi in [index_of[mid] for mid in gt]]
            fact_age = (
                min((now - e.valid_from) for e in valid_now) if valid_now else 0.0
            )
            b = age_bucket(fact_age)
            bucket_hit[b] = bucket_hit.get(b, 0.0) + (n_hit / n_rel if n_rel else 0.0)
            bucket_cnt[b] = bucket_cnt.get(b, 0) + 1

            # Reinforce every memory injected into context.
            for gi in top_idx:
                strategy.on_access(episodes[gi], now)
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
    qm.recall_by_age_bucket = {
        b: bucket_hit[b] / bucket_cnt[b] for b in sorted(bucket_hit)
    }

    # Finalise pruning metrics.
    if prune:
        eps_by_id = stream.episodes_by_id()
        pm.n_pruned = len(pruned_ids)
        correct = 0
        for mid, ptime in pruned_ids:
            ep = eps_by_id[mid]
            if ep.valid_until <= ptime:
                correct += 1
            if max_gt_time.get(mid, -1.0) >= ptime:
                pm.false_forget += 1
        pm.prune_precision = correct / pm.n_pruned if pm.n_pruned else 0.0
        # Recall: of all memories that die within the horizon, how many got pruned.
        pruned_dead = sum(
            1
            for mid, _ in pruned_ids
            if eps_by_id[mid].valid_until <= stream.horizon_hours
        )
        pm.prune_recall = (
            pruned_dead / truly_dead_universe if truly_dead_universe else 0.0
        )
        pm.retained_final = int(np.count_nonzero(alive))

    return RunResult(
        strategy=strategy.name, quality=qm, pruning=pm, k=k, params=_params_of(strategy)
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
) -> RunResult:
    """Async variant of :func:`run_simulation` (CPU-bound; runs inline)."""
    return run_simulation(
        stream=stream,
        strategy=strategy,
        k=k,
        prune=prune,
        prune_interval_hours=prune_interval_hours,
    )
