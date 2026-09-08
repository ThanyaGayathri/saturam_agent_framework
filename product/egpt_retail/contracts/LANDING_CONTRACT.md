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

**Known gap in 1.0.0: no dataset segment.** This path gives one file per source per pull.
CRM works around that by multiplexing three shapes into one file via `record_type`, which is
the pattern any future database source (a fourth connector, or CRM growing past three
shapes) would have to inherit, and it does not generalise past a handful of shapes. Adding a
`{dataset}` segment (`raw/{source}/{dataset}/dt=.../{dataset}-{ts}.ndjson`) is a breaking
change — every landed path moves — so it is deliberately not made now, while nothing has
landed against this convention yet. Recorded here as a known limitation, not a decision, so a
future database-backed connector does not have to rediscover it.

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
| `analytics.event` | one row per event, keyed on user, session, event name and when it occurred | `user_id`, `session_id`, `event_name`, `occurred_at` | `occurred_at` | + `brand`\*, `product_id`\*, `identity_key`\* |
| `crm.contact` | one row per contact, as of its last change | `record_type`, `object_id` | `last_modified_at` | + `email`, `brand`, `identity_key`\* |
| `crm.deal` | one row per deal, as of its last change | `record_type`, `object_id` | `last_modified_at` | + `stage`, `brand`, `quote_request_source_url`\* |
| `crm.deal_stage_history` | one row per deal stage transition | `record_type`, `object_id` | `last_modified_at` | + `deal_id`, `stage`, `entered_at` |
| `session_replay.session` | one row per session | `session_id` | `session_start_at` | + `rage_click_count`, `dead_click_count`, `script_error_count`, `session_url`, `identity_at_login`\*, `identity_at_submit`\*, `identity_key`\* |

\* nullable — present on every row, but a `null` value is not a reported defect. See below.

`analytics.event` and `session_replay.session` both have a `session_id` key column, from two
different tools. They are different namespaces, not the same key: until `identity_key` is
populated, nothing joins a row in one to a row in the other, and a reader must not assume a
matching `session_id` means the same browsing session.

Incremental pulls are by the watermark column: event date for analytics, last-modified for
CRM, session start for session replay — matching the connector hand-off table in
`docs/plan/build-plan.md`.

Every watermark value must be UTC, with the literal `Z` suffix — never naive, never a
non-UTC offset. The re-run rule below compares two watermark values lexically, not by
parsing them, and lexical order only equals chronological order once every value shares the
same fixed-width UTC suffix (the same rule `landing_path`'s `ingested_at` already enforces).

## Known-dirty and legitimately-nullable fields, named rather than silently handled

Two different things can make a required column empty, and the contract tells them apart
rather than treating both as noise:

- **Known-dirty, target is to fix at source.** CRM's `brand` is free text or missing today
  against an eventual enforced enum, and `quote_request_source_url` is inconsistently
  populated on quote-request deals. Both stay in `required_columns` — the contract's target
  shape — so a connector or loader that finds one null **reports it as a defect**, per the
  evidence bar in C3's own verify step ("a count of records failing each rule, reported
  rather than dropped"). It is not silently coerced to an empty string and it is not dropped
  from the file. `brand` is required on **both** `crm.contact` and `crm.deal`, and they are
  not interchangeable: `crm.deal`'s `brand` is authoritative for RFQ-funnel attribution,
  because C3's own verify step queries "deals with empty or free-text brand", and because a
  single contact can request quotes across more than one brand — a fact `crm.contact`'s
  single `brand` column cannot hold. `crm.contact`'s `brand` reflects the contact's own CRM
  record and is known-dirty in the same way, but a reader joining brand into the RFQ funnel
  must use the deal's, not the contact's.
- **Legitimately sometimes unknown.** Session replay's `identity_at_login` and
  `identity_at_submit` are null when a session ends before that point happens — that is the
  truth, not dirty data. `crm.deal`'s `quote_request_source_url` is null on a deal that is
  not a quote request (a renewal, an upsell) — required only when `stage` marks the deal as
  one, a per-row rule R4 enforces; this contract does not encode conditional requirement.
  `analytics.event`'s `brand` and `product_id` are null on an event that is not
  product-scoped (a homepage `page_view`, a `search`) — required only for the RFQ funnel
  events named below.
- **Reserved, pending a cross-lane blocker.** `identity_key` is a required column on
  `analytics.event`, `crm.contact` and `session_replay.session` — one per connector — so
  C2, C3 and C4 do not each invent their own identity column and R4 has one join column to
  wait for. It is null on every row in 1.0.0: the plan's single non-engineering blocker is
  "a single shared identity key, a hash of email, written into every system", gated on
  privacy sign-off rather than build effort, and that sign-off has not happened. Until it
  does, **raw email continues to land in the clear** — `crm.contact.email` and session
  replay's `identity_at_login`/`identity_at_submit` are unhashed. This is a known privacy
  gap in 1.0.0, not a decision: it must be resolved (either the identity key replaces these
  fields, or they are hashed at the connector) before the identity key work in the plan's
  P1 lands, and the storage account backing this contract needs the same handling as the
  CRM source itself until then.
- **Simply absent from a session.** Three RFQ funnel events (`add_to_cart`, `form_start`,
  `submit`) are not instrumented in the client's setup yet. A conforming analytics file does
  not need to contain them at all — there is no placeholder row, no null event. A session
  with only a `product_view` is a complete, conforming file. This is what lets C2's connector
  "survive their absence" rather than treat a short funnel as an error.

Key columns and the watermark column are never nullable, for either reason: they are
structural to the loader (partitioning, de-duplication), not a business fact about the
source system.

## Known gaps in 1.0.0

Recorded so a future lane reads them as gaps, not silence implying a decision:

- **No dataset segment in the storage path** — see the path convention section above.
- **No deletion or merge semantics.** Last-write-wins can add and replace a row, never
  remove one. CRM contacts get merged and deals get deleted at the source; a row that
  disappears there never produces a landed row here, so a loader has no signal that it is
  gone. Not something this contract is asked to solve in 1.0.0 — a future `_op: delete` or
  tombstone convention is the likely shape — but recorded here so R4 does not have to
  discover the gap by noticing a warehouse that only ever grows.

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

**On an exact watermark tie with genuinely different content**, the rule as stated ("greatest
watermark wins") doesn't pick a side — a strict "greater than" comparison keeps whichever row
a loader folds in first. That is a real, if rare, case: it means the source system did not
actually timestamp the change that produced the second row, which is a data-quality gap in
the source, not something this contract can resolve on the loader's behalf. Lane R's
reference dedup (demonstrated in `C01_landing_contract.py`) processes files in the order they
are discovered — oldest `ingested_at` first — so the earliest-pulled version of a tied row
wins, deterministically.

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
