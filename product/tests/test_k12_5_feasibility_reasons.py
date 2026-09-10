"""K12.5 -- why a KPI is not ready, not just that it is not.

A verdict tells a customer they cannot have the number. A reason tells them what to do, and
the two cases here are days apart: a missing table needs a connector configured and data
landed; an unjoinable pair needs one join proposal reviewed.

The second case was not detected at all before this. `assess_feasibility` subtracts tables,
so a formula over two present-but-unjoinable tables reported `ready` and then returned a cross
product at run time -- wrong numbers, no error, nothing upstream to catch it.
"""

from __future__ import annotations

from typing import List, Tuple

from egpt_retail.contracts.kpi import (
    FeasibilityReason,
    FeasibilityVerdict,
    KpiDefinition,
    assess_feasibility,
    assess_feasibility_with_joins,
)

BOTH = frozenset({"analytics_event", "session_replay_session"})


def _kpi(*tables: str, identifier: str = "web__sessions") -> KpiDefinition:
    joined = " JOIN ".join(tables)
    return KpiDefinition(
        identifier=identifier,
        name="n",
        description="d",
        sql=f"SELECT count(*) FROM {joined}"
        if len(tables) == 1
        else f"SELECT count(*) FROM {tables[0]} JOIN {tables[1]} ON true",
        grain="overall",
        business_area="marketing",
        source_tables=frozenset(tables),
    )


def _ruled_out(left: str, right: str) -> bool:
    """Review recorded that this pair must not be joined."""
    return True


def _no_ruling(left: str, right: str) -> bool:
    """Nobody has ruled on this pair -- which is not a prohibition."""
    return False


class TestTheGapThisCloses:
    def test_the_set_difference_alone_calls_it_ready(self) -> None:
        """Kept as a test so the reason for the new function stays visible: this is the
        behaviour that produced a cross product."""
        assert assess_feasibility(_kpi(*BOTH), BOTH).verdict is FeasibilityVerdict.READY

    def test_with_joins_it_is_blocked_and_says_why(self) -> None:
        result = assess_feasibility_with_joins(_kpi(*BOTH), BOTH, _ruled_out)
        assert result.verdict is FeasibilityVerdict.BLOCKED
        assert result.reason is FeasibilityReason.NOT_JOINABLE
        assert result.missing_tables == frozenset()
        assert len(result.unjoinable) == 1

    def test_the_rationale_names_the_pair_and_the_next_step(self) -> None:
        rationale = assess_feasibility_with_joins(
            _kpi(*BOTH), BOTH, _ruled_out
        ).rationale
        for table in BOTH:
            assert table in rationale
        assert "must not be joined" in rationale
        assert "different id spaces" in rationale
        assert "return a number rather than fail" in rationale

    def test_a_pair_nobody_ruled_on_stays_ready(self) -> None:
        """Absence of a join proposal is not a prohibition. Blocking here would make most
        multi-table KPIs unusable to enforce a rule nobody stated."""
        result = assess_feasibility_with_joins(_kpi(*BOTH), BOTH, _no_ruling)
        assert result.verdict is FeasibilityVerdict.READY
        assert result.reason is FeasibilityReason.NONE


class TestReasonsAreDistinguishable:
    def test_a_missing_table_reports_missing_tables(self) -> None:
        result = assess_feasibility(_kpi("absent"), frozenset({"crm_deal"}))
        assert result.reason is FeasibilityReason.MISSING_TABLES
        assert "Missing from the semantic snapshot" in result.rationale

    def test_ready_reports_none(self) -> None:
        result = assess_feasibility(_kpi("crm_deal"), frozenset({"crm_deal"}))
        assert result.reason is FeasibilityReason.NONE

    def test_a_missing_table_is_reported_ahead_of_an_unjoinable_pair(self) -> None:
        """Both can be true. "Connect this source" comes first -- there is no point
        reviewing a join to a table nobody has landed."""
        result = assess_feasibility_with_joins(
            _kpi("analytics_event", "absent"),
            frozenset({"analytics_event"}),
            _ruled_out,
        )
        assert result.reason is FeasibilityReason.MISSING_TABLES
        assert result.unjoinable == ()

    def test_the_two_reasons_render_differently(self) -> None:
        """The whole point: they used to produce the same sentence."""
        missing = assess_feasibility(_kpi("absent"), frozenset())
        unjoinable = assess_feasibility_with_joins(_kpi(*BOTH), BOTH, _ruled_out)
        assert missing.rationale != unjoinable.rationale


class TestSingleTableKpisAreUnaffected:
    def test_one_table_never_needs_a_join(self) -> None:
        calls: List[Tuple[str, str]] = []

        def record(left: str, right: str) -> bool:
            calls.append((left, right))
            return False

        result = assess_feasibility_with_joins(
            _kpi("crm_deal"), frozenset({"crm_deal"}), record
        )
        assert result.verdict is FeasibilityVerdict.READY
        assert calls == [], "a one-table KPI asked about a join"


class TestBackwardCompatibility:
    def test_an_existing_construction_still_works_and_infers_its_reason(self) -> None:
        """`Feasibility` gained two fields; every existing caller constructs it without
        them and must keep the meaning it already had."""
        from egpt_retail.contracts.kpi import Feasibility

        ready = Feasibility(kpi_identifier="k", verdict=FeasibilityVerdict.READY)
        blocked = Feasibility(
            kpi_identifier="k",
            verdict=FeasibilityVerdict.BLOCKED,
            missing_tables=frozenset({"absent"}),
        )
        assert ready.reason is FeasibilityReason.NONE
        assert blocked.reason is FeasibilityReason.MISSING_TABLES
        assert blocked.unjoinable == ()

    def test_assess_feasibility_keeps_its_signature(self) -> None:
        """The pure set difference stays pure: two frozensets in, a verdict out, nothing
        holding a driver. The join check composes rather than replacing it."""
        import inspect

        parameters = list(inspect.signature(assess_feasibility).parameters)
        assert parameters == ["kpi", "available_tables"]


class TestTheOracleIsAskedOnlyAboutPairs:
    def test_every_pair_of_a_three_table_kpi_is_checked(self) -> None:
        asked: List[Tuple[str, str]] = []

        def record(left: str, right: str) -> bool:
            asked.append((left, right))
            return False

        tables = frozenset({"a_table", "b_table", "c_table"})
        kpi = KpiDefinition(
            identifier="three__way",
            name="n",
            description="d",
            sql="SELECT 1 FROM a_table JOIN b_table ON true JOIN c_table ON true",
            grain="overall",
            business_area="marketing",
            source_tables=tables,
        )
        assess_feasibility_with_joins(kpi, tables, record)
        assert sorted(asked) == [
            ("a_table", "b_table"),
            ("a_table", "c_table"),
            ("b_table", "c_table"),
        ]

    def test_one_unjoinable_pair_among_three_blocks_and_names_only_that_pair(
        self,
    ) -> None:
        tables = frozenset({"a_table", "b_table", "c_table"})
        kpi = KpiDefinition(
            identifier="three__way",
            name="n",
            description="d",
            sql="SELECT 1 FROM a_table JOIN b_table ON true JOIN c_table ON true",
            grain="overall",
            business_area="marketing",
            source_tables=tables,
        )
        result = assess_feasibility_with_joins(
            kpi, tables, lambda left, right: {left, right} == {"a_table", "c_table"}
        )
        assert result.reason is FeasibilityReason.NOT_JOINABLE
        assert result.unjoinable == (("a_table", "c_table"),)
