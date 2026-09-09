"""R4 -- the conformance specification per entity, read beside the contract it implements.

G8 asks for "a conformance specification per entity, kept as SQL beside the transforms". This
script is the evidence that it exists and that it says what the landing contract says: for
each of the five streams it prints the contract's rules -- keys, watermark, required columns,
which nulls are legitimate -- and then the SQL statement that implements them.

The point is that a person can read the transform. Everything a row goes through between
landing and silver is in one file per entity, in SQL, with no Python in the path. That is D6
("transformation logic executed as SQL inside the warehouse") and the standing rule that
transforms stay inspectable so they can be handed over later.

Needs no services and no database.

Run:
    .venv/bin/python cookbook/94_retail/R04_conformance_sql.py
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

from egpt_retail.contracts.landing import LANDING_SCHEMAS  # noqa: E402
from egpt_retail.warehouse.schema import conform_script, silver_table  # noqa: E402
from egpt_retail.warehouse.sql import conform_sql  # noqa: E402

SHOW_SQL = "--quiet" not in sys.argv


def strip_comments(sql):
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


print("R4 -- conformance specification per entity (G8)")
print("=" * 78)
print()

for stream_id in sorted(LANDING_SCHEMAS):
    schema = LANDING_SCHEMAS[stream_id]
    body = conform_sql(stream_id)
    stripped = strip_comments(body)
    structural = list(dict.fromkeys([*schema.key_columns, schema.watermark_column]))
    business = [c for c in schema.required_columns if c not in structural]

    print(f"{stream_id}  ->  {silver_table(stream_id)}")
    print("-" * 78)
    print(f"  grain          {schema.grain}")
    print(f"  keys           {', '.join(schema.key_columns)}")
    print(f"  watermark      {schema.watermark_column}")
    print(f"  business cols  {', '.join(business) or '(none)'}")
    print(f"  legit nulls    {', '.join(sorted(schema.nullable_columns)) or '(none)'}")
    print(
        f"  transform      egpt_retail/warehouse/sql/conform/{conform_script(stream_id)}"
    )

    # Every required column has to appear, or a column silently stops reaching silver.
    for column in schema.required_columns:
        assert column in stripped, f"{stream_id}: transform never mentions {column!r}"

    # Structural columns gate entry to silver; business columns must not, because the
    # contract says a missing business value is reported and the row is kept.
    for column in structural:
        assert re.search(rf"{column}\s+IS\s+NOT\s+NULL", stripped), (
            f"{stream_id}: {column!r} is structural but is not filtered on"
        )
    for column in business:
        assert not re.search(rf"\b{column}\s+IS\s+NOT\s+NULL", stripped), (
            f"{stream_id}: {column!r} is a business column but is filtered on, which drops "
            "the row instead of reporting the defect"
        )

    # The fold order the landing contract specifies, and the guard that carries it across runs.
    assert re.search(r"ingested_at\s+ASC", stripped), f"{stream_id}: no ingested_at ASC"
    assert re.search(r"line_number\s+ASC", stripped), f"{stream_id}: no line_number ASC"
    if schema.watermark_column in schema.key_columns:
        assert "DO NOTHING" in stripped
        print("  dedup          watermark is a key column -> duplicate collapse only")
    else:
        assert re.search(rf"{schema.watermark_column}\s+DESC", stripped)
        assert re.search(
            rf"EXCLUDED\.{schema.watermark_column}\s*>\s*s\.{schema.watermark_column}",
            stripped,
        )
        print(
            "  dedup          greatest watermark, then earliest-pulled file, then line"
        )
        print(
            "  late arrivals  ON CONFLICT ... WHERE EXCLUDED > stored (strictly greater)"
        )
    print()

    if SHOW_SQL:
        for line in body.rstrip().splitlines():
            print(f"    {line}")
        print()

print("=" * 78)
print(
    "Evidence: five entities, five statements, each naming every required column of its\n"
    "stream, filtering on exactly the structural ones, and carrying the landing contract's\n"
    "fold order. The rules a row is subject to are readable without a database and without\n"
    "reading any Python. Pass --quiet to print the summary without the statements."
)
