"""The consolidation scheduler (SM-L1 #122): draining `MemoryProvider.consolidate` off the hot
path, idempotent and retryable.

This package is deliberately thin. The consolidation *stages* -- fact extraction (SM-L3 #124,
`extract.py`), dedup (SM-L4 #125), contradiction resolution (SM-L5 #126), reflection (SM-L6
#127), episodic->semantic (SM-L7 #128), summarization (SM-L8 #129) -- extraction is the first of
these to ship; the rest are still `CoLocatedMemoryProvider._distil`'s deterministic-dedup default
(SM-C1 #89). What lives here does not know or care which stages `consolidate` runs: it calls the
method, retries a failure, and calls it again while a pass reports itself incomplete. A future
stage lands inside `consolidate` and this package's contract does not change.

`triggers` (SM-L2 #123) is the policy layer on top: it decides which of `ConsolidationWorker`'s
configured scopes are worth draining on a given tick, rather than draining every one of them
every time.
"""

from egpt.memory.consolidation.extract import (
    STRATEGY_ID,
    ExtractedFacts,
    FactCandidate,
    FactExtractionPipeline,
    FactExtractionReport,
    FactExtractor,
    LLMFactExtractor,
    LLMMergeDecider,
    MergeDecider,
    MergeDecision,
    fact_extraction_details,
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
    "STRATEGY_ID",
    "AnyTrigger",
    "ConsolidationOutcome",
    "ConsolidationTrigger",
    "ConsolidationWorker",
    "EventCountTrigger",
    "ExtractedFacts",
    "FactCandidate",
    "FactExtractionPipeline",
    "FactExtractionReport",
    "FactExtractor",
    "IdleTrigger",
    "LLMFactExtractor",
    "LLMMergeDecider",
    "LatencyTrigger",
    "MergeDecider",
    "MergeDecision",
    "PerTenantThreshold",
    "SizeTrigger",
    "ThresholdSource",
    "fact_extraction_details",
]
