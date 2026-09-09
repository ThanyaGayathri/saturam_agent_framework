# Landing contract fixtures

Hand-written, not generated — this is what the C1 verify step calls for: "hand-write one
conforming sample file per source, upload it, and have Lane R load all three."

`raw/` mirrors the real landing root exactly, one file per source under its
`{source}/dt={YYYY-MM-DD}/` partition, named per
`egpt_retail.contracts.landing.landing_path`. `cookbook/94_retail/C01_landing_contract.py`
walks this directory the way a loader would walk object storage — nothing here is a special
case the script knows about in advance.

- `raw/analytics/dt=2026-09-01/analytics-20260901T020000Z.ndjson` — six events across two
  sessions. One session runs a homepage `page_view` (no `brand`/`product_id` — not
  product-scoped, so both are null rather than dirty) followed by the full RFQ funnel
  (`product_view`, `add_to_cart`, `form_start`, `submit`); the other has only a
  `product_view`, which is a complete, conforming file — a session need not contain events
  the client hasn't instrumented yet.
- `raw/crm/dt=2026-09-01/crm-20260901T020000Z.ndjson` — two contacts, two deals, two
  deal-stage transitions. Contact `c_501` lands with `brand: null`, the known-dirty case
  `LANDING_CONTRACT.md` names explicitly. Deal `d_901` lands with `quote_request_source_url`
  missing outright (as opposed to `c_501`'s present-but-null), exercising `validate_row`'s
  other "not filled in" branch. `h_9002` is a `deal_stage_history` row that recurs in the
  second pull below with the *same* watermark but different content — see that file.
- `raw/crm/dt=2026-09-01/crm-20260901T060000Z.ndjson` — a second, later pull of the *same*
  `dt=` partition: a byte-identical duplicate of `c_500` and `d_900`, a corrected `c_501`
  (brand now populated, watermark advanced), and `h_9002` again with the *same*
  `last_modified_at` as the first pull but a different `stage` — a genuine watermark tie
  with differing content, which pins the dedup's tie-break behaviour (first-processed pull
  wins) rather than letting a loosened `>=` comparison pass unnoticed. Demonstrates the
  re-run clause: raw never overwrites the first file, and the dedup rule (last write wins by
  watermark) collapses the duplicate, resolves `c_501`'s earlier defect, and settles the tie.
- `raw/session_replay/dt=2026-09-01/session_replay-20260901T020000Z.ndjson` — two sessions,
  one with no login identity and one with no submit identity, both legitimately null rather
  than dirty. Each row's `last_updated_at` is later than its `session_start_at` — the
  watermark (`last_updated_at`) moves independently of when the session began.

Every row across all three sources also carries `identity_key: null` — reserved on every
shape but not yet populated anywhere, since the shared hashed identity (`LANDING_CONTRACT.md`
> "Reserved, pending a cross-lane blocker") is gated on privacy sign-off, not on these
fixtures.

## When the real connectors land

C2–C4 write against this same path and shape from day one — that is the point of C1 existing
before any of them are built. These fixtures stay as the P0 verification corpus; they are not
meant to be replaced by real data the way `contracts/semantic.py`'s stub fixture is.
