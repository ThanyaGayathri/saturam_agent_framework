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
"""

from dataclasses import replace
from typing import Dict, List, Sequence, Tuple

from egpt.memory.types import FactWrite, Scope, normalize_entity_name

__all__ = ["merge_near_duplicate_facts"]


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

    The first fact encountered for a key survives unchanged (content, bounds, id); later ones in
    the same group only contribute their provenance. ``facts`` arrives in ``_distil``'s
    deterministic group order, so which one survives is deterministic too -- re-running this stage
    on the same input never picks a different survivor.

    Returns the folded facts, in first-occurrence order, and how many were folded away.
    """
    kept: List[FactWrite] = []
    index_by_key: Dict[Tuple[Scope, str], int] = {}
    merged = 0

    for fact in facts:
        key = (fact.record.scope, normalize_entity_name(fact.record.content))
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(kept)
            kept.append(fact)
            continue

        merged += 1
        survivor = kept[existing_index]
        combined_ids = sorted(set(survivor.source_episode_ids) | set(fact.source_episode_ids))
        kept[existing_index] = replace(
            survivor,
            source_episode_ids=combined_ids,
            record=replace(
                survivor.record,
                provenance={**survivor.record.provenance, "source_episode_ids": combined_ids},
            ),
        )

    return kept, merged
