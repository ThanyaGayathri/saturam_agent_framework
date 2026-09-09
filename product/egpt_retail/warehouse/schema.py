"""Warehouse schema names, and the map from a landed stream to its silver table.

Pure. No I/O, no database, no engine -- so the repository's sync/async rule does not apply
(the construction exemption in ``CLAUDE.md``). Every name the rest of the package uses to
address a table comes from here, and every one of them is derived from
:data:`~egpt_retail.contracts.landing.LANDING_SCHEMAS` rather than typed out a second time.

That derivation is the point. C1 owns the set of streams; a sixth one added to the contract
must not leave R4 with five silver tables and a stream nothing conforms. Because the table
name, the conformance filename and the DDL list are all computed from the contract,
``product/tests/test_r4_sql_assets.py`` can assert the three agree, and a contract change
fails the test suite instead of failing quietly at midnight.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Dict, Mapping, Tuple

from egpt_retail.contracts.landing import LANDING_SCHEMAS

__all__ = [
    "LANDING_SCHEMA",
    "SILVER_SCHEMA",
    "QUALITY_SCHEMA",
    "RAW_ROW_TABLE",
    "DDL_SCRIPTS",
    "GOLD_SCHEMA",
    "GOLD_TABLES",
    "gold_view",
    "SILVER_TABLES",
    "silver_table",
    "conform_script",
    "check_script",
    "defect_script",
    "COERCED_COLUMNS",
]

#: The warehouse schemas: three from R4, gold from R5. Separate from the control plane's
#: tables entirely -- this is a different database (D5), not a different schema in the same one.
LANDING_SCHEMA = "landing"
SILVER_SCHEMA = "silver"
QUALITY_SCHEMA = "quality"
GOLD_SCHEMA = "gold"

#: The fixed relations in gold (070_gold.sql). The per-KPI views are not fixed: they are named
#: by :func:`gold_view` and created by materialisation.
GOLD_TABLES: Tuple[str, ...] = ("kpi_value", "kpi_run", "kpi_latest_run")

#: The single landing table. One table rather than one per stream: landing holds rows as-is,
#: and CRM multiplexes three streams into one file, so per-stream landing tables would force
#: the routing decision into Python -- a transformation, in code, which D6 rules out.
RAW_ROW_TABLE = f"{LANDING_SCHEMA}.raw_row"

#: Applied in this order, every one idempotent. ``020_quality`` precedes ``030_landing``
#: because ``landing.raw_row.load_id`` references ``quality.load_run``, and ``040_coercion``
#: precedes the silver tables because their conformance calls those functions.
DDL_SCRIPTS: Tuple[str, ...] = (
    "010_schemas.sql",
    "020_quality.sql",
    "030_landing.sql",
    "040_coercion.sql",
    "050_silver_analytics_event.sql",
    "051_silver_crm_contact.sql",
    "052_silver_crm_deal.sql",
    "053_silver_crm_deal_stage_history.sql",
    "054_silver_session_replay_session.sql",
    "060_views.sql",
    "065_readonly_role.sql",
    "070_gold.sql",
    "075_gold_grants.sql",
)


def _table_stem(stream_id: str) -> str:
    """``crm.deal_stage_history`` -> ``crm_deal_stage_history``.

    A dot is a schema separator in SQL, so a stream id cannot be a table name unchanged.
    One substitution, applied everywhere, so the table, its DDL file and its conformance
    file are always spelled the same way.
    """
    return stream_id.replace(".", "_")


#: Every landed stream mapped to its unqualified silver table name. Frozen for the same
#: reason ``LANDING_SCHEMAS`` is: the set of entities is a contract, not a runtime registration.
SILVER_TABLES: Mapping[str, str] = MappingProxyType(
    {stream_id: _table_stem(stream_id) for stream_id in LANDING_SCHEMAS}
)


def gold_view(kpi_identifier: str) -> str:
    """The bare name of a KPI's per-identifier view in gold, e.g. ``kpi_supplier__on_time_rate_30d``.

    The API contract queries gold this way (``paths/playground.yaml``): ``gold.kpi_<identifier>``
    with the grain keys as columns. A ``KpiDefinition.identifier`` is validated to be a Python
    identifier, which is also a safe unquoted SQL identifier, so no quoting is needed.
    """
    return f"kpi_{kpi_identifier}"


def silver_table(stream_id: str) -> str:
    """The schema-qualified silver table for a landed stream.

    Raises ``ValueError`` naming the known streams when ``stream_id`` is not one, mirroring
    :func:`~egpt_retail.contracts.landing.landing_schema` -- a caller that mistypes a stream
    should be told what the alternatives are, not handed a ``KeyError``.
    """
    try:
        stem = SILVER_TABLES[stream_id]
    except KeyError:
        known = ", ".join(sorted(SILVER_TABLES))
        raise ValueError(
            f"unknown stream_id {stream_id!r}. Known streams: {known}."
        ) from None
    return f"{SILVER_SCHEMA}.{stem}"


def conform_script(stream_id: str) -> str:
    """The conformance SQL filename for a stream, relative to ``sql/conform/``."""
    # Validate through silver_table, so an unknown stream gets the same message here as it
    # does there rather than a second, subtly different one.
    silver_table(stream_id)
    return f"{_table_stem(stream_id)}.sql"


def check_script(gate: str) -> str:
    """The measurement SQL filename for a gate, relative to ``sql/checks/``."""
    if not gate or not gate.replace("_", "").isalnum():
        raise ValueError(f"gate must be a bare identifier, got {gate!r}")
    return f"{gate}.sql"


def defect_script(name: str) -> str:
    """The defect-detection SQL filename, relative to ``sql/defects/``."""
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"name must be a bare identifier, got {name!r}")
    return f"{name}.sql"


def _coerced(stream_id: str) -> Dict[str, str]:
    """Columns the conformance SQL casts, and what it casts them to.

    The timestamps come from the contract: its ``watermark_column`` plus its
    ``timestamp_columns``, which landing contract 1.1.0 added precisely so a non-watermark
    timestamp is format-checked rather than silently nulled. R4 does not restate that list.

    The integers are R4's own, declared here, because nothing in the landing contract says
    ``rage_click_count`` is a number -- it type-checks timestamps and nothing else, so a
    malformed count still reaches conformance unreported. That is the half of the gap R4
    still owns.

    ``product/tests/test_r4_sql_assets.py`` asserts this agrees with the columns each
    conformance statement actually wraps in a ``silver.try_*`` call, so the declaration
    cannot drift from the SQL it describes.
    """
    schema = LANDING_SCHEMAS[stream_id]
    columns: Dict[str, str] = {schema.watermark_column: "timestamptz"}
    for column in schema.timestamp_columns:
        columns[column] = "timestamptz"
    columns.update(_INTEGER_COLUMNS.get(stream_id, {}))
    return columns


#: Numeric columns, which the landing contract does not type-check. Everything else lands and
#: stays text.
_INTEGER_COLUMNS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "session_replay.session": MappingProxyType(
            {
                "rage_click_count": "integer",
                "dead_click_count": "integer",
                "script_error_count": "integer",
            }
        )
    }
)


#: Every column R4 coerces, per stream, with its target type.
COERCED_COLUMNS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {stream_id: MappingProxyType(_coerced(stream_id)) for stream_id in LANDING_SCHEMAS}
)
