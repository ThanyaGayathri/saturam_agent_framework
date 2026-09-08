# Landing contract and storage layout

*P0 deliverable. Written by Lane C; reviewed by Lane R. Implements D4.*

Where connectors write, what they write, and what a re-run does. All three connectors are
independent of everything else once this is agreed — Lane C builds them before a warehouse
exists, and Lane R builds the silver loader (R4) against sample files before a connector
runs.

---

## Storage path convention

```
raw/{source}/dt={YYYY-MM-DD}/{source}-{ingested_at}.ndjson
```

- `{source}` is one of `analytics`, `crm`, `session_replay`.
- `dt=` is a Hive-style date partition on the pull's target window (event date, last-modified
  date, or session-start date — see the per-source table below), so a loader can address a
  single day without listing the whole container. It matches ADLS Gen2's hierarchical
  namespace directly.
- `{ingested_at}` is the compact UTC timestamp of *when this file was written*
  (`%Y%m%dT%H%M%SZ`), not the data's own timestamp. It exists so two pulls of the same
  partition never collide on a filename — see **Re-run behaviour** below.

Example: `raw/crm/dt=2026-09-01/crm-20260901T060000Z.ndjson`.

`libs/agno` never sees this path or a source-specific concept — the typed half of this
contract, `egpt_retail.contracts.landing`, is what Lane R's loader (and later C2–C4) import
to build and parse it. See `landing_path` / `parse_landing_path`.

## File format

NDJSON: UTF-8, one JSON object per line, no envelope, no header row. Chosen over CSV because
analytics events carry named fields (`brand`, `product_id`) rather than a fixed positional
schema, and over Parquet because the verify step for this package is to *hand-write* one
conforming file per source — a text editor has to be enough.

## What one row represents, per source

Analytics and session replay each land one shape. **CRM lands three**, multiplexed into the
same file and distinguished by a `record_type` field — CRM's own resolution of "one row per
what": the source's *what* is a CRM change event, not a single flat grain. All three CRM
shapes are `1.0.0` of `egpt_retail.contracts.landing.LANDING_SCHEMAS` and share one
`dt=` partition and one file per pull.

| Stream | Grain | Key columns | Watermark | Required columns |
|---|---|---|---|---|
| `analytics.event` | one row per event, keyed on user, session, event name and when it occurred | `user_id`, `session_id`, `event_name`, `occurred_at` | `occurred_at` | + `brand`, `product_id` |
| `crm.contact` | one row per contact, as of its last change | `record_type`, `object_id` | `last_modified_at` | + `email`, `brand` |
| `crm.deal` | one row per deal, as of its last change | `record_type`, `object_id` | `last_modified_at` | + `stage`, `quote_request_source_url` |
| `crm.deal_stage_history` | one row per deal stage transition | `record_type`, `object_id` | `last_modified_at` | + `deal_id`, `stage`, `entered_at` |
| `session_replay.session` | one row per session | `session_id` | `session_start_at` | + `rage_click_count`, `dead_click_count`, `script_error_count`, `session_url`, `identity_at_login`, `identity_at_submit` |

Incremental pulls are by the watermark column: event date for analytics, last-modified for
CRM, session start for session replay — matching the connector hand-off table in
`docs/plan/build-plan.md`.

## Known-dirty and legitimately-nullable fields, named rather than silently handled

Two different things can make a required column empty, and the contract tells them apart
rather than treating both as noise:

- **Known-dirty, target is to fix at source.** CRM's `brand` is free text or missing today
  against an eventual enforced enum, and `quote_request_source_url` is inconsistently
  populated on quote-request deals. Both stay in `required_columns` — the contract's target
  shape — so a connector or loader that finds one null **reports it as a defect**, per the
  evidence bar in C3's own verify step ("a count of records failing each rule, reported
  rather than dropped"). It is not silently coerced to an empty string and it is not dropped
  from the file.
- **Legitimately sometimes unknown.** Session replay's `identity_at_login` and
  `identity_at_submit` are null when a session ends before that point happens — that is the
  truth, not dirty data. These are the only two columns in `LandingSchema.nullable_columns`.
- **Simply absent from a session.** Three RFQ funnel events (`add_to_cart`, `form_start`,
  `submit`) are not instrumented in the client's setup yet. A conforming analytics file does
  not need to contain them at all — there is no placeholder row, no null event. A session
  with only a `product_view` is a complete, conforming file. This is what lets C2's connector
  "survive their absence" rather than treat a short funnel as an error.

Key columns and the watermark column are never nullable, for either reason: they are
structural to the loader (partitioning, de-duplication), not a business fact about the
source system.

## Re-run behaviour

**Raw storage is immutable and append-only.** Landing a window twice — the same `dt=`
partition pulled again — writes a **new file**, named by `{ingested_at}`, never overwrites
the earlier one. This is the data platform's own description of Stage 0 ("untouched
connector output... replayable") and Stage 1 ("append-only, never transformed in place") made
literal at the storage layer: nothing downstream can lose a pull by racing a write, and every
pull stays inspectable on its own.

**De-duplication is downstream, not raw storage's job.** Two files under the same partition
are expected to contain overlapping or identical rows. The rule Lane R's loader (R4) applies:
group by key columns, keep the row with the greatest watermark value ("last write wins"). A
byte-identical duplicate row collapses to one under this rule for free — its watermark ties,
and either copy is correct. This is also what makes C2's connector verify step true at the
warehouse: pulling the same window twice must add no duplicate rows, and pulling an
overlapping window must add only the new rows, once the standard dedup pass runs.

## Scope

This contract governs **Stage 0 Raw only** — the file a connector writes, and nothing past
it. Stage 1 Landing (the warehouse schema loaded as-is with ingest metadata) and Stage 2
Silver (typed, deduplicated, conformed entities, with the quality gates G8 calls for) are
Lane R's R4, which consumes this contract but is not part of it.

---

## Verification

`cookbook/94_retail/C01_landing_contract.py` plays Lane R's loader against one hand-written,
conforming NDJSON file per source under
`product/egpt_retail/contracts/_fixtures/landing/`. It imports nothing but
`egpt_retail.contracts.landing` and the standard library: no source-specific logic exists
outside this contract, which is what makes "Lane R loads all three without asking a question"
checkable rather than asserted. A second half simulates a re-pulled, overlapping CRM window
and runs the last-write-wins dedup rule above, showing a known-dirty row (`c_501`'s null
`brand`) resolved by the later pull, and a byte-identical duplicate collapsed to one row.
