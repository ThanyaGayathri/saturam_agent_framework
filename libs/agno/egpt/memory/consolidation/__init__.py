"""The consolidation scheduler (SM-L1 #122): draining `MemoryProvider.consolidate` off the hot
path, idempotent and retryable.

This package is deliberately thin. Most of the consolidation *stages* -- fact extraction
(SM-L3 #124), contradiction resolution (SM-L5 #126), reflection (SM-L6 #127),
episodic->semantic (SM-L7 #128), summarization (SM-L8 #129) -- are none of them shipped yet;
`CoLocatedMemoryProvider.consolidate` runs the one pass SM-C1 shipped in their place, now
followed by `dedup`'s near-duplicate fold (SM-L4 #125). What lives here does not know or care
which stages `consolidate` runs: it calls the method, retries a failure, and calls it again while
a pass reports itself incomplete. A future stage lands inside `consolidate` and this package's
contract does not change.

`triggers` (SM-L2 #123) is the policy layer on top: it decides which of `ConsolidationWorker`'s
configured scopes are worth draining on a given tick, rather than draining every one of them
every time.
"""

from egpt.memory.consolidation.dedup import FACT_ID_NAMESPACE, merge_near_duplicate_facts
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
    "AnyTrigger",
    "ConsolidationOutcome",
    "ConsolidationTrigger",
    "ConsolidationWorker",
    "EventCountTrigger",
    "IdleTrigger",
    "LatencyTrigger",
    "PerTenantThreshold",
    "SizeTrigger",
    "ThresholdSource",
    "merge_near_duplicate_facts",
]
