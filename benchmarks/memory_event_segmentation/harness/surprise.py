"""EM-LLM segmentation: Bayesian surprise + graph refinement.

Implements the two-stage EM-LLM approach (Fountas et al., 2024) at the *event*
level:

1. **Surprise boundaries.** EM-LLM places an initial boundary where the model's
   token-level negative log-likelihood exceeds ``mu + gamma * sigma`` of a running
   window. We have no token NLL offline, so we use the embedding-space analog of
   Bayesian surprise: ``S_i = 1 - cos(v_i, c_{i-1})`` where ``c`` is an EMA of the
   preceding context. High surprise == the new event is poorly predicted by recent
   context == a likely topic shift. (With a real LLM this array would be swapped
   for token NLL; see findings/README.md.)

2. **Graph refinement.** Treat events as nodes in a similarity graph
   (``A_ij = max(0, cos(v_i, v_j))``) and slide each surprise boundary within a
   small radius to optimise a graph objective over the induced contiguous
   segments — either **modularity** (maximise) or **conductance** (minimise), the
   two refinements named in the EM-LLM paper.

Everything here is deterministic and offline. The public entry point
``em_llm_boundaries`` has a sync and an async variant.
"""

from __future__ import annotations

from typing import List, Sequence

import numpy as np


def bayesian_surprise(vectors: np.ndarray, ema_alpha: float = 0.5) -> np.ndarray:
    """Embedding-space surprise per event: 1 - cos(v_i, EMA(context < i)).

    ``ema_alpha`` weights the newest event when updating the running context; a
    higher value makes the context more reactive (shorter memory).
    """
    n = vectors.shape[0]
    surprise = np.zeros(n)
    context = vectors[0].copy()
    for i in range(1, n):
        cn = np.linalg.norm(context)
        vn = np.linalg.norm(vectors[i])
        cos = float(vectors[i] @ context / (cn * vn)) if cn > 0 and vn > 0 else 0.0
        surprise[i] = 1.0 - cos
        context = (1.0 - ema_alpha) * context + ema_alpha * vectors[i]
    return surprise


def surprise_boundaries(surprise: np.ndarray, gamma: float = 1.0, min_seg: int = 3) -> List[int]:
    """Threshold surprise at ``mu + gamma*sigma``; enforce a minimum segment length."""
    n = surprise.shape[0]
    if n == 0:
        return []
    mu = float(surprise.mean())
    sigma = float(surprise.std())
    threshold = mu + gamma * sigma
    boundaries: List[int] = []
    last = 0
    for i in range(1, n):
        if surprise[i] > threshold and (i - last) >= min_seg:
            boundaries.append(i)
            last = i
    return boundaries


def similarity_matrix(vectors: np.ndarray) -> np.ndarray:
    """Non-negative cosine similarity graph with a zero diagonal (no self-loops)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = vectors / norms
    sim = unit @ unit.T
    sim = np.clip(sim, 0.0, None)
    np.fill_diagonal(sim, 0.0)
    return sim


def _segment_slices(n: int, boundaries: Sequence[int]) -> List[slice]:
    edges = [0] + sorted(int(b) for b in boundaries) + [n]
    return [slice(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def modularity(adj: np.ndarray, boundaries: Sequence[int]) -> float:
    """Newman modularity of the contiguous partition induced by ``boundaries``."""
    n = adj.shape[0]
    two_m = float(adj.sum())
    if two_m == 0:
        return 0.0
    deg = adj.sum(axis=1)
    q = 0.0
    for sl in _segment_slices(n, boundaries):
        block = float(adj[sl, sl].sum())
        deg_sum = float(deg[sl].sum())
        q += block / two_m - (deg_sum / two_m) ** 2
    return q


def mean_conductance(adj: np.ndarray, boundaries: Sequence[int]) -> float:
    """Mean conductance of the induced segments (lower == cleaner cuts)."""
    n = adj.shape[0]
    deg = adj.sum(axis=1)
    vol_total = float(deg.sum())
    if vol_total == 0:
        return 0.0
    conds: List[float] = []
    for sl in _segment_slices(n, boundaries):
        mask = np.zeros(n, dtype=bool)
        mask[sl] = True
        vol_s = float(deg[mask].sum())
        vol_c = vol_total - vol_s
        if vol_s == 0 or vol_c == 0:
            continue
        cut = float(adj[mask][:, ~mask].sum())
        conds.append(cut / min(vol_s, vol_c))
    return float(np.mean(conds)) if conds else 0.0


def refine_boundaries(
    adj: np.ndarray,
    boundaries: Sequence[int],
    radius: int = 3,
    objective: str = "modularity",
    min_seg: int = 3,
    passes: int = 3,
) -> List[int]:
    """Slide each boundary within +/- ``radius`` to optimise the graph objective.

    ``objective``: "modularity" (maximise) or "conductance" (minimise). Repeated
    for ``passes`` sweeps or until nothing moves. Candidate positions are
    constrained so neighbouring segments never fall below ``min_seg``.
    """
    n = adj.shape[0]
    current = sorted(int(b) for b in boundaries)
    if not current:
        return current

    def score(bs: Sequence[int]) -> float:
        if objective == "conductance":
            return -mean_conductance(adj, bs)  # negate so we always maximise
        return modularity(adj, bs)

    best_score = score(current)
    for _ in range(passes):
        moved = False
        for bi in range(len(current)):
            lo_neighbor = current[bi - 1] if bi > 0 else 0
            hi_neighbor = current[bi + 1] if bi + 1 < len(current) else n
            low = max(lo_neighbor + min_seg, current[bi] - radius)
            high = min(hi_neighbor - min_seg, current[bi] + radius)
            best_pos = current[bi]
            for pos in range(low, high + 1):
                if pos == current[bi]:
                    continue
                trial = list(current)
                trial[bi] = pos
                s = score(trial)
                if s > best_score + 1e-12:
                    best_score = s
                    best_pos = pos
            if best_pos != current[bi]:
                current[bi] = best_pos
                moved = True
        if not moved:
            break
    return sorted(current)


def em_llm_boundaries(
    vectors: np.ndarray,
    gamma: float = 1.0,
    min_seg: int = 3,
    ema_alpha: float = 0.5,
    refine: bool = True,
    radius: int = 3,
    objective: str = "modularity",
    passes: int = 3,
) -> List[int]:
    """Full EM-LLM pipeline: surprise boundaries then optional graph refinement."""
    surprise = bayesian_surprise(vectors, ema_alpha=ema_alpha)
    init = surprise_boundaries(surprise, gamma=gamma, min_seg=min_seg)
    if not refine or not init:
        return init
    adj = similarity_matrix(vectors)
    return refine_boundaries(
        adj,
        init,
        radius=radius,
        objective=objective,
        min_seg=min_seg,
        passes=passes,
    )


async def aem_llm_boundaries(
    vectors: np.ndarray,
    gamma: float = 1.0,
    min_seg: int = 3,
    ema_alpha: float = 0.5,
    refine: bool = True,
    radius: int = 3,
    objective: str = "modularity",
    passes: int = 3,
) -> List[int]:
    """Async mirror of :func:`em_llm_boundaries`."""
    return em_llm_boundaries(
        vectors,
        gamma=gamma,
        min_seg=min_seg,
        ema_alpha=ema_alpha,
        refine=refine,
        radius=radius,
        objective=objective,
        passes=passes,
    )
