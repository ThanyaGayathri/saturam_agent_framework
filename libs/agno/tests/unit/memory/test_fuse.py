"""Reciprocal Rank Fusion (SM-H2 #132): the pure fusion function, independent of any provider.

``test_provider_co_located.py`` covers the wiring -- ``config.fusion`` selecting this strategy,
the resulting ``fusion["method"]`` diagnostic. This file is only about what ``rrf_fuse`` itself
decides: the RRF formula, determinism, dedup, and (the acceptance criterion the issue names
directly) that the fused ranking beats every single retriever it fused on a labeled fixture.
"""

import pytest

from egpt.memory.retrieval.fuse import DEFAULT_RRF_K, rrf_fuse
from egpt.memory.types import MemoryRecord, MemoryTier, Scope

ACME = Scope(tenant_id="acme")


def record(id_, content=None, tier=MemoryTier.EPISODIC, scope=ACME):
    return MemoryRecord(id=id_, tier=tier, content=content if content is not None else id_, scope=scope)


def leg(*ids):
    return [record(i) for i in ids]


def test_default_k_is_60_per_sm_r8():
    assert DEFAULT_RRF_K == 60


@pytest.mark.parametrize("bad_k", [0, -1, 1.5, "60", True])
def test_a_non_positive_or_non_integer_k_is_rejected(bad_k):
    with pytest.raises(ValueError, match="positive integer"):
        rrf_fuse({"vector": leg("a")}, k=bad_k)


def test_a_record_present_in_every_leg_outranks_one_present_in_only_one():
    vector = leg("a", "b")
    lexical = leg("b", "a")
    graph = leg("a", "b")

    fused = rrf_fuse({"vector": vector, "lexical": lexical, "graph": graph}, k=60)

    assert [r.id for r in fused] == ["a", "b"]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62 + 1 / 61)


def test_the_formula_is_exactly_sum_of_1_over_k_plus_rank():
    fused = rrf_fuse({"vector": leg("a", "b", "c")}, k=10)

    assert [r.score for r in fused] == pytest.approx([1 / 11, 1 / 12, 1 / 13])


def test_a_leg_that_does_not_rank_a_record_contributes_nothing_for_it():
    """Absence is absence, not a rank -- a document only the vector leg found is not penalized by
    a lexical/graph rank it never had."""
    fused = rrf_fuse({"vector": leg("only_in_vector"), "lexical": leg("x", "y")}, k=60)

    only = next(r for r in fused if r.id == "only_in_vector")
    assert only.score == pytest.approx(1 / 61)


def test_a_repeated_id_within_one_leg_counts_once_at_its_best_position():
    """Mirrors `_merge`'s "one leg contributes one score per id" rule."""
    fused = rrf_fuse({"vector": leg("a", "b", "a")}, k=60)

    a = next(r for r in fused if r.id == "a")
    assert a.score == pytest.approx(1 / 61)  # only the rank-1 occurrence counts


def test_fusing_zero_legs_or_all_empty_legs_returns_nothing():
    assert rrf_fuse({}) == []
    assert rrf_fuse({"vector": [], "graph": []}) == []


def test_ties_break_deterministically_on_content_then_tier_then_id():
    """Two records fused to the identical RRF score must still sort the same way every call --
    never on arrival order, which is driven by UUID record ids (mutation: drop the tie-break and
    this becomes flaky across dict-iteration orders)."""
    tied = rrf_fuse({"vector": [record("z", content="alpha")], "graph": [record("a", content="alpha")]}, k=60)

    assert [r.id for r in tied] == ["a", "z"]  # equal score, equal content -> id breaks the tie


def test_fusion_does_not_mutate_the_input_records():
    original = record("a")
    rrf_fuse({"vector": [original]}, k=60)

    assert original.score is None


def test_a_record_fused_from_more_than_one_leg_reports_no_single_origin():
    fused = rrf_fuse({"vector": leg("a"), "graph": leg("a")}, k=60)

    assert fused[0].substrate is None


# ---------------------------------------------------------------------------
# Acceptance criterion: deterministic fused ordering that beats any single retriever
# ---------------------------------------------------------------------------

#: A labeled fixture: 6 candidate documents, 3 of them relevant, ranked differently and
#: imperfectly by three legs -- each leg surfaces exactly one relevant document in its own top 3,
#: so no single retriever looks good alone. RRF's fused ranking must still do better, because the
#: two relevant documents each leg ranks *close to* the top (never first, never last) accumulate
#: rank credit from all three legs, while purely-irrelevant documents that only one leg favours do
#: not.
_RELEVANT = {"d1", "d2", "d3"}
_VECTOR = leg("d1", "d4", "d5", "d2", "d6", "d3")
_LEXICAL = leg("d2", "d5", "d6", "d3", "d1", "d4")
_GRAPH = leg("d3", "d4", "d6", "d1", "d2", "d5")


def _precision_at_3(records):
    return len({r.id for r in records[:3]} & _RELEVANT)


def test_rrf_fusion_beats_every_single_retriever_on_the_labeled_fixture():
    assert _precision_at_3(_VECTOR) == 1
    assert _precision_at_3(_LEXICAL) == 1
    assert _precision_at_3(_GRAPH) == 1

    fused = rrf_fuse({"vector": _VECTOR, "lexical": _LEXICAL, "graph": _GRAPH}, k=DEFAULT_RRF_K)

    assert _precision_at_3(fused) == 2
    assert _precision_at_3(fused) > max(_precision_at_3(_VECTOR), _precision_at_3(_LEXICAL), _precision_at_3(_GRAPH))


def test_the_fixtures_fused_ordering_is_deterministic_across_repeated_calls():
    first = rrf_fuse({"vector": _VECTOR, "lexical": _LEXICAL, "graph": _GRAPH}, k=DEFAULT_RRF_K)
    second = rrf_fuse({"vector": _VECTOR, "lexical": _LEXICAL, "graph": _GRAPH}, k=DEFAULT_RRF_K)

    assert [r.id for r in first] == [r.id for r in second]
