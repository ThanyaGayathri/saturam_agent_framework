"""The consolidation scheduler (SM-L1 #122): draining `MemoryProvider.consolidate` off the hot
path, idempotent and retryable.

This package is deliberately thin. The consolidation *stages* -- fact extraction (SM-L3 #124,
`extract.py`), near-duplicate fact folding (SM-L4 #125, `dedup.py`), contradiction resolution
(SM-L5 #126), episodic->semantic (SM-L7 #128), summarization (SM-L8 #129) -- extraction and
folding are the first two to ship, plus reflection (SM-L6 #127, `reflect.py`); the rest are still
`CoLocatedMemoryProvider._distil_facts`'s deterministic-dedup default (SM-C1 #89), now followed by
`dedup`'s near-duplicate fold, when no `fact_pipeline` (SM-L3) is wired -- a wired pipeline already
decides ADD/UPDATE/DELETE/NOOP against existing facts itself and skips the fold (see
`co_located.CoLocatedMemoryProvider._distil_facts`'s docstring for why). What lives here does not
know or care which stages `consolidate` runs: it calls the method, retries a failure, and calls it
again while a pass reports itself incomplete. A future stage lands inside `consolidate` and this
package's contract does not change.

`triggers` (SM-L2 #123) is the policy layer on top: it decides which of `ConsolidationWorker`'s
configured scopes are worth draining on a given tick, rather than draining every one of them
every time. `reflect.ReflectionPipeline` reuses `SizeTrigger` from that same policy layer for its
own, narrower question -- once a scope *is* being drained, whether its episodes' accumulated
importance is worth synthesizing into an insight. `sleeptime` (SM-L9 #130) adds a wall-clock
window on top of `triggers`, so a caller can confine heavy consolidation to an idle/off-peak
period instead of firing at any hour a scope happens to go quiet.
"""

from egpt.memory.consolidation.dedup import FACT_ID_NAMESPACE, merge_near_duplicate_facts
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
from egpt.memory.consolidation.reflect import (
    DEFAULT_IMPORTANCE_THRESHOLD,
    DEFAULT_MAX_EPISODES_PER_REFLECTION,
    InsightSynthesizer,
    LLMInsightSynthesizer,
    ReflectionPipeline,
    ReflectionReport,
    SynthesizedInsight,
)
from egpt.memory.consolidation.sleeptime import (
    SleepTimeScheduleConfig,
    SleepTimeTrigger,
    SleepWindow,
    build_sleep_time_trigger,
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

#: `FACT_ID_NAMESPACE` is deliberately not re-exported here: it is `dedup.py`'s own internal
#: implementation detail (the id-derivation namespace), imported by `co_located.py` directly
#: (`from egpt.memory.consolidation import FACT_ID_NAMESPACE`, which works regardless of `__all__`
#: -- that list only governs `import *`) so the two modules cannot drift on its value, not because
#: it is meant to be this package's stable public API (review finding 1, PR #116).
__all__ = [
    "STRATEGY_ID",
    "AnyTrigger",
    "ConsolidationOutcome",
    "ConsolidationTrigger",
    "ConsolidationWorker",
    "DEFAULT_IMPORTANCE_THRESHOLD",
    "DEFAULT_MAX_EPISODES_PER_REFLECTION",
    "EventCountTrigger",
    "ExtractedFacts",
    "FactCandidate",
    "FactExtractionPipeline",
    "FactExtractionReport",
    "FactExtractor",
    "IdleTrigger",
    "InsightSynthesizer",
    "LLMFactExtractor",
    "LLMInsightSynthesizer",
    "LLMMergeDecider",
    "LatencyTrigger",
    "MergeDecider",
    "MergeDecision",
    "PerTenantThreshold",
    "ReflectionPipeline",
    "ReflectionReport",
    "SizeTrigger",
    "SleepTimeScheduleConfig",
    "SleepTimeTrigger",
    "SleepWindow",
    "SynthesizedInsight",
    "ThresholdSource",
    "build_sleep_time_trigger",
    "fact_extraction_details",
    "merge_near_duplicate_facts",
]
