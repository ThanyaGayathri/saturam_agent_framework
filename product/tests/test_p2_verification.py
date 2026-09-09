"""P2 verification -- the DB-free parts of what the review fixed.

Each test here pins a defect the verification found, so the fix cannot quietly regress.
The graph-side defects (a re-harvest wiping reviewed edges; a failed apply blocking the
queue) are proved by the cookbooks against real stores.
"""

from __future__ import annotations

import pytest
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.semantic import JoinEdge
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.entrypoints.__main__ import COMMANDS, build_parser
from egpt_retail.semantic.harvest import HarvestDiff
from egpt_retail.semantic.proposals import (
    CARDINALITIES,
    ApplyOutcome,
    PayloadError,
    ProposalKind,
    Refusal,
    validate_payload,
)
from egpt_retail.semantic.refresh import RefreshReport
from egpt_retail.semantic.snapshots import structural
from pydantic import ValidationError


class TestClosedSetsAtTheGate:
    def test_business_area_outside_the_vocabulary_is_refused(self) -> None:
        """'marketng' parses, builds a Concept, and once in the graph is an area no screen
        lists and no KPI matches. The proposal gate is where the closed set is enforced."""
        with pytest.raises(PayloadError, match="not a permitted business_area"):
            validate_payload(
                ProposalKind.ENRICHMENT,
                {"name": "Campaign", "business_area": "marketng"},
                vocabularies=load_vocabulary_set(),
            )

    def test_a_permitted_business_area_passes(self) -> None:
        vocabularies = load_vocabulary_set()
        permitted = sorted(vocabularies["business_area"].values)[0]
        built = validate_payload(
            ProposalKind.ENRICHMENT,
            {"name": "Campaign", "business_area": permitted},
            vocabularies=vocabularies,
        )
        assert built.business_area == permitted

    @pytest.mark.parametrize("bad", ["1:n", "many", "MANY-TO-ONE", ""])
    def test_cardinality_outside_the_set_is_refused(self, bad: str) -> None:
        with pytest.raises(PayloadError, match="cardinality"):
            validate_payload(
                ProposalKind.JOIN,
                {
                    "left_table": "a",
                    "right_table": "b",
                    "on": "a.x = b.x",
                    "cardinality": bad,
                },
            )

    def test_every_cardinality_in_the_set_passes(self) -> None:
        for cardinality in CARDINALITIES:
            validate_payload(
                ProposalKind.JOIN,
                {
                    "left_table": "a",
                    "right_table": "b",
                    "on": "a.x = b.x",
                    "cardinality": cardinality,
                },
            )


class TestApplyOutcome:
    def test_reports_both_sides(self) -> None:
        outcome = ApplyOutcome(
            applied=("a",), failed=(Refusal("b", "no such concept"),)
        )
        assert str(outcome) == "1 applied, 1 failed"


class TestRowsStayInTheWarehouse:
    def test_structural_strips_sampled_values_and_nothing_else(self) -> None:
        payload = {
            "schema": "silver",
            "tables": [
                {
                    "name": "crm_contact",
                    "columns": [
                        {
                            "name": "email",
                            "data_type": "text",
                            "sampled_values": ["a@x", "b@y"],
                        },
                        {"name": "brand", "data_type": "text", "sampled_values": []},
                    ],
                }
            ],
        }
        stripped = structural(payload)
        columns = stripped["tables"][0]["columns"]
        assert all("sampled_values" not in column for column in columns)
        assert [c["name"] for c in columns] == ["email", "brand"]
        assert columns[0]["data_type"] == "text"
        # The input is not mutated: a caller still holding the harvest keeps its samples.
        assert payload["tables"][0]["columns"][0]["sampled_values"] == ["a@x", "b@y"]


class TestMemoryTableName:
    @pytest.mark.parametrize("bad", ["memory.vectors", "Memory", "drop table; --", ""])
    def test_a_non_identifier_is_refused_at_settings_time(self, bad: str) -> None:
        with pytest.raises(ValidationError, match="EGPT_RETAIL_MEMORY_TABLE"):
            RetailSettings(retail_memory_table=bad)

    def test_the_default_and_a_cookbook_name_pass(self) -> None:
        assert RetailSettings().retail_memory_table == "memory_vectors"
        assert (
            RetailSettings(
                retail_memory_table="memory_vectors_k1_cookbook"
            ).retail_memory_table
            == "memory_vectors_k1_cookbook"
        )


class TestHarvestEntrypoint:
    def test_harvest_is_a_command(self) -> None:
        assert "harvest" in COMMANDS
        args = build_parser().parse_args(
            ["harvest", "--schema", "gold", "--version", "v9"]
        )
        assert (args.command, args.schema, args.version) == ("harvest", "gold", "v9")

    def test_report_lines_say_what_happened(self) -> None:
        report = RefreshReport(
            version="v2",
            previous_version="v1",
            schema="silver",
            tables=5,
            columns=40,
            diff=HarvestDiff(removed_columns=("crm_contact.brand",)),
        )
        lines = report.lines()
        assert report.changed
        assert any("v2" in line and "v1" in line for line in lines)
        assert any(
            line.startswith("drift:") and "crm_contact.brand" in line for line in lines
        )

    def test_a_first_run_has_no_diff(self) -> None:
        report = RefreshReport("v1", None, "silver", 5, 40, diff=None)
        assert not report.changed
        assert any("no previous snapshot" in line for line in report.lines())


class TestCanonicalJoin:
    """A join is undirected in the graph; the stored edge runs from the smaller name."""

    def test_already_canonical_is_untouched(self) -> None:
        from egpt_retail.semantic.store import canonical_join

        edge = JoinEdge("a_table", "b_table", "a.x = b.x", "many-to-one")
        assert canonical_join(edge) is edge

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("many-to-one", "one-to-many"),
            ("one-to-many", "many-to-one"),
            ("one-to-one", "one-to-one"),
            ("many-to-many", "many-to-many"),
        ],
    )
    def test_a_swapped_pair_inverts_cardinality_and_keeps_the_predicate(
        self, given: str, expected: str
    ) -> None:
        from egpt_retail.semantic.store import canonical_join

        edge = canonical_join(JoinEdge("b_table", "a_table", "b.x = a.x", given))
        assert (edge.left_table, edge.right_table) == ("a_table", "b_table")
        assert edge.cardinality == expected
        assert edge.on == "b.x = a.x"
