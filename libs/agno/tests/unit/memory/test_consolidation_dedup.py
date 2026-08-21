"""``merge_near_duplicate_facts`` -- folding near-duplicate facts after ``_distil`` (SM-L4 #125).

``_distil``'s own Jaccard grouping is pinned in ``test_provider_co_located.py``; what is pinned
here is the stage that runs after it, on the facts it already produced.
"""

from uuid import uuid5

from egpt.memory.consolidation.dedup import FACT_ID_NAMESPACE, merge_near_duplicate_facts
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryRecord, Scope

ACME = Scope(tenant_id="acme", user_id="u1")
GLOBEX = Scope(tenant_id="globex", user_id="u1")


def fact(
    content,
    source_episode_ids,
    scope=ACME,
    fact_id=None,
    t_valid=None,
    t_invalid=None,
    created_at=None,
    topics=None,
    expired_at=None,
    superseded_by=None,
):
    fact_id = fact_id or "|".join(source_episode_ids)
    return FactWrite(
        record=MemoryRecord(
            id=fact_id,
            tier=MemoryTier.SEMANTIC,
            content=content,
            scope=scope,
            provenance={"source_episode_ids": source_episode_ids},
            t_valid=t_valid,
            t_invalid=t_invalid,
            created_at=created_at,
            topics=topics,
            expired_at=expired_at,
            superseded_by=superseded_by,
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


def test_the_first_occurrence_survives_with_its_own_content():
    facts = [fact("the deploy failed", ["a"], fact_id="fact-a"), fact("the deploy failed!", ["b"], fact_id="fact-b")]

    kept, _ = merge_near_duplicate_facts(facts)

    assert len(kept) == 1
    assert kept[0].record.content == "the deploy failed"


def test_the_folded_facts_id_is_rederived_from_the_combined_provenance():
    """`_distil` derives a fact's id from its source episodes so a re-run upserts the same row
    (review finding 3, PR #116). A folded fact's provenance grows beyond either input fact's own
    set, so keeping either original id would break that invariant for the very first re-run.
    """
    facts = [fact("the deploy failed", ["a"], fact_id="fact-a"), fact("the deploy failed!", ["b"], fact_id="fact-b")]

    kept, _ = merge_near_duplicate_facts(facts)

    assert kept[0].record.id == str(uuid5(FACT_ID_NAMESPACE, "a|b"))
    assert kept[0].record.id not in ("fact-a", "fact-b")


def test_rederiving_the_id_is_order_independent_like_distils_own_derivation():
    """Two episodes folding in either scan order must upsert the same row."""
    forward = [fact("the deploy failed", ["a"], fact_id="fact-a"), fact("the deploy failed!", ["b"], fact_id="fact-b")]
    backward = [fact("the deploy failed!", ["b"], fact_id="fact-b"), fact("the deploy failed", ["a"], fact_id="fact-a")]

    kept_forward, _ = merge_near_duplicate_facts(forward)
    kept_backward, _ = merge_near_duplicate_facts(backward)

    assert kept_forward[0].record.id == kept_backward[0].record.id


def test_all_punctuation_content_does_not_raise():
    """`normalize_entity_name` refuses content that normalises to "" -- correct for an *entity*
    name, wrong for a fact's content, where it used to abort the whole consolidation pass on every
    retry (review finding 1, PR #116)."""
    facts = [fact("...", ["a"]), fact("the deploy failed", ["b"])]

    kept, merged = merge_near_duplicate_facts(facts)

    assert merged == 0
    assert len(kept) == 2


def test_two_different_all_punctuation_facts_stay_separate():
    """A shared `""` normalization key would wrongly fold unrelated degenerate facts into one."""
    facts = [fact("...", ["a"]), fact("---", ["b"])]

    kept, merged = merge_near_duplicate_facts(facts)

    assert merged == 0
    assert len(kept) == 2


def test_an_empty_batch_merges_nothing():
    kept, merged = merge_near_duplicate_facts([])

    assert kept == []
    assert merged == 0


def test_folding_widens_the_valid_time_window_to_the_earlier_bound():
    """A fact must stay true from the *earliest* evidence either member carries (review finding 1,
    PR #116) -- keeping only the survivor's `t_valid` silently narrowed the window."""
    facts = [
        fact("the deploy failed", ["a"], t_valid=100, t_invalid=200),
        fact("the deploy failed!", ["b"], t_valid=50, t_invalid=300),
    ]

    kept, _ = merge_near_duplicate_facts(facts)

    assert (kept[0].record.t_valid, kept[0].record.t_invalid) == (50, 300)


def test_folding_stays_open_ended_if_either_member_is():
    """`t_invalid=None` (open-ended) from either member must win, mirroring `_distil`'s own
    "any member unbounded keeps the whole fact open" rule."""
    facts = [
        fact("the deploy failed", ["a"], t_valid=100, t_invalid=None),
        fact("the deploy failed!", ["b"], t_valid=50, t_invalid=300),
    ]

    kept, _ = merge_near_duplicate_facts(facts)

    assert kept[0].record.t_invalid is None
    assert kept[0].record.t_valid == 50


def test_folding_takes_the_later_created_at():
    facts = [
        fact("the deploy failed", ["a"], created_at=100),
        fact("the deploy failed!", ["b"], created_at=300),
    ]

    kept, _ = merge_near_duplicate_facts(facts)

    assert kept[0].record.created_at == 300


def test_folding_unions_topics_without_duplicating_the_shared_one():
    facts = [
        fact("the deploy failed", ["a"], topics=["deploys", "incidents"]),
        fact("the deploy failed!", ["b"], topics=["incidents", "oncall"]),
    ]

    kept, _ = merge_near_duplicate_facts(facts)

    # `set(...)`, not a list-equality on a specific order: the union is scan-order-independent
    # (`sorted(set(...))`), and an order-sensitive assertion here would fail on a future
    # `_distil` sort-order change with no correctness bug in this function (review finding 7,
    # PR #116).
    assert set(kept[0].record.topics) == {"deploys", "incidents", "oncall"}


def test_folding_unions_topics_when_only_the_survivor_has_any():
    """Asymmetric case: the survivor carries topics, the absorbed fact has none (review
    finding 6, PR #116)."""
    facts = [
        fact("the deploy failed", ["a"], topics=["deploys"]),
        fact("the deploy failed!", ["b"], topics=None),
    ]

    kept, _ = merge_near_duplicate_facts(facts)

    assert kept[0].record.topics == ["deploys"]


def test_folding_unions_topics_when_only_the_absorbed_fact_has_any():
    """The reverse asymmetric case: the absorbed fact carries topics the survivor lacks entirely
    -- they must not be lost just because the survivor started with none (review finding 6,
    PR #116)."""
    facts = [
        fact("the deploy failed", ["a"], topics=None),
        fact("the deploy failed!", ["b"], topics=["deploys"]),
    ]

    kept, _ = merge_near_duplicate_facts(facts)

    assert kept[0].record.topics == ["deploys"]


def test_folding_with_no_bounds_or_topics_on_either_side_stays_unset():
    facts = [fact("the deploy failed", ["a"]), fact("the deploy failed!", ["b"])]

    kept, _ = merge_near_duplicate_facts(facts)

    r = kept[0].record
    assert (r.t_valid, r.t_invalid, r.created_at, r.topics) == (None, None, None, None)


def test_folding_resets_expired_at_and_superseded_by_on_the_survivor():
    """No current caller passes an already-expired/superseded fact into a fold -- `_distil`'s
    output is always fresh -- but the fold must not silently inherit a survivor's stale expiry
    if some future caller ever does (review finding 5, PR #116)."""
    facts = [
        fact("the deploy failed", ["a"], expired_at=999, superseded_by="old-fact-id"),
        fact("the deploy failed!", ["b"]),
    ]

    kept, _ = merge_near_duplicate_facts(facts)

    assert (kept[0].record.expired_at, kept[0].record.superseded_by) == (None, None)
