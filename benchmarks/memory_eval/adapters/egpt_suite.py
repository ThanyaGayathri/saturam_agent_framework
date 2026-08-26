"""Bridge to the seed's ``egpt.eval.suite`` — the issue-#88 / SM-X7 (#143) integration point.

Three bindings, all reusing seed machinery rather than forking it (skill §3/§4
"reuse first"):

1. ``LLMJudge`` — wraps ``egpt.eval.agent_as_judge.AgentAsJudgeEval`` behind the
   harness ``Judge`` protocol, so the full metric suite (recall, temporal, decay,
   cost) can be computed with the REAL graded LLM verdict. One evaluator ``Agent``
   is built once and reused across probes (no agents-in-loops). The model is
   env-driven and uses ``OpenAIResponses`` (house rule: Responses, not Chat; not
   the seed's ``OpenAIChat`` default), pointing at Azure/gpt-5.4/5.5 per ``.env``.

2. ``build_memory_cases`` / ``abuild_memory_cases`` — turn a ``Benchmark`` + a
   ``MemoryProvider`` into seed ``egpt.eval.suite.Case`` objects: retrieval happens
   at case-build time (so the answer agent sees the recalled context), ``criteria``
   carries the gold answer for the judge, and the returned id map + per-probe query
   latency let the caller merge recall@k / nDCG / temporal (which the seed suite
   does not compute) onto the ``SuiteResult``.

3. ``run_memory_suite`` / ``arun_memory_suite`` — SM-X7's closing piece: actually
   drives the ``Case``s built above through the seed's ``run_cases``/``arun_cases``
   and merges the QA verdicts they return with ``harness.metrics.aggregate`` (the
   memory-specific metrics the suite does not compute), producing one
   ``MemorySuiteResult``. PLAN.md §3 described this as a recipe; this is that
   recipe, executable.

Nothing here is imported unless an LLM/suite run is requested; the offline harness
stays free of egpt.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from harness.judge import JudgeResult
from harness.metrics import ProbeRecord, aggregate
from harness.provider import MemoryProvider, RunConfig, events_of
from harness.schemas import Benchmark, Probe

if TYPE_CHECKING:
    from egpt.eval.suite import Case, SuiteResult

_DEFAULT_JUDGE_MODEL = os.getenv("HARNESS_JUDGE_MODEL", "gpt-5.5")

_JUDGE_INSTRUCTIONS = (
    "You are a strict grader of a memory system's answer.\n"
    "You are given a QUESTION, a REFERENCE answer (ground truth), and a CANDIDATE answer.\n"
    "Decide whether the CANDIDATE is correct: it must be consistent with the REFERENCE and "
    "actually answer the QUESTION. Ignore style and extra detail; judge only correctness.\n"
    "If the reference says information was not provided, the candidate is correct only if it "
    "also abstains. Provide a score from 1-10 and a brief reason."
)


def _build_model(model_id: Optional[str] = None):
    """Build an OpenAIResponses model, Azure-backed when AZURE_* env is present.

    Mirrors the sibling memory_consolidation harness: keep the Responses API but
    point at Azure when configured. Kept tiny; full config lives in .env.
    """
    from egpt.integrations.models.openai import OpenAIResponses

    model_id = model_id or _DEFAULT_JUDGE_MODEL
    if os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_OPENAI_API_KEY"):
        try:
            from openai import AsyncAzureOpenAI, AzureOpenAI

            model = OpenAIResponses(id=model_id)
            # OpenAIResponses calls client.responses.create(...); AzureOpenAI exposes .responses.
            model.client = AzureOpenAI()  # reads AZURE_OPENAI_* from env
            model.async_client = AsyncAzureOpenAI()
            return model
        except Exception:  # noqa: BLE001 - fall back to plain OpenAIResponses
            return OpenAIResponses(id=model_id)
    return OpenAIResponses(id=model_id)


class LLMJudge:
    """The real graded judge, behind the harness ``Judge`` protocol (sync + async)."""

    def __init__(self, model_id: Optional[str] = None, scoring: str = "numeric", threshold: int = 7) -> None:
        from egpt.agent import Agent
        from egpt.eval.agent_as_judge import AgentAsJudgeEval

        self.model_id = model_id or _DEFAULT_JUDGE_MODEL
        self.name = f"llm:{self.model_id}"
        # One evaluator agent, reused across all probes (no agents-in-loops).
        self._agent = Agent(model=_build_model(self.model_id), instructions=_JUDGE_INSTRUCTIONS)
        self._judge = AgentAsJudgeEval(
            criteria="The candidate answer must match the reference answer for the question.",
            scoring_strategy=scoring,  # "numeric" | "binary"
            threshold=threshold,
            evaluator_agent=self._agent,
            show_spinner=False,
            telemetry=False,
        )

    def _map(self, result) -> JudgeResult:
        ev = result.results[0]
        return JudgeResult(
            correct=bool(ev.passed),
            score=(float(ev.score) if ev.score is not None else None),
            rationale=ev.reason,
            judge_name=self.name,
        )

    async def ajudge(self, question: str, expected: str, answer: str) -> JudgeResult:
        payload = f"QUESTION: {question}\nREFERENCE: {expected}"
        result = await self._judge.arun(input=payload, output=answer)
        return self._map(result)

    def judge(self, question: str, expected: str, answer: str) -> JudgeResult:
        return asyncio.run(self.ajudge(question, expected, answer))


def _answer_agent():
    """A plain answerer agent for suite mode (built once, reused across cases)."""
    from egpt.agent import Agent

    instructions = (
        "Answer the user's question using ONLY the provided memory context. "
        "If the context does not contain the answer, say you do not know. Be concise."
    )
    return Agent(model=_build_model(), instructions=instructions)


def _compose_input(probe: Probe, context: str) -> str:
    return f"Memory context:\n{context}\n\nQuestion: {probe.question}"


def _case_for_probe(probe: Probe, mems, agent, judge_model, mode, benchmark: Benchmark) -> "Case":
    from egpt.eval.suite import Case

    context = "\n".join(f"- {m.text}" for m in mems) or "(no relevant memories found)"
    criteria = (
        f"The answer must correctly answer the question. Reference answer: {probe.answer!r}. "
        "Pass only if the answer is consistent with the reference."
    )
    return Case(
        name=probe.probe_id,
        input=_compose_input(probe, context),
        agent=agent,
        criteria=criteria,
        judge_mode=mode,
        judge_model=judge_model,
        tags=(benchmark.name, probe.category),
    )


def build_memory_cases(
    benchmark: Benchmark,
    provider: MemoryProvider,
    config: Optional[RunConfig] = None,
    judge_mode: str = "numeric",
) -> Tuple[List["Case"], Dict[str, List[str]], Dict[str, float]]:
    """Build seed ``Case`` objects for ``run_cases`` + the retrieved-id map + query latency.

    Ingests the corpus into ``provider`` once, then one Case per probe. Returns
    ``(cases, retrieved_ids_by_probe, query_ms_by_probe)`` so the caller can compute
    recall@k / nDCG / latency (not native to the suite) after ``run_cases`` returns
    the QA verdicts. See ``run_memory_suite`` for the caller that does this.
    """
    from egpt.eval.suite import JudgeMode

    config = config or RunConfig()
    for session in sorted(benchmark.sessions, key=lambda s: s.start_ts):
        provider.write(events_of(session, config.tenant))

    agent = _answer_agent()
    # Pin the JUDGE model on every Case. Without this, run_cases builds the judge as
    # AgentAsJudgeEval(model=case.judge_model or judge_model); if the run_cases caller
    # also passes no judge_model, the seed FALLS BACK to OpenAIChat(id="gpt-5-mini")
    # (agent_as_judge.py get_evaluator_agent) — a house-rule violation (no OpenAIChat).
    # Setting it here makes the Case carry an OpenAIResponses judge regardless of caller.
    judge_model = _build_model()
    cases: List["Case"] = []
    retrieved: Dict[str, List[str]] = {}
    query_ms: Dict[str, float] = {}
    mode = JudgeMode.NUMERIC if judge_mode == "numeric" else JudgeMode.BINARY

    for probe in benchmark.probes:
        t0 = time.perf_counter()
        mems = provider.read(probe.question, k=config.k, as_of=probe.as_of, types=config.types, tenant=config.tenant)
        query_ms[probe.probe_id] = (time.perf_counter() - t0) * 1000.0
        retrieved[probe.probe_id] = [sid for m in mems for sid in m.source_ids]
        cases.append(_case_for_probe(probe, mems, agent, judge_model, mode, benchmark))
    return cases, retrieved, query_ms


async def abuild_memory_cases(
    benchmark: Benchmark,
    provider: MemoryProvider,
    config: Optional[RunConfig] = None,
    judge_mode: str = "numeric",
) -> Tuple[List["Case"], Dict[str, List[str]], Dict[str, float]]:
    """Async twin of :func:`build_memory_cases` (uses the provider's async methods)."""
    from egpt.eval.suite import JudgeMode

    config = config or RunConfig()
    for session in sorted(benchmark.sessions, key=lambda s: s.start_ts):
        await provider.awrite(events_of(session, config.tenant))

    agent = _answer_agent()
    judge_model = _build_model()
    cases: List["Case"] = []
    retrieved: Dict[str, List[str]] = {}
    query_ms: Dict[str, float] = {}
    mode = JudgeMode.NUMERIC if judge_mode == "numeric" else JudgeMode.BINARY

    for probe in benchmark.probes:
        t0 = time.perf_counter()
        mems = await provider.aread(
            probe.question, k=config.k, as_of=probe.as_of, types=config.types, tenant=config.tenant
        )
        query_ms[probe.probe_id] = (time.perf_counter() - t0) * 1000.0
        retrieved[probe.probe_id] = [sid for m in mems for sid in m.source_ids]
        cases.append(_case_for_probe(probe, mems, agent, judge_model, mode, benchmark))
    return cases, retrieved, query_ms


@dataclass
class MemorySuiteResult:
    """``run_cases``'s QA verdicts plus the memory-specific metrics it doesn't compute.

    ``suite`` is the seed's own ``SuiteResult`` (its ``to_dict()`` stays the CI contract,
    unmodified). ``metrics`` is ``harness.metrics.aggregate``'s report — recall@k, nDCG,
    temporal/bi-temporal accuracy, decay, and the labelless memory footprint — computed by
    joining ``suite.results`` (keyed by ``CaseResult.name`` == ``Probe.probe_id``) with the
    retrieved-id map and query latency ``build_memory_cases`` returns.
    """

    suite: "SuiteResult"
    metrics: dict


def _judge_name(judge_model) -> str:
    return f"llm:{getattr(judge_model, 'id', judge_model)}"


def _probe_records(
    benchmark: Benchmark,
    suite: "SuiteResult",
    retrieved: Dict[str, List[str]],
    query_ms: Dict[str, float],
    judge_name: str,
) -> List[ProbeRecord]:
    """Join a suite run back onto the probes it answered.

    A ``CaseResult`` with no matching probe (a ``tag=``/``name=`` filtered run_cases call
    selected cases outside this benchmark) is skipped rather than raising: ``aggregate`` scores
    what actually ran, the same way the offline ``run_benchmark`` never sees skipped cases.
    """
    probes_by_id = {probe.probe_id: probe for probe in benchmark.probes}
    records: List[ProbeRecord] = []
    degraded: List[str] = []
    for result in suite.results:
        probe = probes_by_id.get(result.name)
        if probe is None:
            continue
        # An errored, timed-out or skipped case has `passed=False`, so it scores as a *wrong
        # answer* rather than a missing one -- a judge outage then publishes as a legitimate 0%
        # and trips the gate's `min_qa_accuracy` as a false regression, with retrieval still
        # reporting 1.0 beside it to make the wrong story convincing (QC review of #152).
        #
        # The scoring itself is left alone: whether to exclude these from the denominator or fail
        # the run outright is a benchmark-semantics decision, not a review fix. What changes is
        # that the count is now reported, so "the memory system regressed" and "the judge was
        # down" are distinguishable in the output instead of looking identical.
        if getattr(result, "error", None) or getattr(result, "timed_out", False) or getattr(result, "skipped", False):
            degraded.append(result.name)
        records.append(
            ProbeRecord(
                probe=probe,
                retrieved_ids=retrieved.get(result.name, []),
                answer=result.output or "",
                verdict=JudgeResult(
                    correct=result.passed,
                    score=(float(result.judge_score) if result.judge_score is not None else None),
                    rationale=result.judge_reason or "",
                    judge_name=judge_name,
                ),
                query_ms=query_ms.get(result.name, 0.0),
                answer_ms=result.duration_seconds * 1000.0,
            )
        )
    if degraded:
        # stderr, not egpt's logger: this module is importable on the stdlib-only offline
        # path, where egpt may not be installed at all.
        print(
            f"WARNING: {len(degraded)} of {len(suite.results)} eval cases errored, timed out or "
            f"were skipped and are scored as incorrect: {sorted(degraded)}. Treat the "
            f"qa_accuracy below as a floor, not a measurement.",
            file=sys.stderr,
        )
    return records


def run_memory_suite(
    benchmark: Benchmark,
    provider: MemoryProvider,
    config: Optional[RunConfig] = None,
    judge_mode: str = "numeric",
) -> MemorySuiteResult:
    """Drive ``benchmark`` through ``provider`` via the seed's ``run_cases`` (real LLM answer +
    real LLM judge), merging recall/temporal/decay/footprint onto the QA verdicts it returns.

    This is the executable form of the PLAN.md §3 recipe: ``build_memory_cases`` for the
    ``Case``s, the seed's own ``run_cases`` to actually run them, ``harness.metrics.aggregate``
    for what the suite does not compute.
    """
    from egpt.eval.suite import run_cases

    config = config or RunConfig()
    cases, retrieved, query_ms = build_memory_cases(benchmark, provider, config=config, judge_mode=judge_mode)
    suite = run_cases(cases)
    judge_name = _judge_name(cases[0].judge_model) if cases else _judge_name(_DEFAULT_JUDGE_MODEL)
    turn_index = {turn.turn_id: turn for session in benchmark.sessions for turn in session.turns}
    records = _probe_records(benchmark, suite, retrieved, query_ms, judge_name)
    metrics = aggregate(
        records, k=config.k, turn_index=turn_index, judge_name=judge_name, footprint=provider.footprint()
    )
    return MemorySuiteResult(suite=suite, metrics=metrics)


async def arun_memory_suite(
    benchmark: Benchmark,
    provider: MemoryProvider,
    config: Optional[RunConfig] = None,
    judge_mode: str = "numeric",
) -> MemorySuiteResult:
    """Async twin of :func:`run_memory_suite` (uses ``arun_cases`` + the provider's async methods)."""
    from egpt.eval.suite import arun_cases

    config = config or RunConfig()
    cases, retrieved, query_ms = await abuild_memory_cases(benchmark, provider, config=config, judge_mode=judge_mode)
    suite = await arun_cases(cases)
    judge_name = _judge_name(cases[0].judge_model) if cases else _judge_name(_DEFAULT_JUDGE_MODEL)
    turn_index = {turn.turn_id: turn for session in benchmark.sessions for turn in session.turns}
    records = _probe_records(benchmark, suite, retrieved, query_ms, judge_name)
    metrics = aggregate(
        records, k=config.k, turn_index=turn_index, judge_name=judge_name, footprint=await provider.afootprint()
    )
    return MemorySuiteResult(suite=suite, metrics=metrics)
