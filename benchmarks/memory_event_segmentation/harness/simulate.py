"""Run a segmenter over a stream and score it.

Produces one :class:`SegResult` per (segmenter, stream): boundary P/R/F1 (with
tolerance), Pk, WindowDiff, predicted-segment count, wall-clock latency, and — for
the LLM/SLM candidate — token usage and cost. Offline segmenters have zero cost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from harness.metrics import (
    boundary_prf,
    pk,
    segment_ids_from_boundaries,
    window_diff,
    window_size,
)
from harness.segmenters import Segmenter
from harness.stream import Stream


@dataclass
class SegResult:
    name: str
    params: Dict[str, object]
    precision: float
    recall: float
    f1: float
    pk: float
    window_diff: float
    n_true: int
    n_pred: int
    latency_ms: float
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    extra: Dict[str, object] = field(default_factory=dict)


def score_boundaries(stream: Stream, pred: List[int], tol: int = 1) -> Dict[str, float]:
    """Compute all boundary + segmentation-error metrics for one prediction."""
    true_b = stream.true_boundaries()
    bm = boundary_prf(true_b, pred, tol=tol)
    n = stream.n_events
    k = window_size(n, len(true_b))
    ref_ids = stream.segment_ids()
    hyp_ids = segment_ids_from_boundaries(n, pred)
    return {
        "precision": bm.precision,
        "recall": bm.recall,
        "f1": bm.f1,
        "pk": pk(ref_ids, hyp_ids, k),
        "window_diff": window_diff(ref_ids, hyp_ids, k),
        "n_true": float(bm.n_true),
        "n_pred": float(bm.n_pred),
    }


def run_segmenter(stream: Stream, segmenter: Segmenter, tol: int = 1) -> SegResult:
    """Time and score one offline segmenter."""
    start = time.perf_counter()
    pred = segmenter.segment(stream)
    latency_ms = (time.perf_counter() - start) * 1000.0
    m = score_boundaries(stream, pred, tol=tol)
    return SegResult(
        name=segmenter.name,
        params=segmenter.params(),
        precision=m["precision"],
        recall=m["recall"],
        f1=m["f1"],
        pk=m["pk"],
        window_diff=m["window_diff"],
        n_true=int(m["n_true"]),
        n_pred=int(m["n_pred"]),
        latency_ms=latency_ms,
    )


async def arun_segmenter(stream: Stream, segmenter: Segmenter, tol: int = 1) -> SegResult:
    """Async mirror of :func:`run_segmenter`."""
    start = time.perf_counter()
    pred = await segmenter.asegment(stream)
    latency_ms = (time.perf_counter() - start) * 1000.0
    m = score_boundaries(stream, pred, tol=tol)
    return SegResult(
        name=segmenter.name,
        params=segmenter.params(),
        precision=m["precision"],
        recall=m["recall"],
        f1=m["f1"],
        pk=m["pk"],
        window_diff=m["window_diff"],
        n_true=int(m["n_true"]),
        n_pred=int(m["n_pred"]),
        latency_ms=latency_ms,
    )


def run_llm(stream: Stream, detector, tol: int = 1, name: Optional[str] = None) -> SegResult:
    """Run and score the LLM/SLM candidate (real model call)."""
    res = detector.detect(stream)
    m = score_boundaries(stream, res.boundaries, tol=tol)
    return SegResult(
        name=name or f"llm_{detector.provider}",
        params={"provider": detector.provider, "model": res.model_label},
        precision=m["precision"],
        recall=m["recall"],
        f1=m["f1"],
        pk=m["pk"],
        window_diff=m["window_diff"],
        n_true=int(m["n_true"]),
        n_pred=int(m["n_pred"]),
        latency_ms=res.latency_s * 1000.0,
        cost_usd=res.cost_usd,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )


async def arun_llm(stream: Stream, detector, tol: int = 1, name: Optional[str] = None) -> SegResult:
    """Async mirror of :func:`run_llm`."""
    res = await detector.adetect(stream)
    m = score_boundaries(stream, res.boundaries, tol=tol)
    return SegResult(
        name=name or f"llm_{detector.provider}",
        params={"provider": detector.provider, "model": res.model_label},
        precision=m["precision"],
        recall=m["recall"],
        f1=m["f1"],
        pk=m["pk"],
        window_diff=m["window_diff"],
        n_true=int(m["n_true"]),
        n_pred=int(m["n_pred"]),
        latency_ms=res.latency_s * 1000.0,
        cost_usd=res.cost_usd,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )
