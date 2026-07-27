"""Custom bi-temporal + decay probe set.

The public sets do NOT cleanly isolate two behaviors issue #88 requires:

1. **Bi-temporal ("true at T")** -- a fact is updated over time; a query asked
   *as of* an earlier instant must return the value that was true *then*, not the
   latest value. Encoded with ``Probe.as_of`` + ``knowledge_update`` /
   ``as_of_t`` categories, with two probes over the same fact at different times.

2. **Episodic decay** -- a stale, low-salience memory should be de-ranked /
   forgotten so it does not crowd out relevant recent facts. Encoded with
   ``Probe.decay_target_turn_id`` (the turn that SHOULD have decayed).

This set is AUTHORED IN-HOUSE and therefore SINGLE-AUTHORED -- a data-quality
caveat we state honestly (see findings/01). It is a *targeted probe*, not a
general benchmark: it is cross-checked against LongMemEval's temporal /
knowledge-update categories rather than used alone. Its "raw" format IS the
unified schema, so ``build`` == load the committed json.
"""

from __future__ import annotations

from pathlib import Path

from harness.datasets.base import fixture_path, load_fixture
from harness.schemas import Benchmark, load_unified


def download() -> Path:
    """No external source: the set is authored in-house and committed as the fixture."""
    return fixture_path("custom_temporal")


def build(path: Path) -> Benchmark:
    """The custom set's raw format is the unified schema, so build == load."""
    return load_unified(Path(path))


def fixture() -> Benchmark:
    return load_fixture("custom_temporal")
