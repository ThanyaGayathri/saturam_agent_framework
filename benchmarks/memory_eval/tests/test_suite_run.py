"""run_memory_suite / arun_memory_suite — the executable PLAN.md section 3 recipe (SM-X7 #143).

`run_cases`/`arun_cases` themselves are mocked here: driving them for real needs a live LLM
(an answerer agent plus a judge agent), which is exactly what `test_suite_adapter.py`'s
`test_build_memory_cases_against_real_suite` already proves compiles against the real seed API
without spending anything. What this file owns is the part that test cannot verify: that a
`SuiteResult` returned by `run_cases` gets correctly joined back onto the probes it answered and
turned into the retrieval/temporal/decay/footprint metrics the suite itself does not compute.

Skips (like test_suite_adapter.py) when egpt is not importable.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_HAS_EGPT = importlib.util.find_spec("egpt") is not None


def _skip(reason: str) -> None:
    try:
        import pytest

        pytest.skip(reason)
    except ImportError:
        print(f"  SKIP  {reason}")


@dataclass
class _FakeCaseResult:
    """Duck-types the seed's ``CaseResult`` on exactly the fields ``_probe_records`` reads."""

    name: str
    output: str
    judge_score: Optional[int]
    judge_reason: str
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return (self.judge_score or 0) >= 7


@dataclass
class _FakeSuiteResult:
    results: List[_FakeCaseResult]


def _fake_suite_for(bench, correct_ids) -> _FakeSuiteResult:
    """One CaseResult per probe: passed iff its probe_id is in ``correct_ids``."""
    results = []
    for probe in bench.probes:
        passed = probe.probe_id in correct_ids
        results.append(
            _FakeCaseResult(
                name=probe.probe_id,
                output=probe.answer if passed else "I don't know.",
                judge_score=9 if passed else 2,
                judge_reason="fake judge verdict",
                duration_seconds=0.01,
            )
        )
    return _FakeSuiteResult(results=results)


def test_run_memory_suite_merges_qa_and_retrieval_metrics() -> None:
    if not _HAS_EGPT:
        _skip("egpt not installed (offline CI) — suite wiring covered in the venv job")
        return
    from adapters.egpt_suite import run_memory_suite
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    provider = get_provider("recency_decay")
    correct = {p.probe_id for p in bench.probes}  # every probe "answered correctly"

    with patch("egpt.eval.suite.run_cases", return_value=_fake_suite_for(bench, correct)):
        result = run_memory_suite(bench, provider, RunConfig(k=3))

    assert len(result.suite.results) == len(bench.probes), "the mocked run_cases result must flow through unmodified"
    assert result.metrics["n_probes"] == len(bench.probes)
    # Every probe judged "correct" -> qa_accuracy should read 1.0.
    assert result.metrics["qa_accuracy"]["value"] == 1.0
    # Retrieval/temporal/decay/footprint are things run_cases itself never computes;
    # their presence is the actual point of the merge this file owns.
    assert result.metrics["retrieval"]["recall_at_k"] is not None
    assert result.metrics["memory_footprint"] is not None


def test_run_memory_suite_reports_the_suites_own_failures() -> None:
    """A wiring bug that always reported success (e.g. joining on the wrong key, or defaulting
    ``correct`` when a probe id is missing) would pass the test above but not this one."""
    if not _HAS_EGPT:
        _skip("egpt not installed (offline CI)")
        return
    from adapters.egpt_suite import run_memory_suite
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    provider = get_provider("recency_decay")

    with patch("egpt.eval.suite.run_cases", return_value=_fake_suite_for(bench, correct_ids=set())):
        result = run_memory_suite(bench, provider, RunConfig(k=3))

    assert result.metrics["qa_accuracy"]["value"] == 0.0


def test_arun_memory_suite_matches_the_sync_path() -> None:
    if not _HAS_EGPT:
        _skip("egpt not installed (offline CI)")
        return
    from adapters.egpt_suite import arun_memory_suite
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    provider = get_provider("recency_decay")
    correct = {bench.probes[0].probe_id}

    async def _fake_arun_cases(cases, **kwargs):
        return _fake_suite_for(bench, correct)

    with patch("egpt.eval.suite.arun_cases", side_effect=_fake_arun_cases):
        result = asyncio.run(arun_memory_suite(bench, provider, RunConfig(k=3)))

    assert result.metrics["n_probes"] == len(bench.probes)
    assert 0.0 < result.metrics["qa_accuracy"]["value"] < 1.0


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ran  {fn.__name__}")
    print(f"\n{len(fns)} suite-run tests processed (egpt present: {_HAS_EGPT})")


if __name__ == "__main__":
    _run_all()
