"""R4 without a warehouse -- the pure schema map.

Every warehouse table name is derived from the landing contract rather than typed out a
second time, and these pin that derivation. They need no database, no storage and no
services, per the convention the R1 and R2 tests set: anything needing Postgres is a
cookbook scenario run by hand and recorded in TEST_LOG.md.
"""

from __future__ import annotations

import pytest

from egpt_retail.contracts.landing import LANDING_SCHEMAS
from egpt_retail.warehouse.schema import (
    DDL_SCRIPTS,
    SILVER_SCHEMA,
    SILVER_TABLES,
    check_script,
    conform_script,
    silver_table,
)


def test_every_landed_stream_has_a_silver_table() -> None:
    """The five entities are the five streams. Not four, and not a sixth nobody conforms."""
    assert set(SILVER_TABLES) == set(LANDING_SCHEMAS)
    assert len(SILVER_TABLES) == 5


def test_silver_table_is_schema_qualified() -> None:
    assert silver_table("crm.contact") == f"{SILVER_SCHEMA}.crm_contact"
    assert (
        silver_table("crm.deal_stage_history")
        == f"{SILVER_SCHEMA}.crm_deal_stage_history"
    )
    assert silver_table("analytics.event") == f"{SILVER_SCHEMA}.analytics_event"


def test_silver_table_refuses_an_unknown_stream_by_naming_the_known_ones() -> None:
    """Mirrors landing_schema's refusal: a mistyped stream is told the alternatives.

    A bare KeyError would be the same information withheld.
    """
    with pytest.raises(ValueError) as excinfo:
        silver_table("crm.contacts")
    message = str(excinfo.value)
    assert "crm.contacts" in message
    for stream_id in LANDING_SCHEMAS:
        assert stream_id in message


def test_table_names_carry_no_dot() -> None:
    """A dot is a schema separator in SQL, so no stream id survives into a table name."""
    for stem in SILVER_TABLES.values():
        assert "." not in stem


def test_silver_table_names_are_distinct() -> None:
    """Two streams collapsing onto one table would silently merge two entities."""
    assert len(set(SILVER_TABLES.values())) == len(SILVER_TABLES)


def test_conform_script_names_match_the_table_stems() -> None:
    """One conformance file per stream, named the same way its table is."""
    for stream_id, stem in SILVER_TABLES.items():
        assert conform_script(stream_id) == f"{stem}.sql"


def test_conform_script_refuses_an_unknown_stream() -> None:
    with pytest.raises(ValueError):
        conform_script("nope.nope")


def test_check_script_refuses_anything_that_is_not_an_identifier() -> None:
    """The gate name reaches a filename, so a path separator must never get through."""
    assert check_script("row_count_delta") == "row_count_delta.sql"
    for bad in ("../secrets", "a/b", "", "drop table"):
        with pytest.raises(ValueError):
            check_script(bad)


def test_ddl_applies_quality_before_landing() -> None:
    """landing.raw_row.load_id references quality.load_run, so the order is a dependency."""
    order = list(DDL_SCRIPTS)
    assert order.index("020_quality.sql") < order.index("030_landing.sql")


def test_ddl_applies_coercion_before_the_silver_tables() -> None:
    """The conformance statements call silver.try_timestamptz and silver.try_integer."""
    order = list(DDL_SCRIPTS)
    coercion = order.index("040_coercion.sql")
    silver_scripts = [name for name in order if "_silver_" in name]
    assert silver_scripts, "expected the five silver DDL scripts to be named *_silver_*"
    assert all(coercion < order.index(name) for name in silver_scripts)


def test_every_silver_table_has_a_ddl_script() -> None:
    """A table with no DDL is a conformance statement that will fail at midnight."""
    for stem in SILVER_TABLES.values():
        assert any(name.endswith(f"_silver_{stem}.sql") for name in DDL_SCRIPTS), (
            f"no DDL script creates silver.{stem}"
        )


def test_landing_stream_id_expression_enumerates_the_contract_exactly() -> None:
    """The generated column must name every stream, and nothing that is not one.

    Two failures this catches, both silent. A stream missing from the CASE lands rows with a
    NULL stream_id that no conformance statement claims. And concatenating an unvalidated
    CRM record_type -- 'crm.' || whatever arrived -- produces a plausible-looking stream_id
    that likewise matches nothing, so the row disappears without being reported. NULL is the
    honest marker for "this row belongs to no stream"; a made-up id is not.
    """
    from egpt_retail.warehouse.sql import read_sql

    body = read_sql("ddl/030_landing.sql")
    expression = body[body.index("stream_id") : body.index("STORED")]

    for stream_id in LANDING_SCHEMAS:
        assert f"'{stream_id}'" in expression or stream_id.startswith("crm."), (
            f"{stream_id} is not produced by the landing stream_id expression"
        )

    crm_record_types = sorted(
        stream_id.split(".", 1)[1]
        for stream_id in LANDING_SCHEMAS
        if stream_id.startswith("crm.")
    )
    for record_type in crm_record_types:
        assert f"'{record_type}'" in expression, (
            f"CRM record type {record_type!r} is not enumerated, so its rows land with a "
            "NULL stream_id and are never conformed"
        )
    assert "IN (" in expression, (
        "CRM record types must be enumerated, not concatenated blindly: an unrecognised one "
        "has to yield NULL rather than a stream_id no conformance statement matches"
    )
