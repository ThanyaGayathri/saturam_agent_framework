# Contract changelog

One place per contract, appended to, with the date, the reason and who agreed. Rule 5 of
the change protocol in `docs/plan/delivery-plan.md`.

## Landing contract and storage layout

### 1.0.0 — 2026-09-08 — initial, verified against fixture files

Agreed by: Lane C. **Not yet reviewed by Lane R** — this contract is `C → R`, so it is a
proposal until Lane R has read it.

The shape as the delivery plan asks for it: storage path convention with a date partition,
file format, what one row represents, key columns, the watermark column, and the behaviour
when a window is pulled twice — all per source, for analytics, CRM and session replay. Full
text in `LANDING_CONTRACT.md`; the typed half is `egpt_retail.contracts.landing`.

Two decisions made in the writing, not handed down by the plan:

| Decision | Why |
|---|---|
| **NDJSON, not CSV or Parquet** | The verify step is to hand-write one conforming file per source. Parquet fails that outright; CSV fights analytics' named, optional params. |
| **Raw is immutable; re-run writes a new file, never overwrites** | The data platform section already calls Stage 0 "untouched... replayable" and Stage 1 "append-only" — this is that made literal at the storage layer. De-duplication (last write wins by watermark) is downstream, in R4, not in this contract. |

**CRM lands three record shapes in one file**, distinguished by `record_type` — the source's
"one row per what" doesn't reduce to a single grain, so this is a documented clause rather
than an implicit assumption a loader would have to discover.

**Known-dirty fields stay required, not optional.** CRM's `brand` and
`quote_request_source_url` are inconsistent at the source today; they are not moved out of
`required_columns` to accommodate that. A null value is a reported defect, matching C3's own
evidence bar ("a count of records failing each rule, reported rather than dropped"), not a
silently accepted gap in the contract.

Verified by `cookbook/94_retail/C01_landing_contract.py` against three hand-written fixture
files (one per source) plus a second, overlapping CRM pull: loads all three sources with zero
source-specific code, surfaces the one deliberately-dirty row instead of dropping it, and the
dedup rule collapses a duplicate and resolves that row's earlier defect from the later pull.

### Patch — 2026-09-08 — correctness fixes from automated review, round 1

Not a shape change; no field or public signature moved, so `CONTRACT_VERSION` stays `1.0.0`.
Thirteen findings (3 Major, 9 Minor, 1 Nit) from automated review on PR #190, all reproduced
against real code before being fixed. Full list in `TEST_LOG.md`'s "Review round 1" entry;
the three that were behavioral rather than test-coverage gaps:

| Fix | Was |
|---|---|
| `landing_path()` refuses a timezone-naive `ingested_at` | `astimezone(timezone.utc)` on a naive value silently assumed the process's *local* timezone rather than UTC — the common `datetime.utcnow()` idiom would corrupt the embedded timestamp on any non-UTC host, with no error |
| `validate_row` flags a non-scalar key column | A list or dict in a key column passed with zero defects, then crashed the caller's own last-write-wins dedup with an unhandled `TypeError` on grouping by key |
| `validate_row` and `resolve_stream_id` both check `isinstance(row, Mapping)` first | NDJSON permits a bare string, list or number per line; both functions previously assumed an object and raised an unhandled `AttributeError`/`TypeError` instead of a reported defect (`validate_row`) or a clean, intentional `ValueError` (`resolve_stream_id`) |

Also fixed: a key+watermark column (`analytics.event.occurred_at`) double-reporting one null
value as two defects; `LANDING_SCHEMAS` was documented as frozen but was a mutable `dict`
(now `MappingProxyType`); only literal `null`, not an empty string, tripped the known-dirty
report; `parse_landing_path` leaked a raw stdlib message for a calendar-invalid date instead
of its own consistent wrapper.

Added `product/tests/test_landing_contract.py` (30 tests) — `LandingSchema.__post_init__`'s
six construction-time guards were correct but unexercised anywhere in the repository before
this; adversarial `parse_landing_path` inputs (traversal, mismatched source, calendar-invalid
date, wrong extension) likewise. The cookbook fixtures gained two rows (`d_901`, missing
`quote_request_source_url` outright; `h_9002`, a genuine same-watermark tie with different
content across the two CRM pulls) so `C01_landing_contract.py` exercises and asserts on
branches its previous fixtures happened not to touch. `C01_minio_roundtrip.sh` now diffs a
sha256 manifest of uploaded vs. downloaded files, rather than relying on object names alone.

### Patch — 2026-09-08 — round-2 review: correctness fixes, coverage, and contract-vs-plan gaps

`CONTRACT_VERSION` stays `1.0.0` here — not because adding `brand` and `identity_key` as
required columns is non-breaking under `validate_row`'s own semantics (a row missing a
required column entirely is flagged regardless of nullability, so it is breaking by that
rule), but because this contract is still `C → R`, an unreviewed proposal: a correction to a
proposal is announced under change-protocol rule 1 rather than version-bumped as a change to
an agreed shape. That distinction never mattered in practice — these commits never reached
`main` under a released `1.0.0` label (PR #190 closed before round 2 was pushed); this PR
folds rounds 2-4 into a single correct `1.0.0 → 1.1.0` transition below.

Two findings from a deeper pass that checked the contract against
`docs/plan/delivery-plan.md`/`build-plan.md` directly, not just against itself:

| Fix | Was |
|---|---|
| `brand` is a required column on `crm.deal`, not only `crm.contact` | C3's own verify step reads "deals with empty or free-text brand", but `crm.deal` had no `brand` column at all — that verification could not be run against this contract as written. `crm.deal`'s `brand` is now authoritative for RFQ-funnel attribution, since a single contact can request quotes across more than one brand. |
| Watermark timestamps must be UTC with a literal `Z` suffix | `validate_row` accepted a naive or non-UTC-offset timestamp. The dedup rule compares watermarks lexically, and `"09:00:00+02:00" > "07:30:00Z"` is true as strings but false as instants — the same class of bug round 1 fixed for `ingested_at` in `landing_path`, now closed for the row-level watermark too. |

Also fixed, all Minor/Nit, reproduced before being fixed: the key-column type check now
requires a string (a `bool`/`int`/`float` key value previously passed clean, contradicting
the code's own comment); the key+watermark double-report guard now also covers a non-scalar
value, not just a blank one; `test_validate_row_rejects_non_scalar_key_value` now covers
`dict` alongside `list`; the "missing required column" and `nullable_columns` runtime-
suppression branches each gained a dedicated unit test instead of only cookbook coverage.

Three gaps recorded rather than fixed, per each finding's own "not a blocker, document it"
recommendation:

- **No dataset segment in the storage path** (medium) — breaking, and nothing has landed
  against this convention yet, so redesigning it now is premature. Recorded as a known 1.0.0
  limitation in `LANDING_CONTRACT.md`.
- **`analytics.event`'s `brand`/`product_id` are now nullable**, required only for the named
  RFQ-funnel events — they were non-nullable, which made a homepage `page_view` a false
  defect.
- **`identity_key` reserved, null everywhere.** Added as a required-but-nullable column to
  `analytics.event`, `crm.contact` and `session_replay.session` — one per connector — so
  C2–C4 do not each invent their own identity column. It stays null until the plan's shared
  hashed identity ships (gated on privacy sign-off, per `build-plan.md`). Also recorded
  explicitly: raw email continues to land in the clear in 1.0.0, a known privacy gap, not a
  decision. `crm.deal`'s `quote_request_source_url` is now nullable too, since a deal that
  is not a quote request has no referring URL — the conditional requirement is a per-row
  rule for R4, not something this contract's `validate_row` encodes.
- **No deletion or merge semantics** (low) — last-write-wins can add and replace a row,
  never remove one. Recorded as a known 1.0.0 gap in `LANDING_CONTRACT.md`.

Fixture and doc updates to match: `identity_key: null` added to every row across all three
sources; `brand` added to the CRM deal fixture rows; a homepage `page_view` (null
`brand`/`product_id`) added to the analytics fixture to demonstrate the nullable fix;
`deploy/compose.yaml`'s MinIO image pinned to a release tag instead of `:latest`; the
fixtures README brought back in sync with the fixture files (round 1 had left it describing
an earlier, smaller version of the CRM fixtures).

`product/tests/test_landing_contract.py` grew to 40 tests. Full list in `TEST_LOG.md`'s
"Review round 2" entry.

### Patch — 2026-09-08 — round-3 review: a real completeness gap round 2 introduced

`CONTRACT_VERSION` stays `1.0.0` here for the same reason as round 2 above: tightening
`validate_row` to flag a non-scalar value in a required column can fail a row that previously
passed, which is breaking by `validate_row`'s own rule, not exempt from it — but this contract
is still an unreviewed `C → R` proposal, and (as above) no released `1.0.0` label was ever
live against it. One real finding from a review of round 2's own fix, plus three
doc/robustness nits reproduced and closed alongside it.

| Fix | Was |
|---|---|
| `validate_row` flags a non-scalar value in a non-key required column | Round 2's `_is_blank` check covered "missing" and "blank" but not "wrong shape" the way the key-column loop does. A list or dict in, e.g., `brand` was neither missing nor blank, so it passed `validate_row` clean -- the same completeness gap the key-column fix closed for keys, left open for every other required column. |

Also fixed: `LandingSchema.__post_init__`'s guard count corrected from "four" to "six" (the
method has six `raise` sites; the round-1 test file already added six matching tests, the
prose just undercounted) in both `TEST_LOG.md` and this file; `C01_minio_roundtrip.sh`'s
manifest loop is now NUL-delimited (`find -print0` / `sort -z` / `read -d ''`), a
robustness fix with no behavioral difference against today's fixed fixture filenames; the
adversarial `uppercase source` test case now notes it pins the `SourceName` enum layer, not
`_PATH_RE`'s own case restriction, since the regex already excludes it.

One finding investigated and not reproduced: a claimed double-report for a non-scalar value
on a combined key+watermark column. `f6580c34`'s `watermark_already_reported` already
covers this (`_is_blank(watermark) or not isinstance(watermark, str)`); reproducing the
exact input against that commit returns one defect, not two.

`product/tests/test_landing_contract.py` grew to 41 tests.

### 1.1.0 — 2026-09-08 — session_replay's watermark, and a follow-up PR

Agreed by: Lane R — this is Lane R's own finding, from building R4 against this contract, and
R4 (#193) is the consumer whose dedup `ORDER BY` this change reaches.

PR #190 merged at the round-1 commit before the round-2 and round-3 fixes above were pushed
to it — both rounds are real (reproduced, tested, pushed), but landed in `main` through this
follow-up PR instead, once the original was already closed. Recorded here rather than
silently folded into the entries above, since those entries' commit SHAs no longer match
what merged them.

One more finding arrived after the merge, from Lane R having built R4 against this contract
(#193, not yet merged): **breaking**, hence the version bump.

| Change | Why | Cost to Lane R |
|---|---|---|
| `session_replay.session`'s watermark is `last_updated_at`, not `session_start_at` | `session_start_at` never moves once a session begins. A re-pull carrying corrected click counts for the same session would tie on it forever, and the tie-break (earliest-pulled wins) would silently discard every correction — the contract's own re-run rule producing a real data-loss bug, not a loader bug. `last_updated_at` moves when the record does, the same property `last_modified_at` already gives the three CRM streams. | `session_start_at` stays a required column, just no longer structural — R4's existing SQL keeps reading it, only the dedup's `ORDER BY`/comparison column changes. |

Also fixed, additive: `product/pyproject.toml`'s `package-data` only listed
`contracts/_fixtures/*.json`, which does not match
`contracts/_fixtures/landing/raw/**/*.ndjson` — a non-editable install of `egpt-retail`
shipped the landing contract without the fixtures that demonstrate it, invisible in the
editable checkout where all development happens. Added
`contracts/_fixtures/landing/raw/*/*/*.ndjson`, and a test
(`test_landing_fixtures_are_covered_by_package_data_glob`) that fails if a future fixture
file isn't covered by some glob, mirroring the shape of R4's own `test_r4_sql_assets.py`
coverage check.

`product/tests/test_landing_contract.py` grew to 44 tests.

### Patch — 2026-09-09 — round-4 review: format-checking gap, version-compat gap, coverage

Not a shape change; `CONTRACT_VERSION` stays `1.1.0`. Two real gaps in the 1.1.0 change
itself, plus documentation and coverage fixes, from an automated review of this follow-up PR.

| Fix | Was |
|---|---|
| `LandingSchema.timestamp_columns` restores format-checking for a required column that stops being the watermark | `_parses_as_timestamp` only ever ran on `schema.watermark_column`. `session_start_at` was format-checked before 1.1.0 (it was the watermark) and silently stopped being checked after — a garbage string in it passed `validate_row` clean. `crm.deal_stage_history.entered_at` had the same latent gap, never having been the watermark at all. Both are now in `timestamp_columns`. |
| `LandingSchema.superseded_watermark_column` distinguishes a pre-1.1.0 landed row from malformed 1.1.0 data | A row landed under the pre-1.1.0 shape (has `session_start_at`, genuinely lacks `last_updated_at`) reported the identical generic "watermark missing" defect as truly corrupt data — re-validating PR #190's already-landed rows post-merge would have flagged every one of them. `session_replay.session` now names `session_start_at` as its superseded watermark; a blank current watermark alongside a well-formed old one gets a distinct "may predate the switch" message. |

Also fixed: the round-2 and round-3 entries above reworded to stop claiming a version-exempt
"proposal" carve-out that `validate_row` itself does not implement (see each entry); this
entry's own "Agreed by" line added to the `1.1.0` entry above, per this file's Rule 5; the
headline `session_replay` test renamed and its docstring narrowed from claiming a dedup
demonstration it never performed (`validate_row` calls and a string comparison, not any
winner-selection code) to what it actually pins — real dedup coverage is Lane R's R4 (#193),
the actual consumer of `schema.watermark_column`. Coverage gaps closed: `crm.deal.brand`
rejecting an explicit `null` (previously only "missing entirely" was tested), `crm.deal
.quote_request_source_url` accepting an explicit `null` (previously only "missing entirely"
for the opposite direction), `identity_key`'s required-ness on `analytics.event`, and the
non-scalar required-column check on a `session_replay` column (`rage_click_count`), matching
the check's own motivating comment. `LANDING_CONTRACT.md`'s stale "CRM shapes are 1.0.0"
mention (superseded by the 1.1.0 bump) reworded to not name a version at all.

`product/tests/test_landing_contract.py` grew to 57 tests.

## KPI formula shape

### Patch — 2026-09-07 — correctness fixes from automated review (two rounds)

Not a shape change; no field moved, so `CONTRACT_VERSION` stays `2.0.0` per this file's own
policy of bumping only on a breaking change. Headed by date rather than a patch-style version
number for that reason — nothing at runtime will ever equal a `2.0.1`, so a heading implying
one exists is worse than a heading that doesn't try. All were defects that made
`KpiDefinition` refuse a valid formula, or (round two) a fix for one of those defects
overshooting into a new false positive.

| Fix | Was |
|---|---|
| `source_tables` is normalised to lower case at construction | `referenced_tables` lower-cased what it read from the SQL but the drift check compared against the raw `source_tables`, so `source_tables={"Orders"}` against `FROM Orders` was refused — and the refusal blamed the author for omitting a table they had listed |
| `_TABLE_RE` captures an optional schema qualifier and keeps the table | `FROM silver.orders` captured `silver`. Since `TableRef.schema` defaults to `"silver"`, qualified formulas are the expected case: listing `orders` failed construction, and listing `silver` passed construction and then left the KPI permanently blocked, because no snapshot holds a table called `silver` |
| `_POSITIONAL_RE` no longer requires a non-word character before `?` | `WHERE a=?AND b=1` and `col?` slipped past the one check that refuses positional binding |
| `_POSITIONAL_RE` excludes a marker only when it is glued on **both** sides at once (round two) | The first fix's lookahead removal was too broad: any `?` glued to a following word character now matched, including one sitting inside an unrelated string literal (a URL query string like `'foo.com/page?x=1'`, or free text like `'what?next'`) — a valid formula that happened to compare against one was refused at construction |

Also in the validation harness, not the contract: the result-shape heuristic now strips
subqueries and treats any outer `GROUP BY` as a rowset, instead of counting projected columns.
The old rule tagged `SELECT SUM(x) FROM t GROUP BY t.id` as `SCALAR` — a rowset materialised
as a metric and thresholded by an alert that could never be right. **This changes the corpus
result: rowsets found went from 2 to 3**, the third being `Product.sell_through_rate`. Round
two: that stripping was not string-literal-aware, so an unbalanced `(` inside a string (a
store name like `'Toys(R)Us'`) could swallow a genuine outer `ORDER BY`/`GROUP BY`/`LIMIT`
appearing after it, and `GROUP BY` text sitting inside a string literal could itself be
misread as a real outer clause. Fixed by tracking single-quoted string state while scanning
and dropping string content from the output entirely, in both directions.


### 2.0.0 — 2026-09-07 — closed sets move out of Python

Agreed by: Lane K. **Breaking; still awaiting Lane R review** along with 1.0.0.

`grain` and `business_area` were Python enums whose members came, unexamined, from the
POC's synthetic ecommerce ontology — `stock_level`, `warehouse_zone`, `category`. Two things
were wrong with that. They were one dataset's concepts masquerading as facts about retail,
and a frozen enum cannot be extended by a customer overlay, which contradicts D9 and G10:
one ontology ships, customers extend it, and the overlay survives a core upgrade.

Both fields are now `str`, drawn from a vocabulary loaded from the shipped ontology plus the
customer's overlay. The closed-set rule is unchanged and now enforced at the point it
matters: `KpiRegistry.build` refuses a term the deployment does not hold, and a skill's
prompt renders its enumerated list from the same vocabulary rather than hand-typing it, so
the prompt stays correct after an overlay is applied.

`result_shape` and `feasibility` stay Python enums, because a customer's business cannot add
a fourth outcome to a set difference or a third way for SQL to return. They are mirrored in
the vocabulary so prompts can render them uniformly, with `enums_match_vocabulary()`
checking the two definitions have not drifted.

Shipped `grain` now has eleven terms, all defensibly retail-general. The test applied to
each: would the next retail customer recognise it without being told what business we built
it for? `stock_level` and `warehouse_zone` failed that test and were dropped.

| Change | Cost to Lane R |
|---|---|
| `grain: Grain` → `grain: str` | Type change on a field they have not yet consumed |
| `business_area: BusinessArea` → `str` | Same |
| Terms validated by `KpiRegistry`, not `KpiDefinition` | A bare definition is unchecked until it enters a registry; build registries, not lists |
| Vocabularies load from disk | Both sync and async variants provided |


### 1.0.0 — 2026-09-07 — initial, after validation against the POC corpus

Agreed by: Lane K. **Not yet reviewed by Lane R** — this contract is `K → R`, so it is a
proposal until Lane R has read it.

The shape as written in the delivery plan is *name, description, formula with its filter
parameter, grain, source tables, business area*. Validating it against nineteen real
formulas (`cookbook/94_retail/K11_kpi_formula_shape.py`) changed it in five places. Each is
a departure from the agreed wording and therefore needs Lane R's agreement before P3.

| Change | Why | Cost to Lane R |
|---|---|---|
| **Named parameters only; positional `?` refused** | 15 of 19 formulas bind positionally. A `?` carries no name, so nothing can check that supplied arguments match expected ones, and 4 formulas declared a `param` name the SQL never used — undetectable while the marker is anonymous. | Mechanical rewrite, done in `migrate_poc_record`. |
| **`filter parameter` becomes `parameters`, plural, each with a kind** | The contract assumed one filter parameter. The corpus uses two mechanisms that are not interchangeable: a `PREDICATE` (an interpolated boolean SQL fragment, the concept's scoping filter) and a `SCALAR` (a driver-bound value). Collapsing them would mean either interpolating a value or binding a fragment; both are wrong. | Lane R binds the two kinds differently. |
| **`result_shape` added** | 2 of 19 return row listings, not metrics. An alert cannot be evaluated against a top-10 list and a trend cannot be drawn from it. | R5 materialises the two shapes differently; R12 skips `ROWSET`. |
| **`time_window` declared, not written into the SQL** | 4 formulas express a lookback as wall-clock `now` minus an interval. The same formula run twice gives two answers, yesterday's number cannot be recomputed and a backfill is impossible. | Lane R binds an as-of date at execution. Settles the "recompute or append" question the plan flagged: **recompute against a bound as-of date**. |
| **`dialect` added** | 5 formulas use SQLite-only functions against a PostgreSQL warehouse (D5). | Naming it turns a 2 a.m. runtime failure into a listed translation task. |

Two fields were considered and **not** added:

- *Formula-carried grain.* The plan asked whether the formula should carry its own grain.
  It should not: grain is a property of the definition, and letting the SQL imply it means
  Lane R parses SQL to decide how to materialise. `grain` stays a declared closed-set field.
- *A correctness assertion.* `StockLevel.stockout_risk_count` cancels to a constant and
  would still be `ready`. Tempting to add a check; wrong place for it. Feasibility answers
  *can this be computed*, golden questions (R15) answer *is the answer right*. Merging them
  would make the badge mean two things.

## Semantic read API

### 1.0.0 — 2026-09-07 — initial, with a running stub

Agreed by: Lane K. **Not yet reviewed by Lane R** — this contract is `K → R`.

Four reads, as K5 specifies: what tables exist, what columns a table has, what a concept
means, and what joins connect two tables. Plus `list_business_areas`, because K9 derives
supported areas from confirmed mappings and the discovery screen groups by area. Every
method has a sync and an async variant.

Two decisions are encoded in the shape rather than left to callers to remember.

**`include_proposed` defaults to `False` on every read.** The constraint is that nothing
unproposed and unconfirmed enters the semantic layer; a read surface whose default returned
unreviewed rows would make that constraint depend on every caller opting out. A proposed
column nested inside an accepted table is filtered too, so a caller that did not ask for
proposals cannot receive one inside something it did ask for. **Open question for Lane R** —
raised in P0, unanswered so far, and the review screen is the caller that will need `True`.

**`find_join_path` distinguishes three outcomes.** A path, `None` when the graph holds no
path, and a raised `SemanticReadError` when the reader cannot answer. Collapsing the last two
is how a model ends up inventing a join: a caller handed `None` tells the user the datasets
cannot be joined, and that must never be a way of saying the graph was unreachable.

**The stub ships with no fixture**, per the P0 decision. Seeding it from the POC's synthetic
ecommerce payloads would have handed Lane R plausible retail shapes that are not the
customer's; an unloaded read raises `FixtureNotLoaded` naming the fix instead. Lane R gets
the half that actually blocks them — signatures, types and the semantics of an empty result —
and swaps in a fixture when the harvest lands.

### 1.0.0 — 2026-09-07 — reviewed by Lane R: accepted, with an HTTP hardening

Agreed by: Lane R. The `K → R` contract is now agreed on both sides.

`include_proposed=False` as the default: **accepted**. The argument holds — a read surface
whose default returns unreviewed rows makes the constraint depend on every caller opting out.

Over HTTP the default is kept and strengthened: `include_proposed=true` additionally requires
the `semantic:read_proposed` scope, because in-process it is a deliberate act by our own
code and over HTTP it is an attacker-controlled query parameter returning unreviewed
synonyms. The three join-path outcomes map to `200` with a path, `200` with `path: null`,
and `503` — never `404` for "no path", since every HTTP client collapses 404 into "empty".
Both are recorded in the API contract's own changelog
(`egpt_retail/api/openapi/CHANGELOG.md`, 1.0.0); they change nothing in this module.

**Additive, announced (rule 1):** `_fixtures/r3_contract.json`, generated by
`egpt_retail.contracts.fixture_builder` from the shipped vocabularies and the vendored KPI
corpus. The stub still ships with no fixture; this file exists so the phase-zero stub
*server* has something to serve and every example in the API contract is drawn from one
source. Header reads "contract-shaped, not customer-shaped. Replace at K5." Nothing about
the stub or this contract changes.

**Open question raised, not resolved:** the prototype's business hierarchy carries ten retail
areas (`inventory`, `orders`, `merchandise`, `store-ops`, `e-com`, `pos`, `support`,
`pricing`, `finance`, `workforce`); the shipped `business_area` vocabulary carries six. The
API contract treats area ids as vocabulary terms plus overlay, so today four of the
prototype's areas would be overlay terms. Whether the shipped vocabulary should grow, or the
prototype should remap, is Lane K's call and needs settling before P5 wiring.

## KPI formula shape — reviewed by Lane R

### 1.0.0 and 2.0.0 — 2026-09-07 — accepted

Agreed by: Lane R. Both versions are now agreed on both sides.

All five 1.0.0 departures accepted. Consequences Lane R records for its own packages:

- **R6's SQL guard parses the assembled statement, not the template.** An interpolated
  `PREDICATE` is a fragment that only exists post-assembly, so a guard over the template
  would pass a formula whose accepted `sql_filter` was later edited into something else.
- **R5 recomputes against a bound as-of date**, replacing that partition rather than
  appending. `time_window` declared-not-baked is what makes yesterday recomputable.
- **R5 refuses a `sqlite` formula against the warehouse** rather than translating silently.
  Translation is a listed task, not a runtime surprise.
- **R12 skips `ROWSET`** — an alert cannot be evaluated against a listing.

2.0.0 accepted: enums-to-vocabulary-terms is the only shape compatible with D9 and G10, and
`KpiRegistry.build` is the right enforcement point — the set is closed at runtime against
the deployment's vocabulary, which is what "closed sets" has to mean once customers extend
the ontology. One request, additive: **expose the vocabulary version `build` validated
against** on the registry, so R5's provenance record (`Provenance.vocabulary_version` in the
API contract) can name it without a second lookup.

## Executor registry — reviewed by Lane R

### 2026-09-07 — one-name exemption accepted, one amendment

The one-executor-one-name exemption from the standing sync/async rule is **accepted**: the
flat `__name__` namespace cannot afford a doubled surface for a choice nobody makes, and the
exemption is scoped to executors only.

Amendment, recorded in `EXECUTOR_REGISTRY.md`: `check_registry` inspects live functions and
cannot see the `executor_ref` strings already persisted in stored workflows, so a rename
passes the check and breaks every persisted workflow at once. That is the concrete reason a
rename is a change-protocol event, and it belongs in the naming rule rather than in a test
log.

Lane R also takes ownership of the upstream finding: `Registry.get_function`
(`libs/agno/egpt/registry/registry.py:366`) has no duplicate warning while
`_entrypoint_lookup` twenty lines above does. A framework fix, in Lane R's framework bucket
beside R6 and R7; not part of any phase-zero PR.
