"""``merge_near_duplicate_facts`` -- folding near-duplicate facts after ``_distil`` (SM-L4 #125).

``_distil``'s own Jaccard grouping is pinned in ``test_provider_co_located.py``; what is pinned
here is the stage that runs after it, on the facts it already produced.
"""

from egpt.memory.consolidation.dedup import merge_near_duplicate_facts
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryRecord, Scope

ACME = Scope(tenant_id="acme", user_id="u1")
GLOBEX = Scope(tenant_id="globex", user_id="u1")


def fact(content, source_episode_ids, scope=ACME, fact_id=None):
    fact_id = fact_id or "|".join(source_episode_ids)
    return FactWrite(
        record=MemoryRecord(
            id=fact_id,
            tier=MemoryTier.SEMANTIC,
            content=content,
            scope=scope,
            provenance={"source_episode_ids": source_episode_ids},
        ),
        source_episode_ids=source_episode_ids,
        derived_by="co_located.deterministic_dedup",
    )


def test_facts_differing_only_by_punctuation_merge():
    """The gap `_distil`'s raw tokenizer leaves: "failed!" and "failed" share no token."""
    facts = [fact("the deploy failed!", ["a"]), fact("the deploy failed", ["b"])]

    kept, merged = merge_near_duplicate_facts(facts)

    assert merged == 1
    assert len(kept) == 1
    assert sorted(kept[0].source_episode_ids) == ["a", "b"]


def test_the_merged_facts_provenance_is_unioned_not_replaced():
    facts = [fact("Deploy Failed", ["a"]), fact("deploy failed", ["b"]), fact("  deploy   failed  ", ["c"])]

    kept, merged = merge_near_duplicate_facts(facts)

    assert merged == 2
    assert sorted(kept[0].source_episode_ids) == ["a", "b", "c"]
    assert sorted(kept[0].record.provenance["source_episode_ids"]) == ["a", "b", "c"]


def test_distinct_content_never_merges():
    facts = [fact("the deploy failed", ["a"]), fact("customer asked about invoicing", ["b"])]

    kept, merged = merge_near_duplicate_facts(facts)

    assert merged == 0
    assert len(kept) == 2


def test_identical_content_across_scopes_never_merges():
    """Mirrors `_distil`'s own rule: content alone must never decide across a scope boundary, or a
    fact derived from one user's episodes gets attributed to another."""
    facts = [fact("the deploy failed", ["a"], scope=ACME), fact("the deploy failed", ["b"], scope=GLOBEX)]

    kept, merged = merge_near_duplicate_facts(facts)

    assert merged == 0
    assert len(kept) == 2
    assert {f.record.scope for f in kept} == {ACME, GLOBEX}


def test_the_first_occurrence_survives_with_its_own_content_and_id():
    facts = [fact("the deploy failed", ["a"], fact_id="fact-a"), fact("the deploy failed!", ["b"], fact_id="fact-b")]

    kept, _ = merge_near_duplicate_facts(facts)

    assert len(kept) == 1
    assert kept[0].record.id == "fact-a"
    assert kept[0].record.content == "the deploy failed"


def test_an_empty_batch_merges_nothing():
    kept, merged = merge_near_duplicate_facts([])

    assert kept == []
    assert merged == 0
