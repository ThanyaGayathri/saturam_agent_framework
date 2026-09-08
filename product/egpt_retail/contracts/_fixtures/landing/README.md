# Landing contract fixtures

Hand-written, not generated — this is what the C1 verify step calls for: "hand-write one
conforming sample file per source, upload it, and have Lane R load all three."

`raw/` mirrors the real landing root exactly, one file per source under its
`{source}/dt={YYYY-MM-DD}/` partition, named per
`egpt_retail.contracts.landing.landing_path`. `cookbook/94_retail/C01_landing_contract.py`
walks this directory the way a loader would walk object storage — nothing here is a special
case the script knows about in advance.

- `raw/analytics/dt=2026-09-01/analytics-20260901T020000Z.ndjson` — five events across two
  sessions. One session runs the full RFQ funnel (`add_to_cart`, `form_start`, `submit`); the
  other has only a `product_view`, which is a complete, conforming file — a session need not
  contain events the client hasn't instrumented yet.
- `raw/crm/dt=2026-09-01/crm-20260901T020000Z.ndjson` — one contact, one deal, one deal-stage
  transition. Contact `c_501` lands with `brand: null`, the known-dirty case
  `LANDING_CONTRACT.md` names explicitly.
- `raw/crm/dt=2026-09-01/crm-20260901T060000Z.ndjson` — a second, later pull of the *same*
  `dt=` partition: a byte-identical duplicate of `c_500` and `d_900`, and a corrected `c_501`
  (brand now populated, watermark advanced). Demonstrates the re-run clause: raw never
  overwrites the first file, and the dedup rule (last write wins by watermark) both collapses
  the duplicate and resolves the earlier defect.
- `raw/session_replay/dt=2026-09-01/session_replay-20260901T020000Z.ndjson` — two sessions,
  one with no login identity and one with no submit identity, both legitimately null rather
  than dirty.

## When the real connectors land

C2–C4 write against this same path and shape from day one — that is the point of C1 existing
before any of them are built. These fixtures stay as the P0 verification corpus; they are not
meant to be replaced by real data the way `contracts/semantic.py`'s stub fixture is.
