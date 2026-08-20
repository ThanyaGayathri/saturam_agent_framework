"""The consolidation scheduler (SM-L1 #122): draining `MemoryProvider.consolidate` off the hot
path, idempotent and retryable.

This package is deliberately thin. Most consolidation *stages* -- fact extraction (SM-L3 #124),
dedup (SM-L4 #125), contradiction resolution (SM-L5 #126), episodic->semantic (SM-L7 #128),
summarization (SM-L8 #129) -- are not shipped yet; `CoLocatedMemoryProvider.consolidate` runs the
one dedup pass SM-C1 shipped, plus reflection (SM-L6 #127, `reflect.py`), in their place. What
lives here does not know or care which stages `consolidate` runs: it calls the method, retries a
failure, and calls it again while a pass reports itself incomplete. A future stage lands inside
`consolidate` and this package's contract does not change.

`triggers` (SM-L2 #123) is the policy layer on top: it decides which of `ConsolidationWorker`'s
configured scopes are worth draining on a given tick, rather than draining every one of them
every time. `reflect.ReflectionPipeline` reuses `SizeTrigger` from that same policy layer for its
own, narrower question -- once a scope *is* being drained, whether its episodes' accumulated
importance is worth synthesizing into an insight.
"""

from egpt.memory.consolidation.reflect import (
    DEFAULT_IMPORTANCE_THRESHOLD,
    DEFAULT_MAX_EPISODES_PER_REFLECTION,
    InsightSynthesizer,
    LLMInsightSynthesizer,
    ReflectionPipeline,
    ReflectionReport,
    SynthesizedInsight,
)
from egpt.memory.consolidation.triggers import (
    AnyTrigger,
    ConsolidationTrigger,
    EventCountTrigger,
    IdleTrigger,
    LatencyTrigger,
    PerTenantThreshold,
    SizeTrigger,
    ThresholdSource,
)
from egpt.memory.consolidation.worker import ConsolidationOutcome, ConsolidationWorker

__all__ = [
    "AnyTrigger",
    "ConsolidationOutcome",
    "ConsolidationTrigger",
    "ConsolidationWorker",
    "DEFAULT_IMPORTANCE_THRESHOLD",
    "DEFAULT_MAX_EPISODES_PER_REFLECTION",
    "EventCountTrigger",
    "IdleTrigger",
    "InsightSynthesizer",
    "LLMInsightSynthesizer",
    "LatencyTrigger",
    "PerTenantThreshold",
    "ReflectionPipeline",
    "ReflectionReport",
    "SizeTrigger",
    "SynthesizedInsight",
    "ThresholdSource",
]
