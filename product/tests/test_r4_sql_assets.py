"""R4 without a warehouse -- the shipped SQL is present, coherent, and says what C1 says.

The transforms are files rather than Python (D6), which buys inspectability and costs the
usual thing files cost: nothing type-checks them. These tests are what stands in for that.
They read the ``.sql`` on disk and assert it still agrees with the landing contract, still
holds the de-duplication rule the contract specifies, and is still shipped by the package.

None of them needs a database.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import pytest

from egpt_retail.contracts.landing import LANDING_SCHEMAS
from egpt_retail.warehouse.schema import DDL_SCRIPTS, conform_script
from egpt_retail.warehouse.sql import conform_sql, read_sql

_PRODUCT_ROOT = Path(__file__).resolve().parents[1]
_SQL_ROOT = _PRODUCT_ROOT / "egpt_retail" / "warehouse" / "sql"

#: Every gate that owns a measurement statement. Kept here rather than imported so that
#: deleting a gate's file and its registration together still fails a test.
GATES = ("row_count_delta", "key_null_rate", "referential_integrity")


def _strip_comments(sql: str) -> str:
    """The statement text with ``--`` line comments removed.

    Comments in these files carry the reasoning, and they mention column names and SQL
    keywords freely -- so any assertion about what a statement *does* has to read past them
    or it will pass on a comment and miss a deleted clause.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_every_stream_has_a_conformance_file_and_there_are_no_orphans() -> None:
    """Driven off the contract, so a sixth stream fails here rather than at midnight."""
    expected = {conform_script(stream_id) for stream_id in LANDING_SCHEMAS}
    on_disk = {path.name for path in (_SQL_ROOT / "conform").glob("*.sql")}
    assert on_disk == expected


def test_every_ddl_script_declared_is_on_disk_and_vice_versa() -> None:
    on_disk = {path.name for path in (_SQL_ROOT / "ddl").glob("*.sql")}
    assert on_disk == set(DDL_SCRIPTS)


def test_every_gate_has_a_measurement_file() -> None:
    on_disk = {path.name for path in (_SQL_ROOT / "checks").glob("*.sql")}
    assert on_disk == {f"{gate}.sql" for gate in GATES}


@pytest.mark.parametrize("stream_id", sorted(LANDING_SCHEMAS))
def test_conformance_mentions_every_required_column(stream_id: str) -> None:
    """A column silently dropped from a transform is a column silently absent from silver.

    This is the check that catches the contract gaining a required column and the SQL not
    hearing about it.
    """
    body = _strip_comments(conform_sql(stream_id))
    for column in LANDING_SCHEMAS[stream_id].required_columns:
        assert column in body, f"{stream_id}: conformance never mentions {column!r}"


@pytest.mark.parametrize("stream_id", sorted(LANDING_SCHEMAS))
def test_conformance_carries_the_tie_break_rule(stream_id: str) -> None:
    """The landing contract's fold order, asserted rather than trusted to a comment.

    ``ingested_at ASC`` then ``line_number ASC`` is what makes an exact watermark tie resolve
    to the earliest-pulled file -- the behaviour C01_landing_contract.py pins in Python on
    h_9002. It is one line in a long statement and the single most plausible thing for a
    later editor to "simplify", which is why it is a test and not a comment.
    """
    body = _strip_comments(conform_sql(stream_id))
    assert re.search(r"ingested_at\s+ASC", body), f"{stream_id}: no ingested_at ASC"
    assert re.search(r"line_number\s+ASC", body), f"{stream_id}: no line_number ASC"


@pytest.mark.parametrize("stream_id", sorted(LANDING_SCHEMAS))
def test_conformance_prefers_the_greatest_watermark(stream_id: str) -> None:
    """``<watermark> DESC`` -- except where the watermark is itself a key column.

    ``analytics.event`` keys on ``occurred_at``, which is also its watermark, so rows sharing
    the key necessarily share the watermark and there is no greater one to prefer. Ordering
    by it descending there would be a no-op dressed up as a rule, so that file uses
    ON CONFLICT DO NOTHING instead -- an event is immutable. Both shapes are correct; the
    contract decides which, so the test asks the contract.
    """
    schema = LANDING_SCHEMAS[stream_id]
    body = _strip_comments(conform_sql(stream_id))
    if schema.watermark_column in schema.key_columns:
        assert "DO NOTHING" in body, (
            f"{stream_id}: its watermark is a key column, so there is no newer version of a "
            "row to prefer and the conflict clause should do nothing"
        )
        return
    assert re.search(rf"{schema.watermark_column}\s+DESC", body), (
        f"{stream_id}: no '{schema.watermark_column} DESC' -- the greatest watermark must win"
    )
    assert re.search(
        rf"EXCLUDED\.{schema.watermark_column}\s*>\s*s\.{schema.watermark_column}", body
    ), f"{stream_id}: no strictly-greater guard, so a late-arriving older row would win"


@pytest.mark.parametrize("stream_id", sorted(LANDING_SCHEMAS))
def test_conformance_filters_on_every_structural_column(stream_id: str) -> None:
    """Key and watermark columns gate entry to silver; business columns must not."""
    schema = LANDING_SCHEMAS[stream_id]
    body = _strip_comments(conform_sql(stream_id))
    structural = set(schema.key_columns) | {schema.watermark_column}
    for column in structural:
        assert re.search(rf"{column}\s+IS\s+NOT\s+NULL", body), (
            f"{stream_id}: {column!r} is structural but is not filtered on, so a row missing "
            "it would reach silver unidentifiable"
        )
    business = set(schema.required_columns) - structural
    for column in business:
        assert not re.search(rf"\b{column}\s+IS\s+NOT\s+NULL", body), (
            f"{stream_id}: {column!r} is a business column but is filtered on, which drops "
            "the row instead of reporting the defect"
        )


def _statement_count(sql: str) -> int:
    """Top-level statements, counting semicolons outside string literals and dollar quotes."""
    body = _strip_comments(sql)
    body = re.sub(r"\$[a-zA-Z_]*\$.*?\$[a-zA-Z_]*\$", "''", body, flags=re.DOTALL)
    body = re.sub(r"'(?:[^']|'')*'", "''", body)
    return len([part for part in body.split(";") if part.strip()])


@pytest.mark.parametrize(
    "relative_path",
    [f"conform/{conform_script(s)}" for s in sorted(LANDING_SCHEMAS)]
    + [f"checks/{gate}.sql" for gate in GATES],
)
def test_parameterised_files_hold_exactly_one_statement(relative_path: str) -> None:
    """psycopg refuses multiple statements when parameters are supplied.

    Splitting one of these into two would fail only at runtime, against a real database,
    which is the slowest possible place to learn it.
    """
    assert _statement_count(read_sql(relative_path)) == 1


@pytest.mark.parametrize(
    "relative_path",
    [f"ddl/{name}" for name in DDL_SCRIPTS]
    + [f"conform/{conform_script(s)}" for s in sorted(LANDING_SCHEMAS)]
    + [f"checks/{gate}.sql" for gate in GATES],
)
def test_no_double_colon_casts(relative_path: str) -> None:
    """``CAST(x AS type)`` only.

    ``::`` collides with the ``:name`` parameter syntax these files are bound with, and with
    psql's client-side variable substitution -- which is what makes running one of these by
    hand possible, and is the whole point of keeping them as files.
    """
    body = _strip_comments(read_sql(relative_path))
    body = re.sub(r"'(?:[^']|'')*'", "''", body)
    offenders = [line for line in body.splitlines() if "::" in line]
    assert not offenders, f"{relative_path}: use CAST(x AS type), found {offenders}"


def _package_data_block() -> str:
    """The ``[tool.setuptools.package-data]`` section of product/pyproject.toml, as text.

    Read as text rather than parsed. ``tomllib`` is stdlib only from Python 3.11 and this
    package supports 3.10 (`requires-python = ">=3.10"`), and pulling in a TOML backport as a
    test dependency to check one list of strings costs more than it is worth. The section is
    sliced out rather than the whole file searched, so a glob that happens to be mentioned in
    a comment elsewhere cannot satisfy the assertion below.
    """
    body = (_PRODUCT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    start = body.index("[tool.setuptools.package-data]")
    end = body.index("\n[", start + 1)
    return body[start:end]


def test_every_sql_directory_is_covered_by_a_package_data_glob() -> None:
    """Otherwise an installed copy has a warehouse package that cannot create a table.

    product/pyproject.toml's own comment warns about exactly this failure for the ontology,
    fixtures and prompts. It is invisible in an editable checkout, which is where all the
    development happens, so it is worth a test rather than vigilance.

    Checks the directory rather than each file: a setuptools glob does not recurse, so a new
    subdirectory under ``sql/`` is the thing that silently stops being shipped, while a file
    added beside its siblings is already covered.
    """
    block = _package_data_block()
    package_root = _PRODUCT_ROOT / "egpt_retail"
    directories = {path.parent for path in _SQL_ROOT.rglob("*.sql")}
    assert directories, "no SQL found at all, which is itself the bug"
    for directory in sorted(directories):
        glob = f"{directory.relative_to(package_root).as_posix()}/*.sql"
        assert glob in block, (
            f"{glob!r} is not in product/pyproject.toml's package-data, so an installed "
            "wheel would not ship the SQL in that directory"
        )


def test_ddl_is_idempotent_by_construction() -> None:
    """Every DDL script must be safe to re-run: warehouse-migrate has no version ledger.

    R4 deliberately does not extend MigrationManager (it is control-plane only, and tracker
    #178 hardened it against stamping tables it never migrates), so re-runnability is the
    property that replaces a schema version.
    """
    creates: Dict[str, List[str]] = {}
    unguarded_roles: List[str] = []
    for name in DDL_SCRIPTS:
        body = _strip_comments(read_sql(f"ddl/{name}"))
        bad = re.findall(
            r"CREATE\s+(?!SCHEMA\s+IF|TABLE\s+IF|INDEX\s+IF|UNIQUE\s+INDEX\s+IF|OR\s+REPLACE)([A-Z ]+?)\s",
            body,
        )
        # CREATE ROLE is the one statement this regex cannot check directly: Postgres has no
        # `CREATE ROLE IF NOT EXISTS`, so a guarded file wraps it in `DO $$ ... IF NOT EXISTS
        # (SELECT ...) $$` instead -- a pattern this text scan cannot see through by keyword
        # alone. Exempting every "ROLE" match unconditionally would also wave through a
        # *future*, genuinely unguarded `CREATE ROLE` in some other file, defeating the check
        # it was added to satisfy -- so the exemption only applies when this file's own text
        # demonstrates the guard, not merely because the keyword is ROLE.
        if "ROLE" in bad:
            bad = [b for b in bad if b != "ROLE"]
            if not re.search(r"IF\s+NOT\s+EXISTS\s*\(\s*SELECT\b", body):
                unguarded_roles.append(name)
        if bad:
            creates[name] = bad
    assert not creates and not unguarded_roles, (
        f"non-idempotent CREATE statements, which fail on the second warehouse-migrate: "
        f"{creates}; unguarded CREATE ROLE (no `IF NOT EXISTS (SELECT ...)` guard) in: "
        f"{unguarded_roles}"
    )
