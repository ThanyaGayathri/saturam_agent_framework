"""Near-duplicate fact folding inside one consolidation pass (SM-L4 #125).

``CoLocatedMemoryProvider._distil`` (``adapters/co_located.py``) already groups episodes by
Jaccard token overlap before a single fact is ever built -- SM-R7's ratified extract-and-merge
strategy for that grouping is SM-L3's (#124), not this ticket's, so this module does not touch
it. What ``_distil``'s raw ``text.lower().split()`` tokenizer misses is punctuation: "the deploy
failed!" and "the deploy failed" share no token in common ("failed!" != "failed"), so two groups
that are the *same fact* once normalized survive `_distil` as two. This stage folds those
together, immediately after ``_distil`` and before either substrate write, so the merge reaches
both -- there is no separate vector-only or graph-only dedup path to keep in sync.

**Reuses the seed's normalization, not a second implementation of it.**
:func:`~egpt.memory.types.normalize_entity_name` already *is* ``Curator._normalize``
(``egpt.learn.curate``) restated as an importable function -- SM-G8 (#106) made that call for the
same reason this module repeats it: ``Curator._normalize`` is a private method on a class that
needs a wired ``LearningMachine``, and ``test_entity_normalization_matches_the_curator`` already
pins the two in agreement. A third restatement here would be the class of drift the memory
package's own layering forbids (ADR 0001: no cross-package reach-through for something this
small); importing the one already proven equivalent avoids it.

**Exact-match only, deliberately.** ``_distil``'s Jaccard grouping already covers fuzzy overlap
within one pass; a second, differently-thresholded fuzzy pass here would leave two rules
disagreeing about "duplicate" for the same input. Embedding similarity is the other half the
issue names, and validating it needs a real embedder wired against a real backend -- that is
SM-L3's extraction strategy to own, not a normalization helper's.

.. todo:: SM-L3 (aicoe-tracker#124) owns embedding-similarity dedup -- the semantically
   near-duplicate, textually different case this module's exact-match key cannot catch. Not
   forgotten; deferred here because validating it needs a real embedder wired against a real
   backend, which this module has neither of (review finding 2, PR #116).
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import NAMESPACE_OID, uuid5

from egpt.memory.types import FactWrite, MemoryOperationError, MemoryRecord, Scope, normalize_entity_name

__all__ = ["FACT_ID_NAMESPACE", "merge_near_duplicate_facts"]

#: The namespace ``_distil`` derives a fact's id from (``uuid5(_FACT_NAMESPACE, "|".join(sorted
#: (source_episode_ids)))``). Defined here, not in ``adapters/co_located.py``, because this
#: module re-derives it for a folded fact's now-larger provenance set -- a second constant with
#: the same value would only need to match the first by convention, which is exactly the kind of
#: invariant review finding 3 (PR #116) flagged as silently breakable.
FACT_ID_NAMESPACE = uuid5(NAMESPACE_OID, "egpt.memory.consolidate.fact")


def _dedup_key(fact: FactWrite) -> Tuple[Scope, str]:
    """The grouping key: ``(scope, normalized content)``.

    ``normalize_entity_name`` refuses content that normalizes to the empty string -- correct for
    its own caller (an *entity* whose name is empty has nothing to key on), wrong for this one:
    all-punctuation *fact* content is a real, if degenerate, thing an extractor can hand this
    stage, and refusing it here aborted the entire consolidation pass on every retry (review
    finding 1, PR #116). Falling back to the fact's own id keeps two different all-punctuation
    facts apart -- a shared ``""`` key would wrongly fold them into one -- at the cost of never
    folding two *genuinely* identical all-punctuation facts, which is an acceptable trade: content
    this degenerate is not a meaningful duplicate to begin with.
    """
    try:
        normalized = normalize_entity_name(fact.record.content)
    except MemoryOperationError:
        normalized = fact.record.id
    return fact.record.scope, normalized


def _merged_bounds(
    survivor: MemoryRecord, absorbed: MemoryRecord
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """``(t_valid, t_invalid, created_at)`` for a fold, by the same envelope rule ``_distil``
    uses when it first builds a fact from a *group* of episodes -- applied here pairwise, since a
    fold combines exactly two already-built facts.

    A fact is true for as long as *either* member's evidence says it is (review finding 1,
    PR #116): keeping only the survivor's bounds silently narrowed the window to whichever fact a
    bucket iteration order happened to visit first, the same class of bug ``_distil``'s own
    min/max envelope exists to prevent one level up.

    - ``t_valid``: earliest of the two, ignoring an unset one -- ``None`` only if both are unset.
    - ``t_invalid``: latest of the two, but ``None`` (open-ended) if *either* is -- open-ended
      always wins, or a bounded fact folding with an open-ended one would falsely stop being true.
    - ``created_at``: latest of the two, ignoring an unset one -- "when the system most recently
      had evidence for this," matching ``_distil``'s own choice for the same field.
    """
    valids = [t for t in (survivor.t_valid, absorbed.t_valid) if t is not None]
    t_valid = min(valids) if valids else None
    t_invalid = (
        None
        if survivor.t_invalid is None or absorbed.t_invalid is None
        else max(survivor.t_invalid, absorbed.t_invalid)
    )
    createds = [t for t in (survivor.created_at, absorbed.created_at) if t is not None]
    created_at = max(createds) if createds else None
    return t_valid, t_invalid, created_at


def merge_near_duplicate_facts(facts: Sequence[FactWrite]) -> Tuple[List[FactWrite], int]:
    """Fold facts whose content is identical after normalization into one, per scope.

    **Never across scopes**, even inside one tenant -- mirroring ``_distil``'s own rule
    (its docstring explains why: the content tiebreak that picks a survivor has nothing to do with
    which user a fact is about, so merging across scopes would attribute one user's fact to
    another). Grouping keys on ``(scope, normalized content)`` for exactly that reason.

    Provenance is unioned, never replaced: two facts folding into one must still name every
    episode either one was distilled from, or a later read cannot trace the surviving row back to
    an episode that raised it. ``FactWrite.source_episode_ids`` and
    ``record.provenance["source_episode_ids"]`` are kept in sync -- ``_distil`` writes both, so a
    stage after it must too, or one becomes stale the moment it disagrees with the other.

    **The survivor's id is re-derived from the combined provenance**, the same way `_distil` first
    derived it (``uuid5(FACT_ID_NAMESPACE, "|".join(sorted(source_episode_ids)))``) -- not left as
    whichever fact happened to survive. Content is still taken from the first fact encountered,
    unchanged; the id follows the union, because the id is `_distil`'s "re-running this pass
    upserts the same row" guarantee, and that guarantee is keyed on provenance, not on which fact
    a bucket iteration order happened to visit first (review finding 3, PR #116).

    **Bi-temporal bounds and topics are unioned too, by `_merged_bounds` and set union
    respectively** -- not left as the survivor's alone (review finding 1 and finding 3, PR #116):
    a folded fact must stay true for as long as *either* member's evidence says it is, the same
    invariant `_distil` enforces one level up when it first builds a fact from a group of
    episodes.

    **`subject`/`predicate`/`object`/`confidence` are not merged, and stay the survivor's alone.**
    Unlike bounds and topics, there is no well-defined union for two SPO triples or two confidence
    scores -- `_distil` never populates them today (`FactWrite.is_triple()` needs an extraction
    pass this module deliberately does not have, per its own module docstring), so this is a
    forward note rather than a live gap: whichever ticket makes `_distil`'s output carry SPO
    (SM-L3 #124) needs a merge rule for this fold too, and does not have one yet (review finding
    5, PR #116).

    .. todo:: SM-L3 (aicoe-tracker#124) -- add a merge rule for `subject`/`predicate`/`object`/
       `confidence` once `_distil`'s output carries SPO (review finding 8, PR #116).

    ``facts`` arrives in ``_distil``'s deterministic group order, so which fact contributes
    content is deterministic too -- re-running this stage on the same input never picks a
    different one.

    **No async twin, deliberately (CLAUDE.md's sync/async rule).** That rule targets I/O-bound
    API; this is a pure, in-memory fold over a list `_distil` already materialized, with no
    `await` anywhere in it -- the same reasoning `aconsolidate` applies to `_distil` itself
    (offloaded via `asyncio.to_thread` there only because it is quadratic and CPU-bound at scale;
    this fold runs on `_distil`'s already-small output and needs no such offload, see the call
    site's comment in `co_located.py`).

    Returns the folded facts, in first-occurrence order, and how many were folded away.
    """
    kept: List[FactWrite] = []
    index_by_key: Dict[Tuple[Scope, str], int] = {}
    folded = 0

    for fact in facts:
        key = _dedup_key(fact)
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(kept)
            kept.append(fact)
            continue

        folded += 1
        survivor = kept[existing_index]
        combined_ids = sorted(set(survivor.source_episode_ids) | set(fact.source_episode_ids))
        folded_id = str(uuid5(FACT_ID_NAMESPACE, "|".join(combined_ids)))
        t_valid, t_invalid, created_at = _merged_bounds(survivor.record, fact.record)
        # `sorted(set(...))` rather than an order-preserving scan: insertion order tied to which
        # fact `_distil`'s grouping happened to place first is not a property this fold's callers
        # depend on, and preserving it made the topics list -- and the test asserting it --
        # brittle to a future change in `_distil`'s own sort order (review findings 4/7, PR #116).
        merged_topics = sorted(set(survivor.record.topics or []) | set(fact.record.topics or []))
        kept[existing_index] = replace(
            survivor,
            source_episode_ids=combined_ids,
            record=replace(
                survivor.record,
                id=folded_id,
                topics=merged_topics or None,
                provenance={**survivor.record.provenance, "source_episode_ids": combined_ids},
                t_valid=t_valid,
                t_invalid=t_invalid,
                created_at=created_at,
                # Both callers pass fresh `_distil` output, where these are always `None` --
                # reset explicitly rather than relying on that, so a folded fact can never
                # inherit a survivor's stale expiry/supersession (review finding 5, PR #116).
                expired_at=None,
                superseded_by=None,
            ),
        )

    return kept, folded
