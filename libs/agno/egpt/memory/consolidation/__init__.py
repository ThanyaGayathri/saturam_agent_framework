"""The consolidation scheduler (SM-L1 #122): draining `MemoryProvider.consolidate` off the hot
path, idempotent and retryable.

This package is deliberately thin. The consolidation *stages* -- fact extraction (SM-L3 #124),
dedup (SM-L4 #125), contradiction resolution (SM-L5 #126), reflection (SM-L6 #127),
episodic->semantic (SM-L7 #128), summarization (SM-L8 #129) -- are none of them shipped yet;
`CoLocatedMemoryProvider.consolidate` runs the one pass SM-C1 shipped in their place. What lives
here does not know or care which stages `consolidate` runs: it calls the method, retries a
failure, and calls it again while a pass reports itself incomplete. A future stage lands inside
`consolidate` and this package's contract does not change.
"""

from egpt.memory.consolidation.worker import ConsolidationOutcome, ConsolidationWorker

__all__ = ["ConsolidationOutcome", "ConsolidationWorker"]
