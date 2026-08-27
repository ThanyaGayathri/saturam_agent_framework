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
import io
import sys
from contextlib import redirect_stderr
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

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


def _fake_suite_for(bench, correct_ids, order=None) -> _FakeSuiteResult:
    """One CaseResult per probe: passed iff its probe_id is in ``correct_ids``.

    ``order`` emits the results in a different sequence from ``bench.probes``. The default
    (probe order) makes every fixture's result order agree with its probe order, which means a
    positional merge and an id-keyed merge are indistinguishable -- see
    ``test_results_are_joined_by_probe_id_not_by_position``.
    """
    results = []
    for probe in order if order is not None else bench.probes:
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
        _skip("egpt not installed — the smoke job installs it; run ./scripts/dev_setup.sh locally")
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
    # Retrieval/temporal/decay/footprint are things run_cases itself never computes; merging them
    # onto the suite result is what this file owns. Assert the *values*, not their presence: a
    # presence check let three separate mutations through -- dropping `retrieved_ids` from the
    # merge, emptying `turn_index`, and zeroing `query_ms` each left the whole suite green while
    # silently zeroing the benchmark's headline numbers (QC review of #152). For a benchmark
    # harness a plausible-but-wrong number is worse than no number, because it gets compared
    # across runs and acted on.
    retrieval = result.metrics["retrieval"]
    assert retrieval["recall_at_k"] == {"value": 1.0, "n": 2}
    assert retrieval["hit_at_k"] == {"value": 1.0, "n": 2}
    assert retrieval["ndcg_at_k"] == {"value": 1.0, "n": 2}

    # `n=2` against `n_probes=3` is the load-bearing part: the abstention probe carries no
    # evidence, so it is excluded from the retrieval denominator rather than scored as a miss.
    # An implementation that counted it would report 2/3 here and look merely "a bit worse".
    assert retrieval["recall_at_k"]["n"] == result.metrics["n_probes"] - 1

    # Footprint comes from the provider, not the suite -- pinning it catches a merge that
    # forwards an empty or default provider state.
    assert result.metrics["memory_footprint"] == {"items": 7, "tokens": 52}

    # `turn_index` (threaded through the merge into the bi-temporal leak checks at
    # harness/metrics.py:197) IS observable on this fixture: both lme_dog and lme_job carry a
    # real `as_of` timestamp (`knowledge_update` is a bi-temporal category), so an empty
    # turn_index makes every retrieved id a no-op lookup, `leak_measured` stays 0, and this reads
    # None instead of 0.0. Verified as a live mutation, reverted: emptying turn_index at both
    # call sites flips this assertion red while every other assertion above stays green.
    assert result.metrics["bitemporal"]["as_of_leak_rate"] == 0.0

    # query_ms comes from build_memory_cases's own perf_counter() timing, not run_cases; a merge
    # that drops it (e.g. always 0.0) leaves the key present but zeroed, which the assertions
    # above don't touch. Verified as a live mutation, reverted.
    assert result.metrics["latency_ms"]["query"]["mean"] > 0.0


def test_a_degraded_case_is_reported_not_silently_scored_as_wrong() -> None:
    """An errored/timed-out case scores as a wrong answer, so a judge outage looks like a
    regression. The scoring is deliberately unchanged; what is pinned is that the count is
    surfaced, so "the memory system regressed" and "the judge was down" are distinguishable
    (QC review of #152).
    """
    if not _HAS_EGPT:
        _skip("egpt not installed (offline CI)")
        return
    from adapters.egpt_suite import run_memory_suite
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    suite = _fake_suite_for(bench, correct_ids=set())
    suite.results[0].error = "judge endpoint unreachable"

    # `contextlib.redirect_stderr` rather than pytest's `capsys`: this file is also executed as a
    # bare script by the smoke job, whose runner calls every `test_*` with no arguments, so a
    # fixture parameter would raise TypeError there.
    buffer = io.StringIO()
    with patch("egpt.eval.suite.run_cases", return_value=suite), redirect_stderr(buffer):
        run_memory_suite(bench, get_provider("recency_decay"), RunConfig(k=3))

    warning = buffer.getvalue()
    assert "errored, timed out or were skipped" in warning
    assert suite.results[0].name in warning
    assert "floor, not a measurement" in warning


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


def test_arun_memory_suite_uses_the_async_build_path() -> None:
    """A mutation making ``arun_memory_suite`` call the sync ``build_memory_cases`` instead
    of ``abuild_memory_cases`` would not change any metric value here (the fake provider's
    sync and async read/write return identical data), so it must be caught structurally."""
    if not _HAS_EGPT:
        _skip("egpt not installed (offline CI)")
        return
    import adapters.egpt_suite as egpt_suite
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    provider = get_provider("recency_decay")
    correct = {p.probe_id for p in bench.probes}

    async def _fake_arun_cases(cases, **kwargs):
        return _fake_suite_for(bench, correct)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("arun_memory_suite must call abuild_memory_cases, not build_memory_cases")

    with (
        patch("egpt.eval.suite.arun_cases", side_effect=_fake_arun_cases),
        patch.object(egpt_suite, "build_memory_cases", side_effect=_fail_if_called),
    ):
        result = asyncio.run(egpt_suite.arun_memory_suite(bench, provider, RunConfig(k=3)))

    assert result.metrics["n_probes"] == len(bench.probes)


def test_results_are_joined_by_probe_id_not_by_position() -> None:
    """The join must survive ``run_cases`` returning results in any order.

    Every other fixture in this file emits results in ``bench.probes`` order, so a regression from
    the id-keyed join to a positional ``zip(benchmark.probes, suite.results)`` scores the right
    verdicts by accident and the whole file stays green (QC review of #152). Nothing in
    ``egpt.eval.suite`` promises result order, and the async twin especially so.

    Asserted through ``qa_accuracy_by_category`` rather than a record list, because that is the
    published metric where the pairing is observable: it combines ``r.probe.category`` with
    ``r.verdict.correct``, so pairing a probe with its neighbour's verdict moves the 1.0 to the
    wrong category. Overall ``qa_accuracy`` cannot see this -- a permutation preserves the count
    of correct answers, which is exactly why the existing tests miss it.
    """
    if not _HAS_EGPT:
        _skip("egpt not installed — the smoke job installs it; run ./scripts/dev_setup.sh locally")
        return
    from adapters.egpt_suite import run_memory_suite
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()
    provider = get_provider("recency_decay")

    # One probe correct, and the results emitted back-to-front. The fixture's three probes each
    # carry a distinct category, so the correct verdict is attributable to exactly one of them.
    only_correct = bench.probes[0]
    fake = _fake_suite_for(bench, {only_correct.probe_id}, order=list(reversed(bench.probes)))

    assert [r.name for r in fake.results] != [p.probe_id for p in bench.probes], (
        "non-vacuity: the fixture must emit results in a different order from the probes, "
        "or this test cannot tell an id-keyed merge from a positional one"
    )

    with patch("egpt.eval.suite.run_cases", return_value=fake):
        result = run_memory_suite(bench, provider, RunConfig(k=3))

    by_cat = result.metrics["qa_accuracy_by_category"]

    # The load-bearing assertion: the credit lands on the category of the probe that earned it.
    # Under a positional merge the reversed neighbour's verdict is used instead, which moves this
    # 1.0 onto `abstention` and leaves `single_session` at 0.0.
    assert by_cat[only_correct.category]["value"] == 1.0, (
        f"probe {only_correct.probe_id!r} (category {only_correct.category!r}) was judged correct, "
        f"but its category did not score 1.0 -- the merge is joining by position, not by probe_id. "
        f"Got: { {c: v and v['value'] for c, v in by_cat.items()} }"
    )
    for probe in bench.probes[1:]:
        assert by_cat[probe.category]["value"] == 0.0, (
            f"category {probe.category!r} scored above zero, but only {only_correct.probe_id!r} "
            "was judged correct -- another probe's verdict was applied to it"
        )

    # Overall accuracy is deliberately asserted too, to show it is *not* the discriminating
    # signal: a permutation leaves it unchanged, so it would pass under the broken merge as well.
    assert result.metrics["qa_accuracy"]["value"] == 1 / len(bench.probes)


def test_run_config_consolidate_is_honoured_on_both_build_paths() -> None:
    """``RunConfig(consolidate=True)`` must mean the same thing here as on the offline runner.

    ``harness/runner.py`` calls ``provider.consolidate()`` / ``await provider.aconsolidate()``
    between ingest and the first query. This path dropped the flag entirely, so a caller asking
    for consolidation got no error and no consolidation -- a silent behavioural fork between the
    two entry points rather than a style difference (QC review of #152).

    Also pins the *ordering*: consolidation has to happen after the corpus is written and before
    any probe is read, or it consolidates a partial store.
    """
    if not _HAS_EGPT:
        _skip("egpt not installed — the smoke job installs it; run ./scripts/dev_setup.sh locally")
        return
    from adapters.egpt_suite import abuild_memory_cases, build_memory_cases
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    class _RecordingProvider:
        """Wraps a real provider and records the order of write/consolidate/read calls."""

        def __init__(self, inner):
            self._inner = inner
            self.calls: List[str] = []

        def write(self, events):
            self.calls.append("write")
            return self._inner.write(events)

        async def awrite(self, events):
            self.calls.append("awrite")
            return await self._inner.awrite(events)

        def consolidate(self, tiers=None):
            self.calls.append("consolidate")
            return self._inner.consolidate(tiers)

        async def aconsolidate(self, tiers=None):
            self.calls.append("aconsolidate")
            return await self._inner.aconsolidate(tiers)

        def read(self, *args, **kwargs):
            self.calls.append("read")
            return self._inner.read(*args, **kwargs)

        async def aread(self, *args, **kwargs):
            self.calls.append("aread")
            return await self._inner.aread(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    bench = get_loader("longmemeval").fixture()

    # Sync: off means never called; on means called once, after every write, before every read.
    off = _RecordingProvider(get_provider("recency_decay"))
    build_memory_cases(bench, off, RunConfig(k=3))
    assert "consolidate" not in off.calls, "consolidate ran without RunConfig(consolidate=True)"

    on = _RecordingProvider(get_provider("recency_decay"))
    build_memory_cases(bench, on, RunConfig(k=3, consolidate=True))
    assert on.calls.count("consolidate") == 1, f"expected exactly one consolidate, got {on.calls}"
    idx = on.calls.index("consolidate")
    # `idx == len(sessions)` rather than `all(c == "write" for c in calls[:idx])`: the `all()`
    # form is vacuously true when consolidate runs *first* (empty slice), which let a
    # consolidate-before-ingest mutant through while this test stayed green.
    assert idx == len(bench.sessions), (
        f"consolidate must run after all {len(bench.sessions)} sessions are written, but ran at index {idx}: {on.calls}"
    )
    assert "read" not in on.calls[:idx], f"a probe was read before consolidation: {on.calls}"

    # Async twin: same contract through `aconsolidate`.
    aoff = _RecordingProvider(get_provider("recency_decay"))
    asyncio.run(abuild_memory_cases(bench, aoff, RunConfig(k=3)))
    assert "aconsolidate" not in aoff.calls

    aon = _RecordingProvider(get_provider("recency_decay"))
    asyncio.run(abuild_memory_cases(bench, aon, RunConfig(k=3, consolidate=True)))
    assert aon.calls.count("aconsolidate") == 1, f"expected exactly one aconsolidate, got {aon.calls}"
    aidx = aon.calls.index("aconsolidate")
    assert aidx == len(bench.sessions), (
        f"aconsolidate must run after all {len(bench.sessions)} sessions are written, "
        f"but ran at index {aidx}: {aon.calls}"
    )


def test_arun_memory_suite_uses_the_async_footprint() -> None:
    """``arun_memory_suite`` must await ``afootprint()``, not fall back to the sync ``footprint()``.

    Every provider in this repo inherits ``_BaseLexical.afootprint()``, which just delegates to
    ``footprint()`` -- so the two return identical values and a value-based assertion cannot tell
    them apart (QC review of #152). A provider that ever computes its footprint with real async
    I/O would silently run it on the event loop's thread instead.

    Same instrument as ``test_arun_memory_suite_uses_the_async_build_path``: make the sync call
    the thing that fails, so the assertion is about *which method was called*.
    """
    if not _HAS_EGPT:
        _skip("egpt not installed — the smoke job installs it; run ./scripts/dev_setup.sh locally")
        return
    from adapters.egpt_suite import arun_memory_suite
    from harness.baselines import get_provider
    from harness.datasets import get_loader
    from harness.provider import RunConfig

    bench = get_loader("longmemeval").fixture()

    class _AsyncFootprintOnly:
        """Delegates everything, but refuses the *sync* footprint call."""

        def __init__(self, inner):
            self._inner = inner
            self.afootprint_calls = 0

        def footprint(self):
            raise AssertionError("arun_memory_suite must await afootprint(), not call footprint()")

        async def afootprint(self):
            self.afootprint_calls += 1
            return await self._inner.afootprint()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    provider = _AsyncFootprintOnly(get_provider("recency_decay"))
    correct = {p.probe_id for p in bench.probes}

    async def _fake_arun_cases(cases, **kwargs):
        return _fake_suite_for(bench, correct)

    with patch("egpt.eval.suite.arun_cases", side_effect=_fake_arun_cases):
        result = asyncio.run(arun_memory_suite(bench, provider, RunConfig(k=3)))

    assert provider.afootprint_calls == 1, "afootprint() was not awaited exactly once"
    assert result.metrics["memory_footprint"] == {"items": 7, "tokens": 52}


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ran  {fn.__name__}")
    print(f"\n{len(fns)} suite-run tests processed (egpt present: {_HAS_EGPT})")


if __name__ == "__main__":
    _run_all()
