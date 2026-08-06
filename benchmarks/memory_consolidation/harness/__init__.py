"""Episodic->semantic memory consolidation research harness (SM-R7).

This package prototypes and MEASURES candidate consolidation strategies so the
recommendation in ``RESEARCH.md`` is backed by numbers rather than citations.

Nothing here is production code. The chosen strategy is promoted to
``libs/agno/egpt/memory/`` later, in the SM-L7 implementation task. Keep all
candidate strategy implementations inside this harness.
"""

from harness.schemas import (
    ConsolidationResult,
    Episode,
    MergeOp,
    SemanticFact,
    TargetFact,
    UsageStats,
)

__all__ = [
    "ConsolidationResult",
    "Episode",
    "MergeOp",
    "SemanticFact",
    "TargetFact",
    "UsageStats",
]
