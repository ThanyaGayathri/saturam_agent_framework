"""Unit tests for the read-only table surface `api/routers/semantic.py` mounts in contract
1.2.0 (`GET /semantic/tables`, `GET /semantic/tables/{table_name}`) -- DB-free: only the wire
mapping (`_column_out`/`_table_out`), no Neo4j.
"""

from __future__ import annotations

from egpt_retail.api.routers import semantic as semantic_module
from egpt_retail.contracts.semantic import Column, ProposalStatus, Table, TableRef


def _column(**overrides) -> Column:
    defaults = dict(
        name="email",
        data_type="text",
        description="Customer's email address.",
        sampled_values=("alice@example.test", "bob@example.test"),
        is_personal_data=False,
        meaning="",
        concept=None,
        joinable=False,
    )
    defaults.update(overrides)
    return Column(**defaults)


def test_column_out_keeps_sampled_values_for_an_ordinary_column():
    out = semantic_module._column_out(_column(is_personal_data=False))
    assert out.sampled_values == ["alice@example.test", "bob@example.test"]


def test_column_out_strips_sampled_values_for_a_personal_data_column():
    """Round-1 review, finding #2 (critical): a personal-data column's real observed values
    were returned unredacted over this HTTP surface, contradicting the router's own claim that
    it shows only what the compose agent was shown -- the agent's prompt renderer omits such a
    column entirely. The column itself must still appear (so the editor can render its
    "blocked" pill), with `is_personal_data` intact, but carrying no real data."""
    out = semantic_module._column_out(_column(is_personal_data=True))
    assert out.is_personal_data is True
    assert out.sampled_values == []
    # The column's own name and description (what it *is*, not real data) are unaffected --
    # only the observed values are stripped.
    assert out.name == "email"
    assert out.description == "Customer's email address."


def test_table_out_redacts_only_the_personal_data_columns_it_contains():
    table = Table(
        ref=TableRef(name="customers", schema="silver", row_count=100),
        columns=(
            _column(
                name="customer_id", is_personal_data=False, sampled_values=("c_1",)
            ),
            _column(name="email", is_personal_data=True),
        ),
        description="Customer records.",
        status=ProposalStatus.ACCEPTED,
    )
    out = semantic_module._table_out(table)
    by_name = {c.name: c for c in out.columns}
    assert by_name["customer_id"].sampled_values == ["c_1"]
    assert by_name["email"].sampled_values == []
    assert by_name["email"].is_personal_data is True
