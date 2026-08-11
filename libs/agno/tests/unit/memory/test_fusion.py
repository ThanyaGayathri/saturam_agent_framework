"""Rank fusion (SM-C1, phase 4).

Fusion is where a ranking quietly goes wrong: a leg that scores nothing gets sunk, a flat leg gets
discarded, a duplicate gets counted twice, or a tie reorders between runs. Each of those is a test.
"""

import pytest

from egpt.memory.fusion import fuse_rrf, fuse_weighted, merge_tightest_bounds
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryRecord, Scope, Substrate

SCOPE = Scope(tenant_id="acme")


def rec(id_, score=None, substrate=None, **kwargs):
    return MemoryRecord(
        id=id_,
        tier=MemoryTier.SEMANTIC,
        content=f"content {id_}",
        scope=SCOPE,
        score=score,
        substrate=substrate,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Weighted fusion
# ---------------------------------------------------------------------------


def test_weights_decide_the_order_between_legs():
    vector = [rec("v", score=1.0)]
    graph = [rec("g", score=1.0)]

    heavy_vector = fuse_weighted([vector, graph], weights=[0.9, 0.1])
    heavy_graph = fuse_weighted([vector, graph], weights=[0.1, 0.9])

    assert [r.id for r in heavy_vector] == ["v", "g"]
    assert [r.id for r in heavy_graph] == ["g", "v"]


def test_a_record_in_both_legs_scores_higher_than_either_alone():
    both = fuse_weighted([[rec("shared", score=1.0)], [rec("shared", score=1.0)]], weights=[0.5, 0.5])
    one = fuse_weighted([[rec("shared", score=1.0)], []], weights=[0.5, 0.5])

    assert len(both) == 1  # deduped by id
    assert both[0].score > one[0].score


def test_a_record_found_by_both_legs_reports_no_single_origin():
    fused = fuse_weighted(
        [[rec("s", score=1.0, substrate=Substrate.VECTOR)], [rec("s", score=1.0, substrate=Substrate.GRAPH)]],
        weights=[0.5, 0.5],
    )

    assert fused[0].substrate is None


def test_an_unscored_leg_ranks_by_position_instead_of_sinking():
    """Graph traversal often has an order and no magnitude; scoring it 0.0 would drop the leg."""
    graph = [rec("first"), rec("second"), rec("third")]

    fused = fuse_weighted([[], graph], weights=[0.5, 0.5])

    assert [r.id for r in fused] == ["first", "second", "third"]
    assert fused[0].score > fused[-1].score


def test_a_flat_leg_is_kept_not_zeroed():
    """Every hit equally good is a reason to keep them all, not to discard the leg."""
    flat = [rec("a", score=0.7), rec("b", score=0.7)]

    fused = fuse_weighted([flat, []], weights=[1.0, 0.0])

    assert {r.id for r in fused} == {"a", "b"}
    assert all(r.score == pytest.approx(1.0) for r in fused)


def test_an_empty_leg_does_not_disturb_the_other():
    only = [rec("a", score=0.9), rec("b", score=0.1)]

    assert [r.id for r in fuse_weighted([only, []], weights=[0.5, 0.5])] == ["a", "b"]


def test_mismatched_legs_and_weights_is_a_programming_error():
    with pytest.raises(ValueError, match="must match"):
        fuse_weighted([[rec("a")]], weights=[0.5, 0.5])


def test_fusion_does_not_mutate_the_inputs():
    """Substrates may cache their rows; a fuser that edits scores in place corrupts them."""
    original = rec("a", score=0.4, substrate=Substrate.VECTOR)

    fuse_weighted([[original], []], weights=[0.5, 0.5])

    assert original.score == 0.4
    assert original.substrate is Substrate.VECTOR


# ---------------------------------------------------------------------------
# RRF
# ---------------------------------------------------------------------------


def test_rrf_scores_by_rank_and_ignores_magnitude():
    legs = [[rec("a", score=1000.0), rec("b", score=0.001)]]

    fused = fuse_rrf(legs, k=60)

    assert [r.id for r in fused] == ["a", "b"]
    assert fused[0].score == pytest.approx(1 / 61)
    assert fused[1].score == pytest.approx(1 / 62)


def test_rrf_sums_across_legs():
    fused = fuse_rrf([[rec("s")], [rec("s")]], k=60)

    assert fused[0].score == pytest.approx(2 / 61)


def test_a_smaller_k_sharpens_the_top_of_the_ranking():
    small = fuse_rrf([[rec("a"), rec("b")]], k=1)
    large = fuse_rrf([[rec("a"), rec("b")]], k=1000)

    assert small[0].score - small[1].score > large[0].score - large[1].score


# ---------------------------------------------------------------------------
# Determinism and bi-temporal merge
# ---------------------------------------------------------------------------


def test_ties_break_on_id_so_rankings_are_reproducible():
    legs = [[rec("b", score=0.5), rec("a", score=0.5), rec("c", score=0.5)]]

    assert [r.id for r in fuse_rrf(legs)] == [r.id for r in fuse_rrf(legs)]


def test_the_tightest_bounds_win_when_a_record_arrives_from_both_legs():
    """The graph is authoritative for validity; the vector projection carries no bounds."""
    kept = rec("s", t_valid=100, t_invalid=None)
    incoming = rec("s", t_valid=150, t_invalid=200)

    merge_tightest_bounds(kept, incoming)

    assert kept.t_valid == 150  # later start
    assert kept.t_invalid == 200  # earlier end


def test_an_unbounded_copy_never_widens_a_bounded_one():
    kept = rec("s", t_valid=150, t_invalid=200)

    merge_tightest_bounds(kept, rec("s"))

    assert (kept.t_valid, kept.t_invalid) == (150, 200)


def test_supersession_carries_over_from_whichever_leg_knows_it():
    kept = rec("s")

    merge_tightest_bounds(kept, rec("s", expired_at=300, superseded_by="s2"))

    assert kept.expired_at == 300
    assert kept.superseded_by == "s2"


def test_provenance_merges_without_losing_what_was_already_there():
    kept = rec("s", provenance={"source_episode_ids": ["e1"]})

    merge_tightest_bounds(kept, rec("s", provenance={"edge_id": "x1"}))

    assert kept.provenance == {"source_episode_ids": ["e1"], "edge_id": "x1"}
