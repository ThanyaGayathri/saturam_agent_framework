"""Bridge to the seed's ``egpt.eval.suite`` — the issue-#88 integration point.

Two bindings, both reusing seed machinery rather than forking it (skill §3/§4
"reuse first"):

1. ``LLMJudge`` — wraps ``egpt.eval.agent_as_judge.AgentAsJudgeEval`` behind the
   harness ``Judge`` protocol, so the full metric suite (recall, temporal, decay,
   cost) can be computed with the REAL graded LLM verdict. One evaluator ``Agent``
   is built once and reused across probes (no agents-in-loops). The model is
   env-driven and uses ``OpenAIResponses`` (house rule: Responses, not Chat; not
   the seed's ``OpenAIChat`` default), pointing at Azure/gpt-5.4/5.5 per ``.env``.

2. ``build_memory_cases`` — turns a ``Benchmark`` + a ``MemoryProvider`` into seed
   ``egpt.eval.suite.Case`` objects driven by ``run_cases``: retrieval happens at
   case-build time (so the answer agent sees the recalled context), ``criteria``
   carries the gold answer for the judge, and the returned id map lets the caller
   merge recall@k / nDCG (which the seed suite does not compute) onto the
   ``SuiteResult``. This is the concrete recipe SM-X7 (#143) lifts onto
   ``eval/suite.py``.

Nothing here is imported unless an LLM/suite run is requested; the offline harness
stays free of egpt.
"""

from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Optional, Tuple

from harness.judge import JudgeResult
from harness.provider import MemoryProvider, RunConfig, events_of
from harness.schemas import Benchmark, Probe

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
    from egpt.models.openai import OpenAIResponses

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


def build_memory_cases(
    benchmark: Benchmark,
    provider: MemoryProvider,
    config: Optional[RunConfig] = None,
    judge_mode: str = "numeric",
) -> Tuple[List["object"], Dict[str, List[str]]]:
    """Build seed ``Case`` objects for ``run_cases`` + the retrieved-id map.

    Ingests the corpus into ``provider`` once, then one Case per probe. Returns
    ``(cases, retrieved_ids_by_probe)`` so the caller can compute recall@k / nDCG
    (not native to the suite) after ``run_cases`` returns the QA verdicts.
    """
    from egpt.eval.suite import Case, JudgeMode

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
    cases: List[object] = []
    retrieved: Dict[str, List[str]] = {}
    mode = JudgeMode.NUMERIC if judge_mode == "numeric" else JudgeMode.BINARY

    for probe in benchmark.probes:
        mems = provider.read(probe.question, k=config.k, as_of=probe.as_of, types=config.types, tenant=config.tenant)
        retrieved[probe.probe_id] = [sid for m in mems for sid in m.source_ids]
        context = "\n".join(f"- {m.text}" for m in mems) or "(no relevant memories found)"
        criteria = (
            f"The answer must correctly answer the question. Reference answer: {probe.answer!r}. "
            "Pass only if the answer is consistent with the reference."
        )
        cases.append(
            Case(
                name=probe.probe_id,
                input=_compose_input(probe, context),
                agent=agent,
                criteria=criteria,
                judge_mode=mode,
                judge_model=judge_model,
                tags=(benchmark.name, probe.category),
            )
        )
    return cases, retrieved
