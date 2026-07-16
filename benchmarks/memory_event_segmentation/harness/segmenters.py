"""Candidate segmenters (offline).

All offline candidates live here behind one interface so the runner treats them
uniformly. The LLM/SLM candidate lives in ``llm.py`` (it also reports latency and
cost) and is wired in by the runner. Per the SM-R6 working model, none of these
touch framework core — only the chosen one is promoted later (SM-C*).

Interface (each with a sync + async variant, per the repo rule):

    segment(stream)  -> List[int]    boundary positions (indices that open a new
                                     episode; a boundary at i sits between event
                                     i-1 and i). 0 is never returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from harness.stream import Stream
from harness.surprise import em_llm_boundaries


@dataclass
class Segmenter:
    """Base: no internal boundaries (the single-episode baseline)."""

    name: str = "base"

    def params(self) -> Dict[str, object]:
        return {}

    def segment(self, stream: Stream) -> List[int]:
        return []

    async def asegment(self, stream: Stream) -> List[int]:
        return self.segment(stream)


@dataclass
class SingleSession(Segmenter):
    """Naive baseline: the whole stream is one episode (under-segments)."""

    name: str = "single_session"


@dataclass
class PerTurn(Segmenter):
    """Naive baseline: every message opens a new episode (over-segments)."""

    name: str = "per_turn"

    def segment(self, stream: Stream) -> List[int]:
        return list(range(1, stream.n_events))


@dataclass
class FixedWindow(Segmenter):
    """Deterministic: a boundary every ``n`` messages."""

    name: str = "fixed_window"
    n: int = 10

    def params(self) -> Dict[str, object]:
        return {"n": self.n}

    def segment(self, stream: Stream) -> List[int]:
        step = max(1, self.n)
        return [i for i in range(step, stream.n_events, step)]


@dataclass
class TimeGap(Segmenter):
    """Boundary when the inter-message time gap exceeds ``gap_s`` seconds."""

    name: str = "time_gap"
    gap_s: float = 600.0

    def params(self) -> Dict[str, object]:
        return {"gap_s": self.gap_s}

    def segment(self, stream: Stream) -> List[int]:
        ev = stream.events
        return [i for i in range(1, len(ev)) if (ev[i].created_at - ev[i - 1].created_at) > self.gap_s]


@dataclass
class EmbeddingDrift(Segmenter):
    """TextTiling-style semantic drift: boundaries at deep similarity valleys.

    For each gap ``g`` we compare the mean embedding of the ``block`` events on
    either side; a *depth score* measures how far the local similarity dips below
    its neighbouring peaks. Gaps whose depth exceeds ``mu + threshold_c*sigma``
    (and are local minima, spaced >= ``min_seg``) become boundaries.
    """

    name: str = "embedding_drift"
    block: int = 3
    threshold_c: float = 0.5
    min_seg: int = 3

    def params(self) -> Dict[str, object]:
        return {"block": self.block, "threshold_c": self.threshold_c, "min_seg": self.min_seg}

    def _gap_similarity(self, mat: np.ndarray) -> np.ndarray:
        n = mat.shape[0]
        w = self.block
        sims = np.ones(n)  # index g = gap before event g; sims[0] unused
        for g in range(1, n):
            left = mat[max(0, g - w) : g]
            right = mat[g : g + w]
            lm = left.mean(axis=0)
            rm = right.mean(axis=0)
            ln = np.linalg.norm(lm)
            rn = np.linalg.norm(rm)
            sims[g] = float(lm @ rm / (ln * rn)) if ln > 0 and rn > 0 else 1.0
        return sims

    def _depth_scores(self, sims: np.ndarray) -> np.ndarray:
        n = sims.shape[0]
        depth = np.zeros(n)
        for g in range(1, n):
            # Climb left to the local peak.
            lpeak = sims[g]
            j = g - 1
            while j >= 1 and sims[j] >= lpeak:
                lpeak = sims[j]
                j -= 1
            # Climb right to the local peak.
            rpeak = sims[g]
            j = g + 1
            while j < n and sims[j] >= rpeak:
                rpeak = sims[j]
                j += 1
            depth[g] = (lpeak - sims[g]) + (rpeak - sims[g])
        return depth

    def segment(self, stream: Stream) -> List[int]:
        mat = stream.matrix()
        n = mat.shape[0]
        if n < 2 * self.block:
            return []
        sims = self._gap_similarity(mat)
        depth = self._depth_scores(sims)
        interior = depth[1:]
        mu = float(interior.mean())
        sigma = float(interior.std())
        threshold = mu + self.threshold_c * sigma
        boundaries: List[int] = []
        last = 0
        for g in range(1, n):
            is_local_min = sims[g] <= sims[g - 1] and (g + 1 >= n or sims[g] <= sims[g + 1])
            if depth[g] > threshold and is_local_min and (g - last) >= self.min_seg:
                boundaries.append(g)
                last = g
        return boundaries


@dataclass
class EmLlm(Segmenter):
    """EM-LLM: Bayesian surprise + modularity/conductance graph refinement."""

    name: str = "surprise_em_llm"
    gamma: float = 1.0
    min_seg: int = 3
    ema_alpha: float = 0.5
    refine: bool = True
    radius: int = 3
    objective: str = "modularity"
    passes: int = 3

    def params(self) -> Dict[str, object]:
        return {
            "gamma": self.gamma,
            "min_seg": self.min_seg,
            "ema_alpha": self.ema_alpha,
            "refine": self.refine,
            "radius": self.radius,
            "objective": self.objective,
        }

    def segment(self, stream: Stream) -> List[int]:
        return em_llm_boundaries(
            stream.matrix(),
            gamma=self.gamma,
            min_seg=self.min_seg,
            ema_alpha=self.ema_alpha,
            refine=self.refine,
            radius=self.radius,
            objective=self.objective,
            passes=self.passes,
        )


@dataclass
class Hybrid(Segmenter):
    """Deterministic time-gap backbone UNION a semantic segmenter.

    Time-gap boundaries are near-perfect precision but miss topic switches with no
    pause; the semantic pass recovers those. Time-gap boundaries are kept as
    high-precision anchors and each semantic boundary is added only if it is at
    least ``min_seg`` events from an already-kept boundary (so the two passes do
    not double-cut the same switch).
    """

    name: str = "hybrid_gap_semantic"
    gap_s: float = 600.0
    min_seg: int = 3
    semantic_kind: str = "em_llm"  # "em_llm" | "embedding_drift"

    def params(self) -> Dict[str, object]:
        return {"gap_s": self.gap_s, "min_seg": self.min_seg, "semantic_kind": self.semantic_kind}

    def _semantic(self) -> Segmenter:
        if self.semantic_kind == "embedding_drift":
            return EmbeddingDrift(min_seg=self.min_seg)
        return EmLlm(min_seg=self.min_seg)

    def segment(self, stream: Stream) -> List[int]:
        anchors = TimeGap(gap_s=self.gap_s).segment(stream)
        kept = sorted(anchors)
        for b in self._semantic().segment(stream):
            if all(abs(b - a) >= self.min_seg for a in kept):
                kept.append(b)
                kept.sort()
        return kept


def default_segmenters() -> List[Segmenter]:
    """Offline candidates with provisional default parameters."""
    return [
        SingleSession(),
        PerTurn(),
        FixedWindow(n=10),
        TimeGap(gap_s=600.0),
        EmbeddingDrift(block=3, threshold_c=0.5),
        EmLlm(objective="modularity"),
        Hybrid(semantic_kind="em_llm", name="hybrid_gap_emllm"),
        Hybrid(semantic_kind="embedding_drift", name="hybrid_gap_drift"),
    ]
