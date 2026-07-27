"""End-to-end runner + provider tests (offline, deterministic).

Asserts robust invariants rather than brittle exact scores: the harness runs, the
metrics populate, the plug-and-play seam holds, and the metrics DISCRIMINATE a
naive provider from a time-aware one on the temporal-heavy sets.

Runs under pytest, or standalone: ``python tests/test_runner.py``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.baselines import get_answerer, get_provider  # noqa: E402
from harness.datasets import get_loader  # noqa: E402
from harness.judge import StubJudge  # noqa: E402
from harness.provider import MemoryProvider, RunConfig  # noqa: E402
from harness.runner import arun_benchmark, run_benchmark  # noqa: E402

_CFG = RunConfig(k=5)


def _run(bench_name: str, provider_name: str) -> dict:
    bench = get_loader(bench_name).fixture()
    return run_benchmark(bench, get_provider(provider_name), StubJudge(), get_answerer(), _CFG)


def test_report_has_all_sections() -> None:
    rep = _run("longmemeval", "recency_decay")
    for key in (
        "qa_accuracy",
        "qa_accuracy_by_category",
        "temporal_accuracy",
        "retrieval",
        "bitemporal",
        "decay",
        "latency_ms",
        "cost",
    ):
        assert key in rep, f"missing report section {key}"
    for key in ("recall_at_k", "hit_at_k", "ndcg_at_k"):
        assert key in rep["retrieval"]


def test_providers_conform_to_protocol() -> None:
    # runtime_checkable Protocol: proves adapters are swappable behind the seam.
    assert isinstance(get_provider("lexical"), MemoryProvider)
    assert isinstance(get_provider("recency_decay"), MemoryProvider)


def test_no_evidence_probes_excluded_from_recall() -> None:
    # longmemeval fixture has 3 probes but one abstention probe has no evidence.
    rep = _run("longmemeval", "lexical")
    assert rep["retrieval"]["recall_at_k"]["n"] == 2


def test_consolidation_seam_runs_end_to_end() -> None:
    # The consolidate() hook is part of the eval loop: with consolidate=True the
    # runner calls provider.consolidate() before querying and still produces a
    # full report. (Consolidation QUALITY is SM-R7's composite_quality, reused;
    # this only proves the seam is exercised so an SM-R7 provider slots in.)
    from harness.baselines import get_answerer, get_provider
    from harness.datasets import get_loader
    from harness.judge import StubJudge
    from harness.provider import RunConfig
    from harness.runner import run_benchmark

    bench = get_loader("custom_temporal").fixture()
    cfg = RunConfig(k=5, consolidate=True)
    rep = run_benchmark(bench, get_provider("recency_decay"), StubJudge(), get_answerer(), cfg)
    assert rep["qa_accuracy"] is not None and rep["n_probes"] == len(bench.probes)


def test_time_aware_provider_beats_naive_on_updates() -> None:
    # On the update/temporal-heavy sets the recency-decay provider must not do
    # worse on QA than the naive lexical one (it resolves 'latest value wins').
    for bench in ("custom_temporal", "longmemeval"):
        naive = _run(bench, "lexical")["qa_accuracy"]["value"]
        aware = _run(bench, "recency_decay")["qa_accuracy"]["value"]
        assert aware >= naive, f"{bench}: recency_decay {aware} < lexical {naive}"


def test_recency_respects_as_of_no_leak() -> None:
    rep = _run("custom_temporal", "recency_decay")
    assert rep["bitemporal"]["as_of_leak_rate"] == 0.0


def test_async_matches_sync() -> None:
    bench = get_loader("custom_temporal").fixture()
    s = run_benchmark(bench, get_provider("recency_decay"), StubJudge(), get_answerer(), _CFG)
    a = asyncio.run(arun_benchmark(bench, get_provider("recency_decay"), StubJudge(), get_answerer(), _CFG))
    assert s["qa_accuracy"] == a["qa_accuracy"]
    assert s["retrieval"] == a["retrieval"]


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} runner tests passed")


if __name__ == "__main__":
    _run_all()
