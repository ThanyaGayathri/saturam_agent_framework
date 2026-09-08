"""C1 -- the landing contract, checked by playing Lane R's loader.

The P0 deliverable is a written contract, but a contract about a file shape is worth exactly
as much as a loader that can load against it without asking a question. This script is that
loader: it discovers the three hand-written fixture files under
`product/egpt_retail/contracts/_fixtures/landing/`, parses their paths and rows using nothing
but `egpt_retail.contracts.landing`, and reports every conformance defect it finds rather
than dropping the row. A second half simulates a re-pulled, overlapping CRM window and runs
the dedup rule `LANDING_CONTRACT.md` specifies.

Needs no services. This is a product example, not a framework one -- it imports from
`product/`, which is outside the published `egpt` wheel, so it puts `product/` on `sys.path`
itself.

Run:
    .venv/bin/python cookbook/94_retail/C01_landing_contract.py

`EGPT_LANDING_FIXTURES_ROOT` overrides where the fixtures are read from -- the directory
must still hold a `raw/` subtree laid out per the landing contract. This is what
`C01_minio_roundtrip.sh` uses to re-run this exact verification against fixtures that have
been uploaded to and downloaded back from a real S3-compatible store, rather than duplicating
the checks below in shell.
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

from egpt_retail.contracts.landing import (  # noqa: E402
    SourceName,
    parse_landing_path,
    resolve_stream_id,
    validate_row,
)

FIXTURES_ROOT = Path(
    os.environ.get("EGPT_LANDING_FIXTURES_ROOT")
    or REPO_ROOT / "product" / "egpt_retail" / "contracts" / "_fixtures" / "landing"
)


def load_ndjson(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def row_label(row: dict) -> str:
    for column in ("object_id", "session_id", "event_name"):
        if column in row:
            return f"{column}={row[column]!r}"
    return repr(row)


print("Landing contract -- C1 / D4")
print("=" * 78)

print()
print("1. Loading all three sources through the contract alone")
print("-" * 78)

files = sorted((FIXTURES_ROOT / "raw").rglob("*.ndjson"))
assert files, f"no fixture files found under {FIXTURES_ROOT / 'raw'}"

rows_by_source: dict[SourceName, int] = {source: 0 for source in SourceName}
rows_by_path: dict[Path, list[dict]] = {}
total_defects = 0

for path in files:
    rel = path.relative_to(FIXTURES_ROOT)
    parts = parse_landing_path(str(rel))
    rows = load_ndjson(path)
    rows_by_path[path] = rows
    print(f"  {rel}  ({parts.source.value}, dt={parts.dt}, {len(rows)} rows)")

    for row in rows:
        stream_id = resolve_stream_id(parts.source, row)
        defects = validate_row(stream_id, row)
        rows_by_source[parts.source] += 1
        if defects:
            total_defects += len(defects)
            for defect in defects:
                print(f"    ! {stream_id} {row_label(row)}: {defect}")

print()
for source in SourceName:
    print(f"  {source.value:15s} {rows_by_source[source]} rows loaded")

assert all(rows_by_source[source] for source in SourceName), (
    "every source must produce at least one row"
)
# Two distinct violations, deliberately: c_501's null brand (present, blank) and d_901's
# missing quote_request_source_url (absent entirely) exercise validate_row's two different
# "not filled in" branches. Asserted on exact count, not just printed, so a validation
# branch silently going dead (e.g. the "missing required column" check) fails this script
# rather than shipping unnoticed with identical-looking stdout.
assert total_defects == 2, f"expected exactly 2 row-level defects, got {total_defects}"
print()
print(
    f"Loader used only egpt_retail.contracts.landing -- no source-specific code -- "
    f"and surfaced {total_defects} row-level defect(s) (reported, not dropped)."
)

print()
print("2. Re-run behaviour: a re-pulled overlapping window lands a new file")
print("-" * 78)

crm_dir = FIXTURES_ROOT / "raw" / "crm" / "dt=2026-09-01"
first_path = crm_dir / "crm-20260901T020000Z.ndjson"
second_path = crm_dir / "crm-20260901T060000Z.ndjson"

# Already read once during discovery above -- reused rather than re-parsed off disk.
first_rows = rows_by_path[first_path]
second_rows = rows_by_path[second_path]
print(f"  first pull:  {len(first_rows)} rows ({first_path.name})")
print(
    f"  second pull: {len(second_rows)} rows ({second_path.name}), same dt= partition, "
    "first file untouched on disk"
)

# Lane R's dedup rule from LANDING_CONTRACT.md: group by key columns, last write wins by
# watermark. Not a framework function -- this is downstream of the contract, not part of it.
latest: dict[tuple, dict] = {}
for row in first_rows + second_rows:
    key = (row["record_type"], row["object_id"])
    watermark = row["last_modified_at"]
    if key not in latest or watermark > latest[key]["last_modified_at"]:
        latest[key] = row

raw_total = len(first_rows) + len(second_rows)
print(f"  raw rows across both files:                 {raw_total}")
print(f"  after last-write-wins dedup by (record_type, object_id): {len(latest)}")
assert len(latest) < raw_total, "dedup should have collapsed at least one duplicate"

c501_before = next(row for row in first_rows if row["object_id"] == "c_501")
c501_after = latest[("contact", "c_501")]
defects_before = validate_row("crm.contact", c501_before)
defects_after = validate_row("crm.contact", c501_after)
print(
    f"  c_501.brand before dedup: {c501_before['brand']!r}  (defects: {defects_before})"
)
print(
    f"  c_501.brand after dedup:  {c501_after['brand']!r}  (defects: {defects_after or 'none'})"
)
assert defects_before and not defects_after, (
    "the later pull should have resolved the earlier defect"
)

print()
print(
    "-- a genuine tie: h_9002 lands with the SAME watermark in both pulls, content changed --"
)
h9002_first = next(row for row in first_rows if row["object_id"] == "h_9002")
h9002_second = next(row for row in second_rows if row["object_id"] == "h_9002")
assert h9002_first["last_modified_at"] == h9002_second["last_modified_at"], (
    "fixture setup: this row exists to test an exact watermark tie"
)
assert h9002_first["stage"] != h9002_second["stage"], (
    "fixture setup: the tie must carry genuinely different content, or a loosened tie-break "
    "(`>=` instead of `>`) would pass unnoticed -- see review finding #9"
)
h9002_after = latest[("deal_stage_history", "h_9002")]
print(f"  first pull:  stage={h9002_first['stage']!r}  ({first_path.name})")
print(
    f"  second pull: stage={h9002_second['stage']!r}  ({second_path.name}), same watermark"
)
print(f"  kept after dedup: stage={h9002_after['stage']!r}")
# The dedup rule above is `>`, strictly greater -- so on an exact watermark tie, whichever
# row was folded into `latest` *first* stays, and `first_rows + second_rows` puts the first
# pull first. This is the intentional, if slightly non-obvious, tie-break this asserts on;
# a watermark tie in real data means the source system didn't actually timestamp the change,
# which is a data-quality question for Lane C, not something this contract can resolve for it.
assert h9002_after["stage"] == h9002_first["stage"], (
    "on an exact watermark tie, the first-processed pull must win -- deterministic, not "
    "the newer file, since neither watermark claims to be newer"
)

print()
print(
    "Evidence: raw is never mutated in place -- both files remain on disk -- and the "
    "dedup Lane R applies downstream resolves duplicates and stale values by watermark, "
    "exactly as the landing contract's re-run clause specifies."
)
