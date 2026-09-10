"""K10 -- the retail retrieval legs, without a graph.

Round 1 on the P3 PR was right that this file had 297 lines and no unit coverage: the
ranking formula, the dedup and the "None means no leg" contract were backed only by a
cookbook run nobody's CI performs. These are those three, plus the batching that replaced
the per-table round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, cast

import pytest
from egpt.memory.types import MemoryQuery
from egpt_retail.contracts.semantic import (
    Concept,
    SemanticRead,
    Snapshot,
    Table,
    TableRef,
)
from egpt_retail.contracts.semantic import Column as SemanticColumn
from egpt_retail.retrieval.legs import (
    ONTOLOGY_LEG,
    SEMANTIC_LEG,
    ColumnIndexed,
    ontology_leg,
    retail_legs,
    semantic_leg,
)


@dataclass
class _Reader:
    """A semantic reader over a fixed schema. Counts its own calls, so a test can prove a
    round trip did not happen rather than only that the answer was right.

    Cast at the call sites rather than fleshed out to the whole ``SemanticRead`` protocol:
    these legs read four things, and a fake implementing the other dozen methods would hide
    which four.
    """

    tables: Dict[str, List[str]]
    concepts: Dict[str, str]
    indexed: bool = True
    get_table_calls: int = 0
    index_calls: int = 0

    def list_tables(self, *, include_proposed: bool = False) -> Tuple[TableRef, ...]:
        return tuple(
            TableRef(name=q.split(".")[-1], schema=q.split(".")[0], row_count=0)
            for q in sorted(self.tables)
        )

    def get_table(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Table]:
        self.get_table_calls += 1
        columns = self.tables.get(name)
        if columns is None:
            return None
        return Table(
            ref=TableRef(
                name=name.split(".")[-1], schema=name.split(".")[0], row_count=0
            ),
            columns=tuple(SemanticColumn(name=c, data_type="text") for c in columns),
        )

    def column_index(self) -> Dict[str, List[str]]:
        self.index_calls += 1
        return {table: list(columns) for table, columns in self.tables.items()}

    def list_concepts(self, *, include_proposed: bool = False) -> Tuple[str, ...]:
        return tuple(sorted(self.concepts))

    def get_concept(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Concept]:
        if name not in self.concepts:
            return None
        return Concept(name=name, description=self.concepts[name])

    def snapshot(self) -> Snapshot:
        return Snapshot(version="v1", harvested_at="", source="test")


class _UnindexedReader(_Reader):
    """A reader without the batching optimisation -- the P0 stub's shape."""

    column_index = None  # type: ignore[assignment]


def _reader(**kw: Any) -> _Reader:
    return _Reader(
        tables={
            "silver.crm_deal": ["deal_id", "amount", "stage"],
            "silver.crm_contact": ["contact_id", "email"],
            "silver.analytics_event": ["session_id", "event_name"],
        },
        concepts={"QuoteRequest": "A request for a price."},
        **kw,
    )


def _query(text: str, limit: int = 5) -> MemoryQuery:
    return MemoryQuery(text=text, limit=limit)


class TestSemanticLegRanking:
    def test_a_shorter_table_name_wins_on_a_one_word_query(self) -> None:
        """`crm_deal` is the more specific answer to "deal" than `crm_deal_stage_history`;
        ranking by raw hit count would invert that."""
        reader = _Reader(
            tables={"silver.crm_deal": ["id"], "silver.crm_deal_stage_history": ["id"]},
            concepts={},
        )
        results = semantic_leg(cast(SemanticRead, reader))(_query("deal"))
        assert [r.metadata["table"] for r in results] == [
            "crm_deal",
            "crm_deal_stage_history",
        ]

    def test_a_column_match_alone_finds_the_table(self) -> None:
        """ "Which table has email" is a question about a column, and the first version of
        this leg could not answer it at all."""
        results = semantic_leg(cast(SemanticRead, _reader()))(_query("email"))
        assert [r.metadata["table"] for r in results] == ["crm_contact"]
        assert results[0].metadata["columns"] == ["email"]
        assert "via column(s) email" in results[0].content

    def test_a_column_match_is_weaker_than_a_name_match(self) -> None:
        reader = _Reader(
            tables={"silver.stage": ["id"], "silver.crm_deal": ["stage"]}, concepts={}
        )
        results = semantic_leg(cast(SemanticRead, reader))(_query("stage"))
        assert [r.metadata["table"] for r in results] == ["stage", "crm_deal"]

    def test_a_table_matching_nothing_is_absent(self) -> None:
        assert semantic_leg(cast(SemanticRead, _reader()))(_query("zzz_nothing")) == []

    def test_results_are_capped_at_the_limit(self) -> None:
        # "crm" matches two tables by name; the cap must apply after ranking, not before.
        assert len(semantic_leg(cast(SemanticRead, _reader()))(_query("crm"))) == 2
        assert (
            len(semantic_leg(cast(SemanticRead, _reader()))(_query("crm", limit=1)))
            == 1
        )

    def test_every_result_names_the_leg(self) -> None:
        for result in semantic_leg(cast(SemanticRead, _reader()))(_query("email")):
            assert result.origin == SEMANTIC_LEG

    def test_a_concept_is_rendered_as_promotion_renders_it(self) -> None:
        """Two legs reading the same node must render it identically, or fusion by content
        can never recognise them as one finding."""
        from egpt_retail.semantic.promotion import promotable_text

        results = semantic_leg(cast(SemanticRead, _reader()))(_query("quoterequest"))
        concept = next(r for r in results if "concept" in r.metadata)
        assert concept.content == promotable_text(
            Concept(name="QuoteRequest", description="A request for a price.")
        )

    def test_short_query_words_are_ignored(self) -> None:
        """Two-letter fragments match half a schema; the floor keeps the leg from returning
        every table for a query containing "of"."""
        assert semantic_leg(cast(SemanticRead, _reader()))(_query("of an")) == []


class TestSemanticLegBatching:
    def test_an_indexed_reader_is_asked_once_not_once_per_table(self) -> None:
        reader = _reader()
        semantic_leg(cast(SemanticRead, reader))(_query("email"))
        assert reader.index_calls == 1
        assert reader.get_table_calls == 0

    def test_a_reader_without_the_index_still_works(self) -> None:
        """The P0 stub has no column_index; the documented fallback must answer the same."""
        plain = _UnindexedReader(tables={"silver.crm_contact": ["email"]}, concepts={})
        assert not isinstance(plain, ColumnIndexed)
        results = semantic_leg(cast(SemanticRead, plain))(_query("email"))
        assert [r.metadata["table"] for r in results] == ["crm_contact"]
        assert plain.get_table_calls == 1


class TestOntologyLegDedup:
    class _Hit:
        def __init__(self, name: str, score: float, leg: str) -> None:
            self.concept = Concept(name=name, description=f"{name} description")
            self.score = score
            self.leg = leg

    class _Store:
        def __init__(self, vector: List[Any], lexical: List[Any]) -> None:
            self._vector, self._lexical = vector, lexical

        def search_vector(self, query: str, limit: int = 5) -> Tuple[Any, ...]:
            return tuple(self._vector[:limit])

        def search_lexical(self, query: str, limit: int = 5) -> Tuple[Any, ...]:
            return tuple(self._lexical[:limit])

    def test_a_concept_found_by_both_store_legs_appears_once(self) -> None:
        """Passing them as two legs would let one store's internal agreement count as
        corroboration between stores in the outer fusion."""
        both = self._Hit("Quote", 0.9, "vector")
        store = self._Store([both], [self._Hit("Quote", 0.5, "lexical")])
        results = ontology_leg(store)(_query("quote"))
        assert [r.metadata["concept"] for r in results] == ["Quote"]

    def test_it_keeps_the_best_ranked_appearance(self) -> None:
        store = self._Store(
            [self._Hit("Quote", 0.9, "vector")], [self._Hit("Quote", 0.5, "lexical")]
        )
        [result] = ontology_leg(store)(_query("quote"))
        assert result.score == 0.9
        assert result.metadata["leg"] == "vector"

    def test_every_result_names_the_leg(self) -> None:
        store = self._Store([self._Hit("Quote", 0.9, "vector")], [])
        assert all(r.origin == ONTOLOGY_LEG for r in ontology_leg(store)(_query("q")))


class TestNoneMeansNoLeg:
    def test_an_unwired_store_produces_no_leg_at_all(self) -> None:
        """Not an empty one: "asked and found nothing" and "never asked" must stay
        distinguishable, which is what the disable-a-leg scenario turns on."""
        assert retail_legs(ontology=None, semantic=None) == {}

    def test_each_store_contributes_its_own_leg(self) -> None:
        legs = retail_legs(ontology=object(), semantic=cast(SemanticRead, _reader()))
        assert sorted(legs) == [ONTOLOGY_LEG, SEMANTIC_LEG]

    def test_only_the_wired_one_appears(self) -> None:
        assert list(retail_legs(semantic=cast(SemanticRead, _reader()))) == [
            SEMANTIC_LEG
        ]


class TestExecutorDeterminism:
    """The registered workflow step, not just the pure helper it calls."""

    def test_detect_business_areas_is_registered_under_its_own_name(self) -> None:
        from egpt_retail.executors.areas import detect_business_areas
        from egpt_retail.executors.registry import check_registry

        assert detect_business_areas.__name__ == "detect_business_areas"
        assert check_registry([detect_business_areas]) == ()

    def test_the_executor_consults_no_model(self) -> None:
        import inspect

        from egpt_retail.executors import areas

        source = inspect.getsource(areas)
        for forbidden in ("Agent(", "openai", "AzureOpenAI", "instructions="):
            assert forbidden not in source, forbidden

    @pytest.mark.asyncio
    async def test_two_runs_over_one_graph_state_agree(self, monkeypatch: Any) -> None:
        """The executor wires a live driver, so the determinism proof needs the wiring
        stubbed rather than the helper called directly."""
        from egpt.workflow.types import StepInput
        from egpt_retail.executors import areas as executor_module

        calls: List[int] = []

        class _Store:
            def __init__(self, **_: Any) -> None: ...

            def mapped_concepts(self) -> Dict[str, List[str]]:
                calls.append(1)
                return {"Contact": ["silver.crm_contact"]}

        class _Driver:
            def close(self) -> None: ...

        class _GraphDatabase:
            @staticmethod
            def driver(*_: Any, **__: Any) -> Any:
                return _Driver()

        import neo4j

        monkeypatch.setattr(neo4j, "GraphDatabase", _GraphDatabase)
        monkeypatch.setattr(executor_module, "SemanticGraphStore", _Store)

        first = await executor_module.detect_business_areas(StepInput())
        second = await executor_module.detect_business_areas(StepInput())
        assert first.content == second.content
        assert len(calls) == 2
        assert first.content["supported_areas"] == ["customer"]
