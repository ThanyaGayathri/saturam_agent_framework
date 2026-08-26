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
    """Refuse to pass silently when `egpt` is absent.

    This used to `print` and return when pytest was unavailable, so on a bare runner the file
    exited 0 having asserted nothing -- the suite read as green while proving nothing about the
    wiring it exists to prove (QC review of #152). CI now installs `egpt` for the smoke job, so
    reaching this path means the environment is not what the workflow guarantees, and the right
    answer is a loud failure rather than a quiet one.

    Under pytest the skip is still honoured: a developer running the file without the package
    installed gets a skip, not a spurious failure. It is only the bare-script path -- the one CI
    uses -- that now raises.
    """
    try:
        import pytest
    except ImportError:
        raise RuntimeError(
            f"{reason}. This file is run as a bare script by .github/workflows/memory-eval.yml, "
            f"which installs egpt for exactly this reason -- a silent skip here would report a "
            f"green job that asserted nothing."
        ) from None
    pytest.skip(reason)


def test_build_memory_cases_against_real_suite() -> None:
    if not _HAS_EGPT:
        _skip("egpt not installed — the smoke job installs it; run ./scripts/dev_setup.sh locally")
        return
    from adapters.egpt_suite import build_memory_cases
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    cases, retrieved, query_ms = build_memory_cases(bench, get_provider("recency_decay"), RunConfig(k=3))
    # One case per probe, names preserved, retrieval map + query latency populated, criteria carries gold.
    assert len(cases) == len(bench.probes)
    assert {c.name for c in cases} == {p.probe_id for p in bench.probes}
    assert all(p.probe_id in retrieved for p in bench.probes)
    assert all(p.probe_id in query_ms for p in bench.probes)
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
