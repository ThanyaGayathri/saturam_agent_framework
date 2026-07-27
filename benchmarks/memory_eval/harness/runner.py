"""The eval loop: drive a MemoryProvider over a Benchmark and score it.

This is the concrete integration point the plan promises: a uniform
ingest -> query -> answer -> judge -> aggregate loop that ANY provider
(baseline today; SM-R7 consolidation / SM-R8 retrieval tomorrow) runs through
unchanged. Phase 4 wraps this same loop as ``egpt.eval.suite`` ``Case``s; the
scoring is identical either way.

Sessions are ingested in timestamp order (memory is temporal). Each probe is
queried with its ``as_of`` so bi-temporal providers can answer "as of T". The
provider's retrieved ``source_ids`` are flattened in rank order to score
retrieval; the answerer + judge produce QA correctness. Strategy/provider objects
are created ONCE by the caller (no agents-in-loops).

Both sync (``run_benchmark``) and async (``arun_benchmark``) variants are provided.
"""

from __future__ import annotations

import time
from typing import List, Optional

from harness.judge import Judge
from harness.metrics import ProbeRecord, aggregate
from harness.provider import Answerer, MemoryProvider, RunConfig, events_of
from harness.schemas import Benchmark
from harness.usage import UsageStats


def _ordered_sessions(benchmark: Benchmark):
    return sorted(benchmark.sessions, key=lambda s: s.start_ts)


def _retrieved_ids(memories) -> List[str]:
    """Flatten retrieved memories' provenance into a single ranked id list."""
    ids: List[str] = []
    for m in memories:
        ids.extend(m.source_ids)
    return ids


def run_benchmark(
    benchmark: Benchmark,
    provider: MemoryProvider,
    judge: Judge,
    answerer: Answerer,
    config: Optional[RunConfig] = None,
    usage: Optional[UsageStats] = None,
) -> dict:
    """Run one provider over one benchmark (sync) and return the metric report."""
    config = config or RunConfig()
    usage = usage or UsageStats()

    ingest_ms: List[float] = []
    for session in _ordered_sessions(benchmark):
        events = events_of(session)
        t0 = time.perf_counter()
        provider.write(events)
        ingest_ms.append((time.perf_counter() - t0) * 1000.0)
    if config.consolidate:
        provider.consolidate()

    records: List[ProbeRecord] = []
    for probe in benchmark.probes:
        t0 = time.perf_counter()
        mems = provider.read(probe.question, k=config.k, as_of=probe.as_of, types=config.types)
        query_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        answer = answerer.answer(probe.question, mems)
        answer_ms = (time.perf_counter() - t1) * 1000.0

        verdict = judge.judge(probe.question, probe.answer, answer)
        records.append(
            ProbeRecord(
                probe=probe,
                retrieved_ids=_retrieved_ids(mems),
                answer=answer,
                verdict=verdict,
                query_ms=query_ms,
                answer_ms=answer_ms,
            )
        )

    report = aggregate(
        records,
        k=config.k,
        turn_index=benchmark.turn_index,
        ingest_ms=ingest_ms,
        usage=usage,
        judge_name=judge.name,
    )
    report["benchmark"] = benchmark.name
    report["provider"] = getattr(provider, "name", provider.__class__.__name__)
    report["answerer"] = getattr(answerer, "name", answerer.__class__.__name__)
    return report


async def arun_benchmark(
    benchmark: Benchmark,
    provider: MemoryProvider,
    judge: Judge,
    answerer: Answerer,
    config: Optional[RunConfig] = None,
    usage: Optional[UsageStats] = None,
) -> dict:
    """Async twin of ``run_benchmark`` (uses the providers'/judge's async methods)."""
    config = config or RunConfig()
    usage = usage or UsageStats()

    ingest_ms: List[float] = []
    for session in _ordered_sessions(benchmark):
        events = events_of(session)
        t0 = time.perf_counter()
        await provider.awrite(events)
        ingest_ms.append((time.perf_counter() - t0) * 1000.0)
    if config.consolidate:
        await provider.aconsolidate()

    records: List[ProbeRecord] = []
    for probe in benchmark.probes:
        t0 = time.perf_counter()
        mems = await provider.aread(probe.question, k=config.k, as_of=probe.as_of, types=config.types)
        query_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        answer = await answerer.aanswer(probe.question, mems)
        answer_ms = (time.perf_counter() - t1) * 1000.0

        verdict = await judge.ajudge(probe.question, probe.answer, answer)
        records.append(
            ProbeRecord(
                probe=probe,
                retrieved_ids=_retrieved_ids(mems),
                answer=answer,
                verdict=verdict,
                query_ms=query_ms,
                answer_ms=answer_ms,
            )
        )

    report = aggregate(
        records,
        k=config.k,
        turn_index=benchmark.turn_index,
        ingest_ms=ingest_ms,
        usage=usage,
        judge_name=judge.name,
    )
    report["benchmark"] = benchmark.name
    report["provider"] = getattr(provider, "name", provider.__class__.__name__)
    report["answerer"] = getattr(answerer, "name", answerer.__class__.__name__)
    return report
