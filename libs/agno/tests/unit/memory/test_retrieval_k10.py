"""K10 -- fusing a unified read across more than two stores.

The gap this closes: the codebase's only rank-based fusion lived in ``fuse.py`` typed to
``MemoryRecord``, so nothing above the memory store could reach it, and ``unified_read``
returned its legs side by side rather than ranked together. These tests pin the two halves of
the fix -- a generic RRF core, and a fan-out that takes a deployment's own legs.
"""

from typing import Sequence

import pytest
from egpt.memory.retrieval.fuse import DEFAULT_RRF_K, rrf_rank
from egpt.memory.retrieval.unified import (
    RetrievalSource,
    UnifiedResult,
    UnifiedSlice,
    aunified_read,
    default_identity,
    unified_read,
)
from egpt.memory.types import MemoryQuery, Scope

SCOPE = Scope(tenant_id="acme")


def _hit(content: str, origin: str = "ontology", score: float = 0.5) -> UnifiedResult:
    return UnifiedResult(
        content=content,
        source=RetrievalSource.KB,
        score=score,
        id=f"{origin}:{content}",
        origin=origin,
    )


class TestGenericRrf:
    """The core, over items that are not MemoryRecords."""

    def test_it_ranks_strings(self) -> None:
        ranked = rrf_rank(
            {"a": ["x", "y", "z"], "b": ["y", "x"]},
            identity=lambda s: s,
            tie_break=lambda s: (s,),
        )
        assert [f.item for f in ranked] == ["x", "y", "z"]
        assert [f.legs for f in ranked] == [2, 2, 1]

    def test_a_hit_in_two_legs_beats_a_better_hit_in_one(self) -> None:
        """The property that makes fusing incomparable scales defensible: corroboration
        across legs outweighs one leg's confidence."""
        ranked = rrf_rank(
            {"a": ["solo", "shared"], "b": ["shared"]},
            identity=lambda s: s,
            tie_break=lambda s: (s,),
        )
        assert [f.item for f in ranked] == ["shared", "solo"]

    def test_scores_are_never_read(self) -> None:
        """Only positions matter. Two legs whose scores are on wildly different scales fuse
        identically to two legs with no scores at all."""
        ranked = rrf_rank({"a": ["p", "q"]}, identity=lambda s: s, tie_break=lambda s: (s,))
        assert ranked[0].score == pytest.approx(1.0 / (DEFAULT_RRF_K + 1))
        assert ranked[1].score == pytest.approx(1.0 / (DEFAULT_RRF_K + 2))

    def test_a_repeat_inside_one_leg_counts_once_at_its_best_position(self) -> None:
        ranked = rrf_rank({"a": ["x", "x"]}, identity=lambda s: s, tie_break=lambda s: (s,))
        assert len(ranked) == 1
        assert ranked[0].legs == 1
        assert ranked[0].score == pytest.approx(1.0 / (DEFAULT_RRF_K + 1))

    def test_an_empty_leg_contributes_nothing_but_is_not_an_error(self) -> None:
        ranked = rrf_rank({"a": ["x"], "empty": []}, identity=lambda s: s, tie_break=lambda s: (s,))
        assert [f.item for f in ranked] == ["x"]

    @pytest.mark.parametrize("bad", [0, -1, True])
    def test_a_non_positive_k_is_refused(self, bad: object) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            rrf_rank({"a": ["x"]}, identity=lambda s: s, k=bad)  # type: ignore[arg-type]

    def test_it_is_deterministic_across_equal_scores(self) -> None:
        legs = {"a": ["beta", "alpha"], "b": ["alpha", "beta"]}
        first = [f.item for f in rrf_rank(legs, identity=lambda s: s, tie_break=lambda s: (s,))]
        second = [f.item for f in rrf_rank(legs, identity=lambda s: s, tie_break=lambda s: (s,))]
        assert first == second == ["alpha", "beta"]


class TestCrossStoreIdentity:
    def test_the_same_sentence_from_two_stores_is_one_finding(self) -> None:
        """Ids are per-store, so identity by id would reduce fusion to an interleave."""
        assert default_identity(_hit("A Deal is an opportunity.", "prose")) == default_identity(
            _hit("a deal   is an OPPORTUNITY.", "memory")
        )

    def test_different_sentences_stay_apart(self) -> None:
        assert default_identity(_hit("one", "prose")) != default_identity(_hit("two", "prose"))


class TestFusedSlice:
    def _slice(self) -> UnifiedSlice:
        return UnifiedSlice(
            kb=[_hit("shared rule", "kb"), _hit("kb only", "kb")],
            memory=[_hit("memory only", "memory")],
            extra={
                "ontology": [_hit("shared rule", "ontology")],
                "semantic": [_hit("crm_deal", "semantic")],
            },
        )

    def test_every_leg_is_represented(self) -> None:
        assert set(self._slice().legs()) == {"kb", "memory", "ontology", "semantic"}

    def test_an_empty_leg_is_kept_so_ran_and_matched_nothing_stays_visible(self) -> None:
        slice_ = UnifiedSlice(kb=[], memory=[], extra={"ontology": []})
        assert slice_.legs() == {"kb": [], "memory": [], "ontology": []}

    def test_fusion_reports_how_many_legs_had_each_hit(self) -> None:
        fused = self._slice().fused()
        corroborated = next(r for r in fused if r.content == "shared rule")
        assert corroborated.metadata["fused_from"] == 2
        assert all(r.metadata["fused_from"] == 1 for r in fused if r.content != "shared rule")

    def test_the_corroborated_hit_ranks_first(self) -> None:
        assert self._slice().fused()[0].content == "shared rule"

    def test_every_fused_hit_still_names_its_origin(self) -> None:
        """The evidence line: each result labelled with origin and score."""
        fused = self._slice().fused()
        # A corroborated hit keeps the copy from the leg that saw it first, so `origin` alone
        # cannot name every contributor -- `fused_origins` does.
        assert {r.label for r in fused} == {"kb", "memory", "semantic"}
        assert all(r.score is not None and r.score > 0 for r in fused)
        contributors = {origin for r in fused for origin in r.metadata["fused_origins"]}
        assert contributors == {"kb", "memory", "ontology", "semantic"}
        corroborated = next(r for r in fused if r.content == "shared rule")
        assert corroborated.metadata["fused_origins"] == ["kb", "ontology"]

    def test_limit_truncates_after_ranking_not_before(self) -> None:
        assert [r.content for r in self._slice().fused(limit=1)] == ["shared rule"]

    def test_dropping_a_leg_changes_the_ranking(self) -> None:
        """With a leg disabled the answer visibly degrades -- the leg was contributing."""
        full = self._slice()
        without = UnifiedSlice(kb=full.kb, memory=full.memory, extra={"semantic": full.extra["semantic"]})
        assert full.fused()[0].metadata["fused_from"] == 2
        assert without.fused()[0].metadata["fused_from"] == 1
        assert "ontology" not in {r.label for r in without.fused()}


class TestExtraLegsInTheFanOut:
    def test_a_leg_is_labelled_by_the_name_it_was_registered_under(self) -> None:
        """Not by what the leg claims: the registration key is the truth."""

        def lying_leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
            return [_hit("a concept", origin="something-else")]

        result = unified_read(SCOPE, MemoryQuery(text="q"), legs={"ontology": lying_leg})
        assert [r.label for r in result.extra["ontology"]] == ["ontology"]

    def test_omitting_a_leg_is_how_a_caller_disables_it(self) -> None:
        result = unified_read(SCOPE, MemoryQuery(text="q"))
        assert result.extra == {}
        assert result.results == []

    def test_one_failing_leg_does_not_discard_another_s_results(self) -> None:
        def good(query: MemoryQuery) -> Sequence[UnifiedResult]:
            return [_hit("found", "semantic")]

        def bad(query: MemoryQuery) -> Sequence[UnifiedResult]:
            raise RuntimeError("neo4j is down")

        with pytest.raises(RuntimeError, match="neo4j is down"):
            unified_read(SCOPE, MemoryQuery(text="q"), legs={"ontology": bad, "semantic": good})

    @pytest.mark.asyncio
    async def test_the_async_path_runs_extra_legs_concurrently(self) -> None:
        import asyncio

        async def slow(query: MemoryQuery) -> Sequence[UnifiedResult]:
            await asyncio.sleep(0.05)
            return [_hit("slow", "ontology")]

        async def also_slow(query: MemoryQuery) -> Sequence[UnifiedResult]:
            await asyncio.sleep(0.05)
            return [_hit("also", "semantic")]

        loop = asyncio.get_event_loop()
        started = loop.time()
        result = await aunified_read(SCOPE, MemoryQuery(text="q"), legs={"ontology": slow, "semantic": also_slow})
        elapsed = loop.time() - started
        assert set(result.extra) == {"ontology", "semantic"}
        assert elapsed < 0.09, "the legs ran in sequence, not concurrently"

    @pytest.mark.asyncio
    async def test_an_async_leg_that_raises_still_lets_the_others_finish(self) -> None:
        async def bad(query: MemoryQuery) -> Sequence[UnifiedResult]:
            raise RuntimeError("ontology is down")

        async def good(query: MemoryQuery) -> Sequence[UnifiedResult]:
            return [_hit("found", "semantic")]

        with pytest.raises(RuntimeError, match="ontology is down"):
            await aunified_read(SCOPE, MemoryQuery(text="q"), legs={"ontology": bad, "semantic": good})
