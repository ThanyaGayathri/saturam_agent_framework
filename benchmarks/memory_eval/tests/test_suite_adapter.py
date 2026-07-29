"""egpt.eval.suite adapter test — SKIPS when egpt is not importable.

The offline harness must not depend on core, so this test is skipped in the
stdlib-only CI smoke. With the dev venv (egpt installed) it builds real
``Case`` objects and an ``LLMJudge`` WITHOUT making any LLM call, verifying the
integration wiring compiles against the real seed API.

Runs under pytest (uses skip) or standalone (prints SKIP/OK).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_HAS_EGPT = importlib.util.find_spec("egpt") is not None


def _skip(reason: str) -> None:
    try:
        import pytest

        pytest.skip(reason)
    except ImportError:
        print(f"  SKIP  {reason}")


def test_build_memory_cases_against_real_suite() -> None:
    if not _HAS_EGPT:
        _skip("egpt not installed (offline CI) — suite adapter covered in the venv job")
        return
    from adapters.egpt_suite import build_memory_cases
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    cases, retrieved = build_memory_cases(bench, get_provider("recency_decay"), RunConfig(k=3))
    # One case per probe, names preserved, retrieval map populated, criteria carries gold.
    assert len(cases) == len(bench.probes)
    assert {c.name for c in cases} == {p.probe_id for p in bench.probes}
    assert all(p.probe_id in retrieved for p in bench.probes)
    for c, p in zip(cases, bench.probes):
        assert p.answer in c.criteria  # gold answer flows into the judge criteria
        # judge_model pinned to OpenAIResponses (NOT the seed's OpenAIChat fallback).
        assert c.judge_model is not None
        assert type(c.judge_model).__name__ == "OpenAIResponses"
        assert "Question:" in c.input


def test_llm_judge_constructs_without_calling() -> None:
    if not _HAS_EGPT:
        _skip("egpt not installed (offline CI)")
        return
    from adapters.egpt_suite import LLMJudge

    j = LLMJudge()  # builds the evaluator agent; makes no API call
    assert j.name.startswith("llm:")
    # Conforms to the harness Judge protocol (sync + async methods present).
    assert hasattr(j, "judge") and hasattr(j, "ajudge")


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ran  {fn.__name__}")
    print(f"\n{len(fns)} suite-adapter tests processed (egpt present: {_HAS_EGPT})")


if __name__ == "__main__":
    _run_all()
