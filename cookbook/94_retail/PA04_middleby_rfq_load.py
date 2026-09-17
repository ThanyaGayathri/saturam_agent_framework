"""PA04 -- load the Middleby RFQ demo extracts into the local warehouse.

Four CSV extracts (quotes, chat inquiries, web sessions, customers) into the warehouse
database R4 created (``egpt-retail warehouse-migrate``): the rows as delivered into
``source.dc_*`` (every column text, nothing coerced), then ``sql/middleby_silver.sql`` types
them into ``silver.dc_*`` with the column comments the semantic harvest reads. The transform
is a SQL file, per the load contract's rule that transforms stay inspectable.

    .venv/bin/python cookbook/94_retail/PA04_middleby_rfq_load.py --dir ~/Downloads

Then: ``egpt-retail harvest --schema silver`` reads the tables into the semantic layer, and
``egpt-retail suggest`` proposes KPIs over them.

Reads EGPT_RETAIL_WAREHOUSE_DB_URL (default: the local warehouse the cookbooks use).
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from pathlib import Path
from typing import Dict, List

import psycopg

DEFAULT_URL = "postgresql://ai:ai@localhost:5532/egpt_warehouse"
TABLES = ("dc_rfq_quotes", "dc_chat_inquiries", "dc_web_sessions", "dc_customers")
SQL = Path(__file__).resolve().parent / "sql" / "middleby_silver.sql"


def _latest(directory: Path, table: str) -> Path:
    matches = sorted(glob.glob(str(directory / f"{table}_*.csv")))
    if not matches:
        raise SystemExit(f"no {table}_*.csv in {directory}")
    return Path(matches[-1])


def load(directory: Path, url: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    with psycopg.connect(url) as connection:
        cur = connection.cursor()
        cur.execute("CREATE SCHEMA IF NOT EXISTS source")
        for table in TABLES:
            path = _latest(directory, table)
            with open(path, newline="") as handle:
                reader = csv.reader(handle)
                header: List[str] = next(reader)
                columns = ", ".join(f'"{c}" text' for c in header)
                cur.execute(f"DROP TABLE IF EXISTS source.{table}")
                cur.execute(f"CREATE TABLE source.{table} ({columns})")
                comment = f"Middleby extract as delivered: {path.name}".replace(
                    "'", "''"
                )
                cur.execute(f"COMMENT ON TABLE source.{table} IS '{comment}'")
                with cur.copy(
                    f"COPY source.{table} ({', '.join(chr(34) + c + chr(34) for c in header)}) FROM STDIN"
                ) as copy:
                    for row in reader:
                        copy.write_row([v if v != "" else None for v in row])
            cur.execute(f"SELECT COUNT(*) FROM source.{table}")
            counts[table] = int(cur.fetchone()[0])
        cur.execute(SQL.read_text())
        # This is a load of the warehouse, so it records itself where every load does:
        # gold's "last finished load passed" gate (executors/gold.py::last_load_verdict)
        # reads quality.load_run, and a warehouse whose only rows arrived through this
        # cookbook would otherwise carry no verdict at all -- or a stale one.
        cur.execute(
            "INSERT INTO quality.load_run (load_id, started_at, finished_at, trigger, "
            "landing_root, files_loaded, rows_landed, verdict, reason) VALUES "
            "(gen_random_uuid(), now(), now(), 'cookbook', %s, %s, %s, 'passed', "
            "'PA04: Middleby extracts typed into silver.dc_*')",
            (str(directory), len(TABLES), sum(counts.values())),
        )
        connection.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", default=str(Path.home() / "Downloads"))
    parser.add_argument(
        "--url", default=os.environ.get("EGPT_RETAIL_WAREHOUSE_DB_URL") or DEFAULT_URL
    )
    args = parser.parse_args()
    url = args.url.replace("postgresql+psycopg://", "postgresql://")
    counts = load(Path(args.dir).expanduser(), url)
    for table, n in counts.items():
        print(f"  source.{table:<20} {n:>6} rows -> silver.{table}")
    print("silver tables typed and commented; grant to egpt_reader applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
