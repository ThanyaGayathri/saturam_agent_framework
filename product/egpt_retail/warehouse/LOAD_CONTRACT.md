# Load contract — landing to silver

*R4. Owned by Lane R. The counterpart to `contracts/LANDING_CONTRACT.md`, which is Lane C's
and governs Stage 0 Raw only.*

Three stages. Lane C owns the first; R4 owns the second and third.

| Stage | Where | What it holds |
|---|---|---|
| 0 Raw | Object storage | Immutable NDJSON, written by the connectors. `LANDING_CONTRACT.md` |
| 1 Landing | `landing.raw_row` | Those rows as-is, unparsed, plus ingest metadata |
| 2 Silver | `silver.*` | Five typed, deduplicated, conformed entities |

Gold is R5's, and R4 stops one step short of it: a load ends in a verdict, and a blocked
verdict returns `stop=True`, so a gold step appended after it never runs.

---

## The transforms are files

Every rule a row is subject to between landing and silver is in
`warehouse/sql/conform/<entity>.sql` — one file per entity, one statement each, keys and
de-duplication and type coercion all visible. That is D6 ("transformation logic executed as
SQL inside the warehouse") and the standing risk-table rule that transforms stay inspectable
so they can be handed over later. `warehouse/conform.py` runs them and counts rows; it
decides nothing.

They are bound with `:name` parameters, which is also psql's client-side variable syntax, so
the same file runs by hand:

```bash
psql "$EGPT_RETAIL_WAREHOUSE_DB_URL" \
  -v load_id="'3f2c8e14-0000-0000-0000-000000000000'" \
  -f product/egpt_retail/warehouse/sql/conform/crm_contact.sql
```

Two consequences, both enforced by `product/tests/test_r4_sql_assets.py`: a parameterised
file holds exactly one statement (psycopg refuses several when parameters are supplied), and
casts are written `CAST(x AS type)` rather than `::`, which would collide with `:name`.

**No DuckDB, pandas, polars or pyarrow — deliberately.** Landing holds rows as-is, so nothing
is parsed there and a JSON-inferring reader buys nothing; inference would move type coercion
out of the readable SQL and would silently coerce exactly the known-dirty values the landing
contract requires be *reported*; and a second SQL dialect would repeat the SQLite-versus-
Postgres damage already recorded in the KPI contract's changelog. Revisit for R5 if analytics
volumes make a columnar reader genuinely necessary.

---

## The rules R4 implements

**De-duplication.** Group by the stream's `key_columns`, keep the greatest
`watermark_column`; on an exact tie take the earliest-pulled file, and within one file the
first line. `ORDER BY <watermark> DESC, ingested_at ASC, line_number ASC`. This is the landing
contract's rule, and it produces the same answers `C01_landing_contract.py` asserts in Python.

**Late-arriving rows.** `ON CONFLICT ... DO UPDATE ... WHERE EXCLUDED.<watermark> >
s.<watermark>` promotes the same strictly-greater rule from within one run to across runs. A
later load carrying an older or equal row updates nothing.

**Idempotence.** `landing.raw_row` keys on `(source_path, line_number)`, and the conformance
pass is a full recompute over all of landing. So re-loading the same files inserts nothing
and re-conforming rewrites nothing — not the counts, not the values, not the `conformed_at`
stamps.

**Nullability.** Key and watermark columns are `NOT NULL` in silver; a row missing one cannot
be identified and is excluded, with a structural defect recorded. Every other required column
is nullable, because the contract says a missing business value is reported and the row kept
— and a `NOT NULL` there would abort the statement on one dirty row, dropping the whole load
rather than the row.

**Defects.** Detected by calling the landing contract's own `validate_row`, not by restating
its rules in SQL. Two definitions of "conforming" that must agree would eventually not.

**Type coercion is split between the contract and R4, deliberately.** Contract 1.1.0 added
`timestamp_columns`, so every timestamp R4 casts — the watermark and any other declared one — is
format-checked by `validate_row` and reported before it reaches conformance. Numbers are not:
nothing in the landing contract says `rage_click_count` is a number, because nothing in the
landing contract knows R4 stores it as one.

So `sql/defects/uncoercible.sql` covers **numeric columns only**, and does it by calling the
same `silver.try_*` functions the conformance statements call — "will not coerce" then means
exactly what it means there, rather than being a second definition that has to agree.
`schema.py::COERCED_COLUMNS` derives its timestamps from the contract and declares only the
integers, and a test binds the whole thing to what each conform statement actually wraps.

---

## Per-stream notes

**`analytics.event`'s watermark is inside its key.** Rows sharing the key necessarily share
`occurred_at`, so there is no greater watermark to prefer: de-duplication degenerates to
duplicate collapse and the conflict clause is `DO NOTHING`. Correct — an event is immutable
once it has happened.

**`session_replay.session`'s watermark moved, and the reason is worth keeping.** It used to be
`session_start_at`, which never changes for a session — so a re-pull carrying corrected click
counts tied on the watermark and the earliest pull won, discarding the correction. R4 raised
that as a landing-contract defect rather than working around it, because special-casing one
stream to order by `ingested_at DESC` would have given the five entities silently different
de-duplication semantics. Contract 1.1.0 added `last_updated_at` and records `session_start_at`
as the superseded watermark; the correction now wins.

**No analytics-to-replay join is checked.** `analytics_event.session_id` and
`session_replay_session.session_id` are two vendors' id spaces (`s_2001` versus `sess_300`)
with no bridge, so checking that edge would report a 100% breach against perfectly conforming
data. Raised as a Lane C finding; the shared identity key that would fix it is gated on
privacy sign-off, and R10's friction join needs it.

---

## Concurrency

**Loads are serialised by a Postgres advisory lock** (`warehouse/loads.py::LoadLock`), held on
a dedicated connection for the length of a run and released when it ends — or automatically, by
the server, if the process dies. A second load finding the lock held refuses and says so.

The risk is not corruption: every write is an upsert and landing is keyed on
`(source_path, line_number)`, so two overlapping loads cannot damage each other's rows. It is
the *baseline*. Each load calls `previous_successful_load` independently, so whichever finished
second would have computed its comparison before the first recorded a verdict — both gates
measuring against a state that had already moved, and nothing in the output saying two loads
overlapped. A silently wrong gate is worse than a visible conflict.

Same guard and same shape as `entrypoints/consolidator.py`, for the same reason. The connection
is opened straight to Postgres: through a transaction-mode pooler the session would end with the
first transaction and the lock would quietly evaporate.

## When a load does not finish

A load is recorded as started before a single file is read, so a crash always leaves evidence.
Three mechanisms cover the ways one can end badly:

- **A step raises** → the load is closed as `'failed'` with the exception in its reason, and the
  exception still propagates so the workflow stops rather than carrying on toward gold.
- **The process dies** (OOM, eviction) → no handler runs, so the *next* load closes it, via
  `close_stale_load_runs`. Without this, `'failed'` would be a verdict the schema permits and
  nothing ever writes, and a dead load would read as "still running" for ever.
- **Its rows are still in landing** → conformance is a full recompute, so rows landed by a load
  that never passed are folded into the next load's silver, attributed to a load whose gates
  never saw them. That is a real hole — a load's gates can be bypassed for rows it landed — so
  the next load counts them (`ungated_rows`) and **refuses** until someone looks. Re-run once the
  earlier problem is fixed, or accept them deliberately.

`quality.latest_load` is deliberately **not** filtered to finished loads: a run still in flight
and a run that died are exactly what a health screen needs to show, and filtering hid both.

## The gates

Three measurements, one decision. Each measurement is a `.sql` file returning counts and rates
with **no threshold in it**, so an analyst can run it in psql without knowing the policy; the
comparison lives in `warehouse/quality.py`, because "anything that permits or blocks is an
executor, never a model".

| Gate | Blocks when | Default |
|---|---|---|
| `check_row_count_delta` | landed rows fall below a fraction of the last **passed** load, silver shrinks at all, or the load read nothing whatsoever | `min_landed_ratio` 0.5 |
| `check_key_null_rates` | a key or watermark column is missing, blank or uncoercible | `max_key_null_rate` 0.0 |
| `check_referential_integrity` | a child row names a parent that does not exist | `max_orphan_rate` 0.0 |

None of them stops. `decide_load_verdict` aggregates and stops, so the one thing that says no
is in one reviewable place — and an operator with two problems on one night learns about both,
instead of fixing them one at a time over two nights.

**Scoping is per stream, not per source, and that distinction is load-bearing.** CRM
multiplexes contact, deal and deal_stage_history into one file, so an incremental window in
which only contacts changed is an ordinary pull — not two streams that went missing. Scoping by
source reported exactly that, on every targeted load. A stream that carried no rows is recorded
as "not in this pull" and passes.

Coverage is `landed_rows > 0` — what the files the load *read* carried — and never which rows
carry this `load_id` in landing. An idempotent re-load inserts nothing, so the latter test would
call a complete re-pull an empty one.

Per-stream scoping alone would then let a wholly empty pull through, since every stream sits out
legitimately; so a load that read **nothing at all** while a previous load delivered is refused
against the load itself. That is the one input this gate exists to catch.

The residual, stated: a single stream that silently stops delivering while its siblings keep
going is **not** caught here, because it is indistinguishable from a quiet window. That is
monitoring's job (R14, "runs that stopped firing"), not a gate's — a gate that fired on every
ordinary pull would be turned off within a week.

The baseline is the last **passed** load, never merely the last: two bad nights in a row must
not let small become the new normal.

**Two acknowledgement flags, not one.** `acknowledge_shrink` waives the silver-shrink check;
`acknowledge_undersized` waives the truncated-pull check. They were a single flag, which meant
acknowledging a backfill-to-delta transition also switched off the one check whose entire job is
catching an incomplete pull. Both are written into the verdict reason, so using either leaves a
trace rather than being a silent bypass.

Thresholds are constants in `quality.py`, overridable per run through the workflow's
`additional_data`. Not environment variables: fifteen of them that nobody sets is worse than a
constant. A `quality.gate_threshold` table for editing without a deploy is the P3 answer.
`acknowledge_shrink` exists for the legitimate backfill-to-delta transition and is written
into the verdict reason, so using it leaves a trace rather than being a silent bypass.

---

## Running it

The pipeline is a registered workflow, `landing-to-silver`, so it inherits per-step retries
and run history, and R5 gets a place to append gold.

**It must be driven with `arun`.** Its executors are `async def` (the executor registry's
exemption for I/O), and a synchronous `Workflow.run()` raises inside `Step`'s retry loop,
which `on_error` then handles — and `on_error` defaults to `OnError.skip`, whatever the
comment above that default says. A sync run would skip every step and report a completed run
that loaded nothing. Every step also sets `on_error=OnError.fail`, so an unexpected exception
stops the chain instead of carrying on toward gold.

```python
asyncio.run(workflow.arun(input="load", additional_data={"trigger": "manual"}))
```

Over HTTP: `POST /workflows/landing-to-silver/runs`.

**R4 does not schedule it.** R5 owns the nightly run. The call it will make, written down and
not executed:

```python
ScheduleManager(db).create(
    name="landing-to-silver-nightly",
    cron="0 1 * * *",
    endpoint="/workflows/landing-to-silver/runs",
    payload={"additional_data": {"trigger": "schedule"}},
    timezone="UTC",
)
```

Note that `ScheduleManager.trigger()` is a no-op that logs a warning; hand-triggering a
schedule is `POST /schedules/{id}/trigger`.

---

## Schema changes

`warehouse-migrate` applies `CREATE ... IF NOT EXISTS` and `CREATE OR REPLACE`, and has no
version ledger. `MigrationManager` is deliberately not extended: it covers the framework's
control-plane tables, the warehouse holds none of them, and tracker #178 hardened it against
stamping tables it never migrated.

**The cost of that, found the hard way while building this:** idempotent DDL cannot *evolve* a
table. Adding a column to `quality.load_stream_count` did nothing on a database where the
table already existed, and the failure surfaced as an `UndefinedColumn` error mid-load. Until
R5 needs a versioned migrator, the procedure for a schema change is to drop the three schemas
and re-apply — which is acceptable precisely because nothing outside R4 depends on them yet,
and stops being acceptable the day gold does.

---

## Verification

| Script | Proves |
|---|---|
| `cookbook/94_retail/R04_landing_to_silver.py` | The four scenarios in the package's Verify line, against a real warehouse |
| `cookbook/94_retail/R04_conformance_sql.py` | The conformance specification per entity, readable with no database |
| `cookbook/94_retail/R04_minio_load.sh` | The same four scenarios loading from a real S3 bucket |
