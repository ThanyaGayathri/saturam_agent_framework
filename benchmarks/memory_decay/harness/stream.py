"""Synthetic episode-stream generator.

Design goals:
    - Deterministic (seeded) and fully offline. Relevance is a controlled knob:
      a memory's "embedding" is its topic base vector plus small noise, so
      same-topic relevance is high and cross-topic relevance is low. This
      isolates the *decay* signal from embedding noise.
    - Exercises the behaviours decay must handle:
        * durable facts   -> valid forever, high importance (e.g. "user is vegan")
        * fact versions   -> a fact updated over time; older versions go STALE but
                             remain physically stored (models UPDATE consolidation)
        * ephemeral facts -> short valid window then expire (e.g. "flight is delayed")
        * noise           -> never queried; models unrelated clutter / growth
    - Ground truth: at any query time the correct answer is the currently-valid
      version of the topic. Older versions are "stale" and should be ranked down
      (and eventually pruned).
"""

from __future__ import annotations

from typing import List

import numpy as np

from harness.episode import FOREVER, Episode, Query, Stream
from harness.vocab import memory_text, query_text, subject_pairs


def _unit(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return vec
    return vec / norm


def _topic_vector(
    rng: np.random.Generator, base: np.ndarray, jitter: float
) -> np.ndarray:
    """A point near a topic's base direction (same fact, slightly different words).

    ``jitter`` is the *total* noise magnitude relative to the unit base vector, so
    per-dimension std is scaled by 1/sqrt(dim). This keeps same-topic cosine high
    (~1/sqrt(1+jitter^2)) and cross-topic cosine near 0, regardless of ``dim``.
    """
    dim = base.shape[0]
    noise = rng.normal(0.0, jitter / np.sqrt(dim), size=base.shape)
    return _unit(base + noise)


def generate_stream(
    seed: int = 7,
    horizon_hours: float = 24.0 * 90.0,  # 90 days
    dim: int = 32,
    n_durable: int = 25,
    n_versioned: int = 40,
    n_ephemeral: int = 25,
    n_noise: int = 200,
    max_versions: int = 5,
    ephemeral_ttl_hours: float = 24.0 * 3.0,
    queries_per_topic: float = 3.0,
    topic_jitter: float = 0.35,
    query_jitter: float = 0.30,
    importance_noise: float = 0.0,
) -> Stream:
    """Generate a reproducible synthetic episode stream.

    Args:
        seed: RNG seed.
        horizon_hours: Length of the simulated clock, in hours.
        dim: Dimensionality of the synthetic embedding space.
        n_durable: Number of durable (never-superseded) topics.
        n_versioned: Number of topics whose fact is updated over time.
        n_ephemeral: Number of topics emitting short-lived, expiring facts.
        n_noise: Number of never-queried clutter memories.
        max_versions: Max fact versions for a versioned topic.
        ephemeral_ttl_hours: Validity window of an ephemeral fact.
        queries_per_topic: Mean number of queries per (queryable) topic.
        topic_jitter: Noise added to a topic base vector per memory.
        query_jitter: Noise added to a topic base vector per query.
        importance_noise: In [0, 1]. Decorrelates the assigned ``importance`` from
            ``kind``. At 0 (default) importance is the clean kind-based oracle; at 1
            it is a fully independent uniform draw. Values in between model an
            imperfectly calibrated importance scorer. See ``confound.py``. This is
            the de-circularization control for the importance_weighted comparison:
            a production importance signal is noisy, so the decision must be shown
            to survive that noise rather than rely on a planted oracle.

    Returns:
        A fully populated :class:`Stream`.
    """
    rng = np.random.default_rng(seed)

    def importance(lo: float, hi: float) -> float:
        """Kind-based importance prior, optionally decorrelated by importance_noise."""
        oracle = float(rng.uniform(lo, hi))
        if importance_noise <= 0.0:
            return oracle  # no extra RNG draw -> default runs reproduce exactly
        indep = float(rng.uniform(0.0, 1.0))
        return float(
            np.clip(
                (1.0 - importance_noise) * oracle + importance_noise * indep, 0.0, 1.0
            )
        )

    episodes: List[Episode] = []
    queries: List[Query] = []

    topic_id = 0
    mem_id = 0
    query_id = 0

    def next_mem_id() -> str:
        nonlocal mem_id
        mem_id += 1
        return f"m{mem_id:05d}"

    def next_query_id() -> str:
        nonlocal query_id
        query_id += 1
        return f"q{query_id:05d}"

    # Per-topic record of (episode, valid_from, valid_until) so we can build GT.
    topic_versions: dict = {}

    # Assign each topic a distinct (person, attribute) subject for its text form.
    # Text is only consumed by the embedding relevance mode; the synthetic vectors
    # are generated regardless so the default (offline) mode is unaffected.
    pairs = subject_pairs()
    total_topics = n_durable + n_versioned + n_ephemeral + n_noise
    if total_topics > len(pairs):
        raise ValueError(
            f"Need {total_topics} distinct subjects but vocab has only {len(pairs)}"
        )
    subject_order = rng.permutation(len(pairs))

    def subject_for(tid: int) -> tuple:
        person, attribute, values = pairs[int(subject_order[tid])]
        return person, attribute, values

    def pick_values(values: List[str], count: int) -> List[str]:
        """`count` distinct values (so consecutive versions genuinely differ)."""
        take = min(count, len(values))
        chosen = [values[int(i)] for i in rng.permutation(len(values))[:take]]
        while len(chosen) < count:  # only if count exceeds pool; cycle to fill
            chosen.append(values[len(chosen) % len(values)])
        return chosen

    def base_vector() -> np.ndarray:
        return _unit(rng.normal(0.0, 1.0, size=dim))

    # ---- Durable topics: single version, valid forever, high importance ----
    for _ in range(n_durable):
        base = base_vector()
        person, attribute, values = subject_for(topic_id)
        created = float(rng.uniform(0.0, horizon_hours * 0.5))
        ep = Episode(
            memory_id=next_mem_id(),
            topic_id=topic_id,
            vector=_topic_vector(rng, base, topic_jitter),
            created_at=created,
            importance=importance(0.75, 1.0),
            kind="durable",
            valid_from=created,
            valid_until=FOREVER,
            text=memory_text(person, attribute, pick_values(values, 1)[0]),
        )
        episodes.append(ep)
        topic_versions[topic_id] = (base, [ep])
        topic_id += 1

    # ---- Versioned topics: a fact updated over time; old versions go stale ----
    for _ in range(n_versioned):
        base = base_vector()
        person, attribute, values = subject_for(topic_id)
        n_versions = int(rng.integers(2, max_versions + 1))
        version_values = pick_values(values, n_versions)
        times = sorted(rng.uniform(0.0, horizon_hours, size=n_versions).tolist())
        versions: List[Episode] = []
        for i, t in enumerate(times):
            valid_until = times[i + 1] if i + 1 < n_versions else FOREVER
            ep = Episode(
                memory_id=next_mem_id(),
                topic_id=topic_id,
                vector=_topic_vector(rng, base, topic_jitter),
                created_at=float(t),
                importance=importance(0.4, 0.85),
                kind="fact_version",
                valid_from=float(t),
                valid_until=float(valid_until),
                text=memory_text(person, attribute, version_values[i]),
            )
            versions.append(ep)
        for i in range(len(versions) - 1):
            versions[i].superseded_by = versions[i + 1].memory_id
        episodes.extend(versions)
        topic_versions[topic_id] = (base, versions)
        topic_id += 1

    # ---- Ephemeral topics: short-lived facts that expire, leaving a gap ----
    for _ in range(n_ephemeral):
        base = base_vector()
        person, attribute, values = subject_for(topic_id)
        n_events = int(rng.integers(2, 5))
        event_values = pick_values(values, n_events)
        starts = sorted(rng.uniform(0.0, horizon_hours, size=n_events).tolist())
        versions = []
        for i, t in enumerate(starts):
            ep = Episode(
                memory_id=next_mem_id(),
                topic_id=topic_id,
                vector=_topic_vector(rng, base, topic_jitter),
                created_at=float(t),
                importance=importance(0.1, 0.4),
                kind="ephemeral",
                valid_from=float(t),
                valid_until=float(min(t + ephemeral_ttl_hours, horizon_hours)),
                text=memory_text(person, attribute, event_values[i]),
            )
            versions.append(ep)
        episodes.extend(versions)
        topic_versions[topic_id] = (base, versions)
        topic_id += 1

    # ---- Noise: unrelated clutter, never queried (drives store growth) ----
    for _ in range(n_noise):
        base = base_vector()
        person, attribute, values = subject_for(topic_id)
        created = float(rng.uniform(0.0, horizon_hours))
        episodes.append(
            Episode(
                memory_id=next_mem_id(),
                topic_id=topic_id,
                vector=_topic_vector(rng, base, topic_jitter),
                created_at=created,
                importance=importance(0.0, 0.3),
                kind="noise",
                valid_from=created,
                valid_until=FOREVER,
                text=memory_text(person, attribute, pick_values(values, 1)[0]),
            )
        )
        topic_id += 1

    # ---- Queries over queryable topics (durable/versioned/ephemeral) ----
    for tid, (base, versions) in topic_versions.items():
        person, attribute, _ = subject_for(tid)
        q_text = query_text(person, attribute)
        n_q = max(1, int(rng.poisson(queries_per_topic)))
        for _ in range(n_q):
            # Query some time after the topic's first fact appears.
            first_created = min(v.created_at for v in versions)
            if first_created >= horizon_hours:
                continue
            at = float(rng.uniform(first_created, horizon_hours))
            gt_ids = [v.memory_id for v in versions if v.is_valid_at(at)]
            if not gt_ids:
                # No valid fact at this instant (ephemeral gap) -> skip; a query
                # with no correct answer is not informative for ranking quality.
                continue
            queries.append(
                Query(
                    query_id=next_query_id(),
                    topic_id=tid,
                    at=at,
                    vector=_topic_vector(rng, base, query_jitter),
                    gt_ids=gt_ids,
                    text=q_text,
                )
            )

    queries.sort(key=lambda q: q.at)
    return Stream(
        episodes=episodes,
        queries=queries,
        horizon_hours=horizon_hours,
        dim=dim,
        seed=seed,
    )


async def agenerate_stream(**kwargs) -> Stream:
    """Async variant of :func:`generate_stream` (generation is pure/CPU-bound)."""
    return generate_stream(**kwargs)
