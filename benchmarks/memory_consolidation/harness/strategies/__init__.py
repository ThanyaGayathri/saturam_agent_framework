"""Candidate consolidation strategies (harness-only).

Each strategy implements ``consolidate`` and ``aconsolidate`` over a batch of
new episodes given the facts learned so far. Only the chosen strategy is
promoted to core in SM-L7.
"""

from harness.strategies.base import ConsolidationStrategy
from harness.strategies.baseline import NoConsolidationStrategy
from harness.strategies.hierarchical import HierarchicalSummaryStrategy
from harness.strategies.hybrid import HybridStrategy
from harness.strategies.mem0 import Mem0MergeStrategy
from harness.strategies.pipeline import PipelineStrategy
from harness.strategies.reflection import ReflectionStrategy

STRATEGIES = {
    "baseline": NoConsolidationStrategy,
    "mem0": Mem0MergeStrategy,
    "reflection": ReflectionStrategy,
    "hierarchical": HierarchicalSummaryStrategy,
    "hybrid": HybridStrategy,
    # The shipped `FactExtractionPipeline` itself, not a prototype (aicoe-tracker#194 item 2):
    # the four labelled-corpus metrics ADR 0003 section A.7 ratifies are only meaningful when
    # measured against the code that actually runs.
    "pipeline": PipelineStrategy,
}

__all__ = [
    "ConsolidationStrategy",
    "NoConsolidationStrategy",
    "Mem0MergeStrategy",
    "ReflectionStrategy",
    "HierarchicalSummaryStrategy",
    "HybridStrategy",
    "PipelineStrategy",
    "STRATEGIES",
]
