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

**No production call site yet (round-2 review).** The expected caller is whichever pass already
resolves a scope's full episode-id set to run consolidation over -- e.g.
:class:`~egpt.memory.consolidation.worker.ConsolidationWorker` or
:meth:`~egpt.memory.adapters.co_located.CoLocatedMemoryProvider.consolidate`. Wiring it there
needs the **full** resolved set for the scope, not one ``distil()`` call's episode batch --
union-forward provenance routinely cites episodes from earlier passes outside the current batch,
so checking only the current batch would false-positive on legitimate old citations -- plus a
policy decision on cadence and on-violation behaviour (log / raise / quarantine) this ticket's
acceptance criteria don't specify. Tracked as
`aicoe-tracker#194 <https://github.com/Saturam-Inc/aicoe-tracker/issues/194>`_ rather than decided
here.
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

    def __post_init__(self) -> None:
        """Refuse an incoherent report -- ``dangling`` can never name more facts than ``checked``
        counted (round-2 review). Nothing in :func:`provenance_report` can build such a report, but
        a ``frozen=True`` dataclass's invariant should be structural, not merely implied by its one
        constructor: without this, ``ProvenanceReport(checked=0, dangling=("f1",))`` would silently
        report ``validity == 1.0`` (the vacuous-empty-batch branch) *and* ``is_valid is False`` --
        a contradiction a caller asserting only ``validity == 1.0`` would not catch.
        """
        if len(self.dangling) > self.checked:
            raise ValueError(
                f"ProvenanceReport.dangling ({len(self.dangling)} entries) cannot exceed checked "
                f"({self.checked}) -- more facts flagged than were counted."
            )

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


def dangling_source_episode_ids(fact: FactWrite, resolved_episode_ids: AbstractSet[str]) -> List[str]:
    """Which of ``fact.source_episode_ids`` do **not** resolve against ``resolved_episode_ids``.

    Order-preserving and may contain duplicates if the fact does -- callers wanting the ratified
    metric use :func:`provenance_report`, which counts facts, not citations; this is the finer-
    grained read a caller diagnosing *one* fact wants.
    """
    return [episode_id for episode_id in fact.source_episode_ids if episode_id not in resolved_episode_ids]


def _has_dangling_citation(fact: FactWrite, resolved_episode_ids: AbstractSet[str]) -> bool:
    """Short-circuiting truthiness check behind :func:`provenance_report`'s per-fact filter
    (round-2 review): :func:`dangling_source_episode_ids` builds the full offending list a
    *diagnostic* caller wants, which is one throwaway list allocation per fact when
    :func:`provenance_report` only needs to know whether that list would be non-empty. No
    behaviour change -- same predicate, first miss stops the scan instead of collecting every one.
    """
    return any(episode_id not in resolved_episode_ids for episode_id in fact.source_episode_ids)


def provenance_report(facts: Sequence[FactWrite], resolved_episode_ids: AbstractSet[str]) -> ProvenanceReport:
    """Check every fact in ``facts`` against ``resolved_episode_ids``, the anti-lossiness gate.

    ``resolved_episode_ids`` is the caller's own resolved set -- typically every episode id that
    exists in the scope being consolidated, fetched once and reused across the whole batch, not
    re-fetched per fact. Named for what it must contain, not for a liveness property it does not
    require (round-2 review, replacing the earlier ``live_episode_ids``): a superseded-but-still-
    present episode (row-level ``expired_at``, RESEARCH.md's "archive, never hard-delete") still
    resolves here, because this checks *existence*, not current retention state -- a fact's
    provenance points at history that happened, not at whether that episode is still "live" by
    :mod:`egpt.memory.episodic.forget`'s definition. A caller that resolved only non-expired
    episodes into this set would see every fact citing an archived one reported as dangling, which
    is not what "dangling" means here.
    """
    dangling = tuple(fact.record.id for fact in facts if _has_dangling_citation(fact, resolved_episode_ids))
    return ProvenanceReport(checked=len(facts), dangling=dangling)
