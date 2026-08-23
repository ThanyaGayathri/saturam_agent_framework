"""Provenance validity: the anti-lossiness check episodic->semantic consolidation needs (SM-L7 #128).

SM-L3 (#124, ``extract.py``) already builds and unions-forward ``FactWrite.source_episode_ids``,
and SM-S1 (#118, ``semantic/facts.py``) already persists it on both substrates. What is missing is
the check RESEARCH.md section 8 names as anti-lossiness safeguard 4 -- "every cited episode id must
resolve to a real episode" -- and the CI gate ADR 0003 section A.7 ratifies on it:
``provenance_validity == 1.0``. Without this module, a fact whose ``source_episode_ids`` cites a
row that was never written, or was hard-deleted out of band, looks identical to a well-sourced one:
the list is non-empty either way. This is a **referential**, not a labelled-recall, check -- it
needs no ground truth, only the live episode ids a caller already has to fetch to run consolidation
in the first place, so it is exactly as available in production as in the eval harness.

**Pure, no store access, no async twin -- the same exemption
:mod:`egpt.memory.episodic.forget` documents for itself.** This module is handed the live episode
ids a caller already resolved (via ``EpisodicSubstrate.recall`` / ``.read_tier``); reaching into a
store here would duplicate a fetch the caller is already doing and force two round trips instead of
one. CLAUDE.md's sync/async pairing rule targets I/O-bound public API -- there is none here.

**Structural validity only.** ``provenance_precision`` / ``provenance_recall`` /
``conflict_accuracy`` / ``dedup_score`` (RESEARCH.md section 9, ``benchmarks/memory_consolidation/
harness/metrics.py``) score a run against *gold* target facts and are eval-only: they need a
labelled dataset no production caller has. ``provenance_validity`` needs no label at all -- a
dangling citation is wrong regardless of what the fact should have said -- which is what makes it
the one metric from that ratified gate this module can enforce at runtime, not only in CI.
"""

from dataclasses import dataclass
from typing import AbstractSet, List, Sequence, Tuple

from egpt.memory.types import FactWrite

__all__ = [
    "ProvenanceReport",
    "dangling_source_episode_ids",
    "provenance_report",
]


@dataclass(frozen=True)
class ProvenanceReport:
    """The outcome of checking one batch of facts' ``source_episode_ids`` against live episodes.

    ``validity`` is RESEARCH.md section 9's ``provenance_validity`` metric: the fraction of facts
    whose *every* cited episode id resolves. A fact with no citations at all counts as valid rather
    than dangling -- an empty ``source_episode_ids`` is :func:`~egpt.memory.consolidation.extract.
    FactExtractionPipeline`'s own "ungrounded, dropped" outcome (``extract.py`` lines 606-608), not
    a dangling reference; conflating the two would make this report double-count what extraction
    already filtered.

    ``dangling`` names which facts failed, by ``record.id`` -- never by content or the offending
    episode id in a log line, matching :func:`~egpt.memory.validation.assert_scope_isolation`'s own
    reasoning: "which fact, how many missing citations" is what an operator acts on.

    ``Tuple[str, ...]``, not ``List[str]`` -- ``frozen=True`` blocks reassigning the attribute, but
    a mutable field value is still reachable and mutable in place (``report.dangling.append(...)``),
    which would silently invalidate ``is_valid`` for any other holder of the same report without
    raising anything. A tuple closes that gap; a list only looked like it did (round-1 review).
    """

    checked: int
    dangling: Tuple[str, ...] = ()

    @property
    def validity(self) -> float:
        """``1.0`` on an empty batch -- vacuously true, matching every other ratio metric in
        ``harness/metrics.py`` (an empty run is not a failing one, it is a no-op)."""
        if self.checked == 0:
            return 1.0
        return (self.checked - len(self.dangling)) / self.checked

    @property
    def is_valid(self) -> bool:
        """The ratified CI gate is ``== 1.0``, not a threshold -- spelled as a property so a
        caller enforcing the gate does not have to re-derive "no dangling citations" from a float
        comparison that floating-point division could, in principle, make imprecise."""
        return not self.dangling


def dangling_source_episode_ids(fact: FactWrite, live_episode_ids: AbstractSet[str]) -> List[str]:
    """Which of ``fact.source_episode_ids`` do **not** resolve against ``live_episode_ids``.

    Order-preserving and may contain duplicates if the fact does -- callers wanting the ratified
    metric use :func:`provenance_report`, which counts facts, not citations; this is the finer-
    grained read a caller diagnosing *one* fact wants.
    """
    return [episode_id for episode_id in fact.source_episode_ids if episode_id not in live_episode_ids]


def provenance_report(facts: Sequence[FactWrite], live_episode_ids: AbstractSet[str]) -> ProvenanceReport:
    """Check every fact in ``facts`` against ``live_episode_ids``, the anti-lossiness gate.

    ``live_episode_ids`` is the caller's own resolved set -- typically every episode id in the scope
    being consolidated, fetched once and reused across the whole batch, not re-fetched per fact.
    A superseded-but-still-present episode (row-level ``expired_at``, RESEARCH.md's "archive, never
    hard-delete") still resolves here: this checks *existence*, not liveness, because a fact's
    provenance points at history that happened, not at the episode's current retention state --
    the same distinction :mod:`egpt.memory.episodic.forget` draws between archiving and deleting.
    """
    # No `fact.record.id is not None` guard here (round-1 review): `MemoryRecord.id` is typed
    # `str`, and `__post_init__` raises on any falsy value, so no successfully constructed record
    # can reach this loop with `id=None` -- the guard was dead and implied a skip path that cannot
    # exist.
    dangling = tuple(fact.record.id for fact in facts if dangling_source_episode_ids(fact, live_episode_ids))
    return ProvenanceReport(checked=len(facts), dangling=dangling)
