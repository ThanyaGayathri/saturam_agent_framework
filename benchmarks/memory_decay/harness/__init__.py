"""Episodic decay simulation harness (SM-R5).

Self-contained, offline simulation used to *decide* the episodic decay model for
the EGPT memory subsystem. Nothing in this package touches framework core
(``libs/agno/egpt/**``); every candidate decay strategy lives here so the
decision can be measured before any core implementation (SM-C*) begins.

Public entry points:
    - :func:`harness.stream.generate_stream` / ``agenerate_stream``
    - :func:`harness.simulate.run_simulation` / ``arun_simulation``
    - :class:`harness.strategies.DecayStrategy` and the five candidates
"""

from harness.episode import Episode, Query, Stream
from harness.metrics import PruneMetrics, QueryMetrics, RunResult
from harness.simulate import arun_simulation, run_simulation
from harness.strategies import (
    DecayStrategy,
    Ebbinghaus,
    ExpRecency,
    ImportanceWeighted,
    NoDecay,
    TtlHalflife,
    default_strategies,
)
from harness.stream import agenerate_stream, generate_stream

__all__ = [
    "Episode",
    "Query",
    "Stream",
    "generate_stream",
    "agenerate_stream",
    "DecayStrategy",
    "NoDecay",
    "ExpRecency",
    "TtlHalflife",
    "ImportanceWeighted",
    "Ebbinghaus",
    "default_strategies",
    "run_simulation",
    "arun_simulation",
    "QueryMetrics",
    "PruneMetrics",
    "RunResult",
]
