"""Run a strategy over a dataset and score it.

The runner owns the running fact store: it feeds each session batch to the
strategy in order, carrying facts forward, and accumulates one ``UsageStats``
across the whole stream. Strategy objects are created once per run (never inside
the session loop) so there is no agents-in-loops anti-pattern.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from harness.datasets.base import Dataset
from harness.llm import LLMClient
from harness.metrics import (
    ajudge_targets,
    aqa_accuracy,
    composite_quality,
    judge_targets,
    label_metrics,
    qa_accuracy,
)
from harness.schemas import SemanticFact, UsageStats
from harness.strategies import STRATEGIES


def _instantiate(strategy_name: str, llm: LLMClient):
    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{strategy_name}'; choices: {sorted(STRATEGIES)}")
    return STRATEGIES[strategy_name](llm)


def run_strategy(
    strategy_name: str,
    dataset: Dataset,
    llm: LLMClient,
    with_qa: bool = False,
    qa_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Run one strategy over one dataset (sync)."""
    strategy = _instantiate(strategy_name, llm)
    usage = UsageStats()
    facts: List[SemanticFact] = []
    for session in dataset.sessions:
        result = strategy.consolidate(session, facts, usage)
        facts = result.facts
    return _score(strategy, dataset, facts, usage, llm, with_qa, qa_limit, is_async=False)


async def arun_strategy(
    strategy_name: str,
    dataset: Dataset,
    llm: LLMClient,
    with_qa: bool = False,
    qa_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Run one strategy over one dataset (async)."""
    strategy = _instantiate(strategy_name, llm)
    usage = UsageStats()
    facts: List[SemanticFact] = []
    for session in dataset.sessions:
        result = await strategy.aconsolidate(session, facts, usage)
        facts = result.facts
    return await _ascore(strategy, dataset, facts, usage, llm, with_qa, qa_limit)


def _base_report(strategy, dataset: Dataset, facts, usage) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "strategy": strategy.name,
        "dataset": dataset.name,
        "n_episodes": dataset.n_episodes,
        "n_sessions": len(dataset.sessions),
        "usage": usage.to_dict(),
    }
    # Travels with the measurement rather than staying in source comments only (round-2 review,
    # PR #178) -- e.g. PipelineStrategy's lower-bound-not-an-estimate caveat on
    # dedup_score/conflict_accuracy.
    if strategy.metric_caveat:
        report["metric_caveat"] = strategy.metric_caveat
    return report


def _score(strategy, dataset, facts, usage, llm, with_qa, qa_limit, is_async) -> Dict[str, Any]:
    report = _base_report(strategy, dataset, facts, usage)
    if dataset.targets:
        asserted = judge_targets(dataset.targets, facts, llm, usage)
        m = label_metrics(facts, dataset.targets, dataset.episode_ids, asserted=asserted)
        report["label_metrics"] = m
        report["composite_quality"] = composite_quality(m)
    if with_qa and dataset.qa_pairs:
        report["qa"] = qa_accuracy(facts, dataset.qa_pairs, llm, usage, limit=qa_limit)
    report["usage"] = usage.to_dict()  # refresh after judge/QA calls
    return report


async def _ascore(strategy, dataset, facts, usage, llm, with_qa, qa_limit) -> Dict[str, Any]:
    report = _base_report(strategy, dataset, facts, usage)
    if dataset.targets:
        asserted = await ajudge_targets(dataset.targets, facts, llm, usage)
        m = label_metrics(facts, dataset.targets, dataset.episode_ids, asserted=asserted)
        report["label_metrics"] = m
        report["composite_quality"] = composite_quality(m)
    if with_qa and dataset.qa_pairs:
        report["qa"] = await aqa_accuracy(facts, dataset.qa_pairs, llm, usage, limit=qa_limit)
    report["usage"] = usage.to_dict()
    return report
