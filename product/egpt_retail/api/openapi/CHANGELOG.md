# API contract changelog

One place for every change to the Enterprise GPT API contract, appended to, with the date,
the reason and who agreed. Rule 5 of the change protocol in `docs/plan/delivery-plan.md`.

Contract owner: Lane R. Reviewed by: Lane K. Additive changes are announced here, not
negotiated; a breaking change needs both lanes and a version bump.

## 1.0.0 — 2026-09-07 — initial, derived from the prototype's storage seam

Agreed by: Lane R. **Not yet reviewed by Lane K** — this contract is `R → K`, so it is a
proposal until Lane K has read it. The `semantic` fragment in particular is the HTTP surface
of their contract and is the part they should read first.

The endpoint inventory was mapped accessor by accessor from `frontend/src/storage/*.js` in
`Retail-GPT-prototype`, which the build plan names as the seam the interface will be rewired
through. Mapping it mechanically would have been wrong in five places; those five are the
substance of this version.

| Decision | Why |
|---|---|
| **`alerts.js` is split into `/alerts` (definitions) and `/alert-events` (firings).** | The prototype stores one list and mutates it with acknowledge/snooze/resolve. R12 defines an alert as KPI + operator + threshold + scope + severity + recipient. Collapsed, a screen would PATCH a *threshold* to dismiss a *notification*. |
| **`processes.js` is contracted read-only** (`GET /processes`, `GET /processes/{id}`), backed by K9's business areas. | The build plan's G16 says the Processes menu appears in no wiring table. Contracting it read-only forces the decision now; the write path is deferred with Lane K named as owner of the hierarchy it would edit. |
| **No `profileId` anywhere.** | `keys.js` scopes every key by a fictional demo customer. Under D3 there is one deployment per customer; a literal mapping would bake a demo artifact into the API forever. |
| **`use-case-draft.js` maps to R9's spec/gap/plan, not to a CRUD resource.** `GET /use-cases/{id}/plan` is a view over the compiled workflow. | The module is hardcoded model output, not storage. A plan that is authored in parallel with the workflow is the failure the build plan names. |
| **`kpi-mutations.js` maps to nothing.** | Seven pure array functions; client-side edit state. |

Two decisions inside the `semantic` fragment that Lane K should read as Lane R's answers to
their open questions, and one that strengthens their proposal:

- `include_proposed` defaults to `false` — **accepted**, and over HTTP `true` additionally
  requires the `semantic:read_proposed` scope. In-process it is a deliberate act by our own
  code; over HTTP it is an attacker-controlled query parameter returning unreviewed synonyms.
  The framework has no config surface for a product-defined scope (`AuthorizationConfig` has
  no scope-mapping field), so in phase zero the stub enforces it from the JWT claim itself,
  and registering it in the framework's scope map is a named phase-five cost.
- Join path over HTTP: `200` with a path, `200` with `path: null` when the graph holds none,
  `503` when the reader cannot answer, `400` when an endpoint is not a known table. **Never
  404 for "no path"** — every client collapses 404 into "empty", which is the collapse the
  three-outcome contract exists to prevent.
- Proposals get a bulk accept (`POST /semantic/proposals/accept`) because a single harvest
  produces hundreds and nobody clicks through them one gate at a time (G5).

Also fixed in this version so R7 implements it rather than designs it: the **provenance
record** (`Provenance` in `schemas/common.yaml`) — datasets read, SQL executed with the
guard's decision, rows sampled, freshness, retrieval hits with scores and origin, model tier,
skill and version, semantic snapshot and vocabulary version.

**Known gaps, deliberate:**

- Every list is unpaginated. Phase-zero volumes do not need it; adding `next_cursor` later
  is additive.
- Framework endpoints (chat runs, sessions, schedules, components, health, config) are
  referenced, not re-specified. Specifying a second chat endpoint would be the single most
  expensive mistake available in this contract.
- The stub serves examples, not behaviour: it does not persist a POST. A screen that needs
  to see its own write come back should use the `example` selector to render the populated
  state.
- **`include_proposed=true` does not label proposed rows at the list level.** `TableRef`
  (`listSemanticTables`, inherited from Lane K's dataclass) and the bare-string
  `ConceptList.concepts` (`listSemanticConcepts`) have no `status` field, so a proposed row
  is distinguishable from an accepted one only by having appeared under
  `include_proposed=true`, not by any marker on the row itself. `GET /semantic/tables/{name}`
  and `GET /semantic/concepts/{name}` do label it (`Table.status` / `Concept.status`). Fixing
  the list shapes needs a change to Lane K's `TableRef` dataclass and a `ConceptList` item
  shape, both out of R3's scope; tracked for Lane K to pick up. Round-1 review, PR #187.

## 1.0.1 — 2026-09-08 — fixture: an accepted concept must not name an unreviewed column

`Customer.personal_data_fields` and `.compliance_tags` named `email`/`pii` unconditionally,
even though `customers.email` is a deliberately **proposed** column in the same fixture
(invisible unless `include_proposed=true`). A default, accepted-only read of
`/semantic/concepts/Customer` therefore leaked the existence of an unreviewed personal-data
column through an accepted concept — exactly what "nothing unconfirmed leaks into an answer"
(`contracts/semantic.py`) exists to prevent. Fixed by leaving both lists empty until the
column itself is accepted; the column's own proposed-and-personal-data demonstration is
unchanged. Round-1 review, PR #187.

## 1.1.0 — 2026-09-09 — R9: `Spec.questions` and `GapKind.missing_parameter` (additive)

Agreed by: Lane R (contract owner). Announced to Lane K per rule 5; additive, so no
negotiation and no major bump.

- **`Spec.questions`** (array of string, optional). The spec-extraction skill's refusal shape
  for an *unknown*, as distinct from `notes`, which records what the deployment *cannot
  express*. "…and average basket size" against a registry with no such metric yields an
  empty `kpi_identifiers` for it and a question naming it; "…and pause the underperforming
  ones" yields a question about whether the use case should act. The template's rule --
  never coin a value to avoid asking -- needed a field to ask in.
- **`GapKind.missing_parameter`.** The gap check is "for the chosen autonomy level" (delivery
  plan, R9), and the build plan's section 07 gives each of the four shapes a parameter list.
  A slot the draft and spec do not fill is a gap of this kind, `subject` the parameter name,
  `detail` the builder field that fills it or the statement that none exists yet (Recommend
  and Act slots are P4's). A screen keyed on the enum gains one row to render; every existing
  value is unchanged.

## 1.1.1 — 2026-09-09 — R9: `GapKind.unknown_grain` (additive)

Agreed by: Lane R (contract owner). Additive, so no negotiation and no major bump.

- **`GapKind.unknown_grain`.** Round-2 review, PR #203: `spec.grain` was the one closed-set
  spec field `assess_gaps` never re-checked against ground truth -- `business_area`, each
  entity's `concept` and `kpi_identifiers` all did. `spec_extractor.validate_spec` catches a
  coined grain at extraction time, but the deterministic gap check is meant to be the
  backstop regardless of whether a caller ran that validator first, and until now it
  structurally could not be: it never received the grain vocabulary. Fixed by threading the
  vocabulary's `grain` terms into `assess_gaps` (a new, defaulted-`None` `grains` parameter
  that skips the check when not supplied, so every existing caller is unchanged) and
  `check_spec_gaps` now loads it. A screen keyed on the enum gains one row to render; every
  existing value is unchanged.

## 1.2.9 — 2026-09-21 — kpis: persistent suggestions (`getCurrentKpiSuggestions`, `dismissKpiSuggestion`); semantic: `getSemanticTable` `source_id` (additive)

Agreed by: Lane R (contract owner). Additive, so no negotiation and no major bump. Part of
#302: what a connected source gives the customer once its harvest is over.

- **`GET /kpis/suggestions/current`** (`getCurrentKpiSuggestions`, `KpiSuggestionCurrent`). A
  suggestion run is a job and its result was read by id, so proposed KPIs lasted as long as
  the page that started the run: reload, or come back tomorrow, and they were gone. This is
  the standing answer instead -- the newest successful run of each source, merged, less what
  is already in the registry and what a person dismissed. The KPIs page reads it on open.
  `generating` names a run still working (a source was just connected, its suggestions are on
  the way) and `failed` the newest run that failed with no later success, so an empty list is
  explained rather than silent.
- **`POST /kpis/suggestions/dismissals`** (`dismissKpiSuggestion`, `kpis:write`). Removes a
  suggestion for good: it stays out of `current` and out of every run read afterwards,
  including a later run that proposes the same identifier (the agent has no memory of what was
  declined -- `ProposalStore` makes the same rule for proposals, "re-proposing a rejected thing
  is the failure mode"). Idempotent. Deployment-wide, like the registry it feeds.
- **`KpiSuggestionRun.source_id`** and **`KpiSuggestion.source_id`** (nullable). A run is now
  also started for a source automatically when its harvest finishes (the `suggest-kpis` step
  of the `harvest-source` workflow) and reads only that source's tables; the field says which.
  Null for a run started by `POST /kpis/suggestions`, which reads the deployment's own
  warehouse, as before.
- **`KpiSuggestionRunRequest.source_id`** (optional). Starts a run for one connected source, so
  the run that failed after a harvest (no model configured, a quota) can be started again for
  that source rather than only for the deployment's own warehouse. `404` for an unknown source,
  `409` while its harvest has not finished.
- **`getSemanticTable` `source_id`** (query, optional). `listSemanticTables` gained it in
  1.2.7, so a source's tables could be listed but not read back: the Data catalog listed them
  and found no columns. Absent still means the deployment's own warehouse.

## 1.2.8 — 2026-09-20 — configure: `InternalSource.harvest_state`, `last_harvest_at` (additive)

Agreed by: Lane R (contract owner). Additive, so no negotiation and no major bump. Part of
#302 (phase 4, #308).

- **`HarvestState`** (enum: `not_started`, `building`, `modelled`, `needs_review`, `error`)
  and **`InternalSource.harvest_state`** (required). The Configure card reports semantic-layer
  readiness, not reachability -- and until now the only readiness it could show came from a
  hardcoded per-seed lookup in the frontend (`source-model-state.js`'s `BY_ID`). #306's
  `harvest-source` workflow already records exactly this state on the row
  (`configure.data_source.harvest_state`, the same five words); the wire just never carried
  it, so a screen polling `GET /configure/sources` after starting a harvest had nothing to
  read. `required` rather than optional because every row has one (the column defaults to
  `not_started`); a client is never left guessing what an absent field means.
- **`InternalSource.last_harvest_at`** (Timestamp, nullable). The card's "Last sync". `null`
  until a harvest reaches a terminal state -- the card says "Never" rather than fabricating a
  time, which is the bug the redesign fixed and this field keeps fixed.

`last_harvest_id` is unchanged. The run id alone was never enough to draw the card: reading a
run's status needs the framework's `GET /workflows/{id}/runs/{run_id}` plus the `session_id`
the 202 returned, and says nothing about `needs_review`, which is a decision recorded on the
source, not a run outcome.

## 1.2.7 — 2026-09-20 — semantic: `listSemanticTables` `source_id` (additive)

Agreed by: product (nandha-saturam). Additive -- one new optional query parameter and one new
`404` response on one operation, nothing removed or renamed -- so no negotiation and no major
bump.

- **`GET /semantic/tables?source_id=`** (#307). Absent means the deployment's own warehouse,
  exactly what this operation returned before a customer could register a source. Present
  lists that registered source's tables (`InternalSource.id`); an id naming no registered
  source is `404`. The two sets never mix: a self-serve source's tables are absent from the
  default listing, and the default listing's tables absent under a source -- the property
  the SQL guard's allow-list and KPI feasibility depend on, since both are computed from this
  listing and a customer's `orders` must not make an operator KPI look ready. Under a
  `source_id`, `snapshot` describes that source's own harvest (newest table snapshot version,
  last harvest time, source name), never the deployment's.
- An explicit empty `source_id=` is not the same as omitting it: it names no registered source
  and is `404`. And a registered source that was harvested but now holds no tables reports
  `snapshot.version: "empty"` beside its real `harvested_at`; `"unharvested"` appears only
  with `harvested_at: "never"`, so the two fields cannot disagree.

## 1.2.6 — 2026-09-19 — kpis: `ColumnUsage`, `TableUsage` (additive)

Agreed by: product (nandha-saturam). Additive -- two new schemas, one new optional field on
`Kpi`/`KpiSuggestion` and two on `KpiWrite`, six new optional fields on `SourceTableStatus`,
nothing removed or renamed -- so no negotiation and no major bump.

- **`ColumnUsage`** (new schema) and **`Kpi.column_usage` / `KpiWrite.column_usage` /
  `KpiSuggestion.column_usage`** (all optional, default `[]`). The mapping detail panel's
  "the columns it uses" section needed which columns a formula reads, in what role
  (`ColumnUsageRole`: window/filter/measure/dimension/join_key) and why -- the suggestion
  agent already reads every table's full column list to write the formula
  (`suggestions/context.py`'s harvest text), so this is it declaring what it used, not a
  second system parsing `sql` back apart. Carried through adoption like `formula_explanation`.
  Gated behind `kpis:write` the same way `sql` is -- `why` can name a column by what it means.
  `KpiDefinition.__post_init__` refuses a `column_usage` entry naming a table
  `source_tables` does not list, the same drift guard `sql` itself already has.
  `ColumnUsage.definition` is a seventh, read-only field on the same schema, not part of what
  a write or the agent's own output carries: the column's own reviewed definition
  (`Column.meaning`, falling back to `Column.description`, the schema's raw comment, when
  enrichment hasn't reached the column), filled in only by `GET /kpis/{kpi_id}` (same
  enrichment posture as `SourceTableStatus.description` below) -- `""` everywhere else.
- **`TableUsage`** (new schema) and **`KpiWrite.table_usage` / `KpiSuggestion.table_usage`**
  (optional, default `[]`) -- how the formula uses each of its `source_tables` (`TableRole`:
  fact or lookup) and, for a lookup, one line naming what the join confirms. Not a separate
  field on `Kpi`: a KPI's own `table_usage` rides along on `SourceTableStatus` instead (below),
  since both describe the same tables and the panel renders them together.
- **`SourceTableStatus`** gains six fields, read at `GET /kpis/{kpi_id}` time: `description`
  and `concepts` from the semantic layer's own `Table` record (visibility matches
  `source_tables` itself -- concept names, not schema internals, so these are never gated);
  `source_category` (`crm`/`analytics`/`session_replay`) from the landing contract's
  stream-to-source mapping, no I/O; and `role`/`join_note` (this KPI's own `table_usage` for
  the table) plus `unused_columns` (the table's columns, from the semantic layer, that
  `column_usage` does not name) -- all three gated behind `kpis:write` like `sql`, always
  empty/null for a `kpis:read`-only caller regardless of what the KPI has recorded.

## 1.2.5 — 2026-09-18 — kpis: `FormulaExplanation` (additive)

Agreed by: product (nandha-saturam). Additive -- one new schema, one new optional field each on
`Kpi`, `KpiWrite` and `KpiSuggestion`, nothing removed or renamed -- so no negotiation and no
major bump.

- **`FormulaExplanation`** (new schema) and **`Kpi.formula_explanation` /
  `KpiWrite.formula_explanation` / `KpiSuggestion.formula_explanation`** (all optional,
  nullable). The mapping detail panel's "Technical" section needed the formula rendered in
  plain, readable notation for a reader who will never see `sql` -- a literal `SELECT` is not
  something a business user parses, and there is no general SQL-to-English translator reliable
  enough to run over arbitrary generated queries. Rather than build KPI-to-KPI linkage for a
  numerator/denominator relationship (a Phase-2-sized modeling change), the suggestion agent
  now emits the plain-English rendering itself at proposal time (`skills/kpi_suggestion.py`,
  `skills/kpi_suggestion.md` section 5): `expression_before`/`expression_after` are the
  surrounding text, and `numerator_label`/`denominator_label` name the two halves of a ratio
  when the formula is one (both set, or both null). Carried through adoption exactly like
  `rationale`/`business_value` (`_kpi_from_write`'s carry-forward semantics). Gated behind
  `kpis:write` on `Kpi` and `KpiSuggestion` the same way `sql`/`proposed_sql` are -- its labels
  can name the same columns and tables the formula reads, which is exactly the class of detail
  that gate exists to protect.

## 1.2.4 — 2026-09-18 — kpis: `Kpi.source_table_status`, `POST /kpis/{kpi_id}/compute`'s `as_of` (additive)

Agreed by: product (nandha-saturam). Additive -- one new schema (`SourceTableStatus`), one new
optional field on `Kpi`, one new optional query parameter and a documented `422`, nothing
removed or renamed -- so no negotiation and no major bump.

- **`Kpi.source_table_status`** (array of `SourceTableStatus`, default `[]`). The detail
  panel's "where this number comes from" tab needed each of a KPI's `source_tables`' most
  recent load (row count, when it loaded, the load's quality-gate verdict) -- read from the
  warehouse's own `quality.load_run`/`quality.load_stream_count` history
  (`warehouse/lineage.py`), joined to the control-plane KPI definition via the existing
  stream-id registry. Populated only by `GET /kpis/{kpi_id}` (a list read never pays for N
  per-row warehouse queries); `create_kpi`/`update_kpi` return it empty. Best-effort: `[]`,
  never a failure, when the warehouse is unreachable or a table has no load history -- this is
  enrichment for the panel, not core KPI data.
- **`POST /kpis/{kpi_id}/compute`'s new `as_of` query parameter** (date, optional, defaults to
  today UTC). The same panel's "Re-run for another date" needed to materialise a specific past
  business date, not only today -- `materialise_gold` already accepted an `as_of` via its
  workflow `additional_data`, just unreachable from the API. Bounded to
  `[today - 3 years, today]` at `_as_of()` (`executors/gold.py`), the shared point every
  `materialise_gold` caller passes through; out of range is a new **`422`**
  (`common.yaml#/Error`), not the `503`/`409` this route already returns for other refusals.

## 1.2.3 — 2026-09-17 — kpis: `KpiSuggestionRunRequest.ask`, a request in the customer's words (additive)

Agreed by: product (nandha-saturam). Additive -- one new, nullable, non-required field on the
run request and its echo on the run -- so no negotiation and no major bump.

- **`KpiSuggestionRunRequest.ask`** (string or null, at most 2000 characters, not required)
  and **`KpiSuggestionRun.ask`** (its echo). The discovery page's "Add KPI" gains an
  AI-generated path: the customer says what they want to measure, in their own words, and a
  suggestion run is started with that as its request. It reaches both agents in the message
  after the focus (1.1.8) and before the standing ask, worded as the request to answer first
  -- a steer, never a filter, for the same reason the focus is one: what comes back is still
  judged by `assemble`, stamped with the ontology's area, and checked for feasibility exactly
  as an unprompted run's proposals are, so a request the harvest cannot back produces no
  invented metric, only the agent's `notes` saying so. Whitespace-only reads as absent; the
  message is then exactly what it was before this field existed. Echoed on the run row so a
  screen restoring a run from its id can say what it was for.

## 1.2.2 — 2026-09-17 — kpis: `Kpi.rationale`, `Kpi.business_value` kept after adopt (additive)

Agreed by: product (nandha-saturam). Additive -- two new, nullable, non-required fields on
`Kpi` and `KpiWrite` -- so no negotiation and no major bump.

- **`Kpi.rationale`, `Kpi.business_value`** (string or null, not required) and the same two on
  **`KpiWrite`**. 1.1.10 gave a *suggestion* its "why the model proposed it" and "what value it
  provides" text, and the discovery panel shows both -- until the customer clicks "Add to my
  metrics", at which point the panel switches to the registry row and both vanish, because the
  registry never had anywhere to keep them. Adopt now resends them on `KpiWrite` and the
  registry stores them, so the panel says the same thing about a KPI after it is governed as it
  did while it was a proposal, on every later visit and for every viewer. Presentation only,
  like `unit`/`industry_benchmark`: neither participates in feasibility or execution, and an
  update that omits them carries the stored value forward the way every optional `KpiWrite`
  field does. Nullable and outside `required` rather than a breaking bump: a KPI authored by
  hand, or adopted before this field existed, reads back with both `null`.

## 1.2.1 — 2026-09-16 — merge: reconcile with `fix/dashboard-suggestion-routing` (additive)

Agreed by: product (nandha-saturam). Additive on top of 1.2.0 -- the dashboards work below was
authored in parallel with, and had not yet incorporated, #272/#276/#277/#278/#280 (already on
`fix/dashboard-suggestion-routing`, this PR's retargeted base). Reconciling the two:

- **`DashboardSuggestionRunRequest.preselected_kpi_identifiers`** (added on the base branch by
  #272, a free-text "already selected by the caller" line appended to the agent's message) is
  **not carried forward** -- superseded by 1.2.0's own `kpi_identifiers`, which does the same
  job as structured, validated composer context (the agent is shown the metric's definition and
  the semantic-layer tables behind it, not just its name in a sentence) rather than free text.
  A caller written against the base branch's field name gets a 422 naming the unknown field,
  same as any other unrecognised property; `kpi_identifiers` is the field to send instead.
- **`KpiSuggestion.business_value`** (1.1.10, below) is unaffected and carried forward as-is --
  unrelated to dashboards, no overlap with anything in 1.2.0.
- Every other dashboards-relevant change on the base branch (the unified create-dashboard mode
  picker, the editable refresh-schedule control, the abandon-AI-suggestion reset) was already
  independently reapplied on top of 1.2.0's new chart/tile model during this PR's own round-1
  review response, before the base was retargeted -- so nothing further to port for those.

## 1.2.0 — 2026-09-16 — dashboards: composed charts, per-tile CRUD (**breaking**)

Agreed by: product (nandha-saturam). **Breaking, not additive** -- flagged in round-1 review
of the PR that introduced it (#281), which shipped this exact rename against an unchanged
`info.version` for a day; this entry is that fix, landing before the PR merges to `main`.

- **`DashboardSuggestion.tiles` → `charts`, type changed `DashboardTileSuggestion[]` →
  `DashboardChartSuggestion[]`.** A strictly-typed client built against 1.1.8's `tiles` field
  breaks. The dashboard-suggestion agent no longer only selects a KPI identifier from a closed
  list (1.1.8's whole design) -- it is shown the selected metrics' definitions and the
  semantic layer's tables/columns/joins behind them, and composes the chart a purpose needs: a
  metric's own SQL reused as-is (`source: kpi`, unchanged tile shape) or a query composed
  across the tables (`source: query`, new). `DashboardTileSuggestion` is removed;
  `DashboardChartSuggestion` carries `title`, `chart`, `source`, `kpi_identifier`/`query`,
  `description`, `rationale`, and a `guard` verdict (`ok`/`rewritten`/`refused`, with detail)
  from the warehouse read guard every composed query passes before the caller sees it, plus an
  optional bounded `preview` (columns/rows/duration).
- **`Tile`/`TileWrite`** gain `source` (`kpi`|`query`, default `kpi`), `query` (a new
  `TileQuery`: `sql`, `dialect`, `source_tables`, `x`/`y` `ChartAxis`, `series_by`),
  `description` and `rationale` -- all additive, all defaulted, so a pre-1.2.0 tile JSON
  reads back unchanged as `source: kpi`. `kpi_identifier` on `Tile` is now nullable (null on a
  `query` tile) -- additive in the loosening direction, but a caller that assumed it always
  present should check `source` first.
- **New routes, all additive:** `POST/PATCH/DELETE /dashboards/{id}/tiles[/{tile_id}]` (a
  tile keeps its id across an edit instead of every write reminting every tile's id -- also a
  behaviour change on the *existing* whole-array `PATCH`, worth knowing even though no schema
  changed: `TileWrite.id`, optional, is honoured when a client sends one); `POST
  /dashboards/queries/preview` (guard + bounded run, nothing stored); `POST
  /dashboards/{id}/refresh` (recompute every `query` tile now).
- **`DashboardSuggestionRunRequest`** gains `kpi_identifiers`, `table_names`, `chart_budget`
  (additive, all optional) -- what the agent reads as context, not a KPI list it must tile one
  for one. **`DashboardSuggestionRun`** gains `stage` (additive, nullable) for progress while
  `running`.
- **`Column`** (the semantic-layer schema) gains `concept`, `meaning`, `joinable` (additive) --
  already on the domain dataclass (`contracts/semantic.py`, contract 1.2.0 there predates this
  one) but never mirrored onto the wire shape until now. **`GET /semantic/tables` and `GET
  /semantic/tables/{table_name}`** are mounted on the real router for the first time (no
  schema change -- they were already contracted, stub-only); a personal-data column's
  `sampled_values` are stripped over this surface regardless of scope, since real observed
  values are not a description of the data and were never meant to be readable this way.

## 1.1.10 — 2026-09-16 — kpis: `KpiSuggestion.business_value` (additive)

Agreed by: product (nandha-saturam). Additive -- one new, nullable, non-required field on an
existing schema -- so no negotiation and no major bump.

- **`KpiSuggestion.business_value`** (string or null, not required). K12's `rationale` answers
  "what data is this derived from" -- traceability, not motivation. Nothing in the contract
  said why a proposed metric is worth tracking at all, which the discovery screen's "Why the
  model proposed it" panel needed to answer on its own. `business_value` is the agent's
  one-or-two-sentence answer to that, rendered alongside `rationale` rather than folded into
  it, since the two are different claims with different evidence: one traces to tables, the
  other to what a customer would do differently once they can see the number. Nullable and
  outside `required` rather than a breaking bump: a suggestion run persisted before this field
  existed reads back with `business_value: null`, not a contract violation.

## 1.1.8 — 2026-09-15 — dashboards: suggestion runs, `POST /dashboards/suggestions` + `GET /dashboards/suggestions/{run_id}` (additive)

Agreed by: product (nandha-saturam), for the "Create dashboard" flow's AI-generated path
(#222). Additive -- two new operations, five new schemas, nothing removed or renamed -- so no
negotiation and no major bump. Numbered 1.1.8, not 1.1.6: PRs #259 (`KpiSuggestionRun.phase`)
and #264 (`BusinessArea.concept_count`), open at the same time, already claim 1.1.6 and 1.1.7.

- **`POST /dashboards/suggestions` → `202` `DashboardSuggestionRun`,
  `GET /dashboards/suggestions/{run_id}`.** The same run-then-poll shape 1.1.5 gave KPI
  suggestions (#254), for the same reason: a live model call takes long enough that it does
  not belong inside one request. `dashboards:read`, not `dashboards:write` -- what is created
  is a run row, never a dashboard; a suggestion becomes one only through the existing
  `POST /dashboards`.
- **`DashboardSuggestionRunRequest`** (`purpose`, required, non-empty) is the `POST` body: what
  the caller says the dashboard should help them track, in their own words.
- **`DashboardSuggestion`** (`name`, `tiles: DashboardTileSuggestion[]`,
  `filters: DashboardFilter[]`, `notes`) is a run's `result` once `succeeded`. Every tile names
  a real, registered, `ready` KPI -- the agent selects from this deployment's own registry
  rather than inventing one, so a suggestion is exactly as adoptable as a dashboard built by
  hand and needs no separate feasibility check before `POST /dashboards` accepts it. The one
  filter field this agent may propose is `business_area`: it is the one `DashboardFilterField`
  with a well-defined value space to validate against (this deployment's own vocabulary,
  exactly as `KpiSuggestionDraft.business_area` is validated). `time_window` has no established
  filter-value format anywhere in this contract yet -- nothing today constructs one -- so this
  agent never proposes one rather than guessing a shape. Reusing `DashboardFilter` rather than
  a parallel draft-only shape means a suggestion's filters round-trip into `POST /dashboards`
  unchanged, the same "renders without transformation" guarantee 1.1.5's `KpiSuggestion`
  carries for the discovery screen.

## 1.1.9 — 2026-09-15 — kpis: `GET /kpis/{kpi_id}/series` + `POST /kpis/{kpi_id}/compute` (additive)

Agreed by: product (nandha-saturam), for the discovery detail panel's value graph (part of
#222). Additive -- two new operations, four new schemas, nothing removed or renamed -- so no
negotiation and no major bump. Numbered 1.1.9 because 1.1.6, 1.1.7 and 1.1.8 are claimed by
open PRs (#259, #260, #261).

- **`GET /kpis/{kpi_id}/series` → `KpiSeries`.** The value a KPI materialised over its last
  `days` (default 30) business dates, read from `gold.kpi_value` by KPI identifier -- the
  same rows `GET /dashboards/{id}/data` already plots for a `line`/`bar` tile, which was until
  now the only way to reach them: a KPI had to be tiled on a saved dashboard to have a
  history. One `SeriesPoint` per night that has a row (a missing night is absent, not
  `null`), the latest `value`, its `delta` against the previous point, gold's stamps and a
  `KpiHealthStatus`. A per-dimension KPI follows one `dimension` at a time and lists the
  `dimensions` the latest night carried. A KPI with no gold rows is a `200` with empty
  `points` and `status: never_computed`, never a 404: the KPI exists; its value does not yet.
- **`POST /kpis/{kpi_id}/compute` → `KpiComputeResult`.** Runs the nightly `materialise_gold`
  pass for exactly this KPI and today's date, so a KPI adopted from a suggestion has a value
  the moment it is adopted rather than after its first night. `kpis:write`, because it writes
  gold. `409` when the KPI is not `ready` (`not_ready`); `503` when gold itself refuses to run
  (`gold_refused`: the silver gates or the load lock, the nightly run's own two refusals). A
  formula that fails is a `200` with `status: failed` and the error -- the request did what it
  said; the formula did not.
- **`KpiSeriesInterval`** (`day`, the only interval gold has) and **`KpiComputeStatus`**
  (`gold.kpi_run.status`'s three words) are the two enums.

## 1.1.8 — 2026-09-15 — kpis: `KpiSuggestionRunRequest.business_areas`, a focus for the run (additive)

Agreed by: product (nandha-saturam). Additive -- one optional field on the request, one
defaulted field echoed on the run -- so no negotiation and no major bump. Numbered 1.1.8:
#259 (1.1.6) and #260 (1.1.7), open at the same time, already claim the two before it.

- **`KpiSuggestionRunRequest.business_areas`** (array of `BusinessAreaId`, optional). The
  discovery page's focus ("Which areas of the business do you care about most?") used to
  reach the run only when exactly one area was chosen, and then only as a filter on what
  came back -- the agents never heard it, and a narrowed run cost the same two model calls
  for a subset of the answer. The focus now goes *into* the message both agents receive,
  as a priority ("propose for these first and most thoroughly"), never as a filter: a
  proposal outside the focus is kept when the data clearly supports it. Every id is checked
  against the vocabulary before the row exists (422 otherwise); duplicates are dropped,
  order is kept. The frontend sends the chosen areas, or every supported area when nothing
  is chosen, so the agents always know where the customer's attention is.
- **`KpiSuggestionRun.business_areas`** (array, default `[]`) echoes the focus on the `202`
  and every poll.
- **`business_area`** (one id, the after-the-fact narrowing filter) is unchanged and still
  honoured, for callers written against 1.1.5; new callers should send `business_areas`.

## 1.1.7 — 2026-09-15 — configure: `BusinessArea.concept_count`, `.table_count`, `.reason` (additive)

Agreed by: product (nandha-saturam). Additive -- three optional, defaulted fields on an
existing schema, nothing removed or renamed -- so no negotiation and no major bump.
Numbered 1.1.7, not 1.1.6: PR #259 (`KpiSuggestionRun.phase`), open at the same time,
already claims 1.1.6.

- **`BusinessArea.concept_count` / `.table_count` / `.reason`.** The discovery page's focus
  prompt ("Which areas of the business do you care about most?") now takes its options from
  `GET /business-areas` instead of a browser-local list, and shows *why* an area is or is not
  supported, not only whether. K9 already computed that evidence on every call -- the
  accepted concepts with a confirmed table mapping, the tables behind them, and a sentence
  either way -- and the router returned only the boolean. The three fields are that
  evidence: the two counts, plus `AreaStatus.reason` verbatim. `0`/`0`/the "No table is
  mapped" sentence on an unsupported area; an overlay-added area (no ontology concepts, so
  K9 never lists it) reads the same. Derived from the graph on every call, never stored --
  the same rule as KPI feasibility.

## 1.1.6 — 2026-09-15 — kpis: `KpiSuggestionRun.phase` and `.typical_duration_seconds` (additive)

Agreed by: product (nandha-saturam). Additive -- two optional, nullable fields on an
existing schema and one new enum, nothing removed or renamed -- so no negotiation and no
major bump.

- **`KpiSuggestionRun.phase`** (`KpiSuggestionRunPhase`: `reading` | `proposing` |
  `checking`, nullable). 1.1.5 made the suggestion pass a run to poll, but a poll that only
  ever said `running` left the screen a stopwatch and nothing else to show for a wait of a
  minute or two. The executor now records the stage the pass is in as
  `suggestions/run.py` reports each one: gathering the semantic layer, the model calls,
  assembly and feasibility. Null on a `queued` run; kept once terminal, so a `failed` run
  says where it failed.
- **`KpiSuggestionRun.typical_duration_seconds`** (number, nullable). The median wall-clock
  duration of the deployment's last five successful runs, read from the run table with the
  row on both the `202` and the poll, so the screen can say "usually about a minute and a
  half" from this deployment's own evidence before the run has done anything. Null until a
  run has succeeded. An expectation, not a bound.

## 1.1.5 — 2026-09-14 — kpis: suggestion runs, `POST /kpis/suggestions` + `GET /kpis/suggestions/{run_id}` (additive)

Agreed by: product (nandha-saturam), for #254 (part of #222). Additive -- two new
operations, three new schemas, one existing operation marked deprecated with a new
response, nothing removed or renamed -- so no negotiation and no major bump.

- **`POST /kpis/suggestions` → `202` `KpiSuggestionRun`, `GET /kpis/suggestions/{run_id}`.**
  The K12 suggestion pass is two frontier-tier model calls and takes minutes; 1.1.0's
  `GET /kpis/suggestions` served it inside the request with no bound, on an api that runs
  from more than one instance. The run is now a resource: `POST` records it `queued` and
  returns it, the caller polls until `succeeded` (`result` is the same `KpiSuggestionList`)
  or `failed` (`error` says why -- the timeout names its bound, and a run whose serving
  instance was lost mid-run is closed as `failed` by the read side rather than left
  `running` forever). Same shape as `POST /semantic/harvests` and `POST /use-cases/{id}/runs`;
  `KpiSuggestionRunStatus` reuses `HarvestStatus`'s four words rather than coining a third
  spelling. `kpis:read`, like the route it replaces: a run row is not a KPI.
- **`GET /kpis/suggestions` is deprecated**, not removed. It is now held to the deployment's
  suggestion timeout and answers `504` past it (new response), where it used to hang.
- **`KpiSuggestionRunRequest`** (`business_area`, optional) is the `POST` body; the `GET`'s
  query parameter of the same name is unchanged.

## 1.1.4 — 2026-09-13 — dashboards: `RefreshSchedule` gains `daily` and `weekly` (additive)

Agreed by: product (nandha-saturam), for the interface's dashboard cadence options (#226,
part of #222). Additive -- two new enum values, nothing removed or renamed -- so no
negotiation and no major bump.

- **`RefreshSchedule`** is now `nightly | daily | weekly | hourly | manual`. The prototype's
  create-dashboard flow offers "every morning" and "Monday mornings" and the contract had no
  value for either, so the frontend would have had to drop two options or lie about the
  third. The description now says what each value means: `nightly` (the default) follows
  gold -- as soon as the nightly materialisation completes (D7); `daily` is once a day at a
  fixed morning time (06:00), reading what gold holds; `weekly` is Monday mornings; `hourly`
  is every hour, capped in practice by gold's cadence; `manual` is only when asked. The
  cadence is stored, not enforced -- there is no scheduler honouring it yet, unchanged from
  1.0.0. `dashboards/store.py` keeps the column as plain `text`, so no migration.
  Delivery (who is sent what, and when) is still not in the contract; the frontend carries
  it locally and says so.

## 1.1.3 — 2026-09-11 — R12: `AlertScope.grain_column` (additive)

Agreed by: Lane R (contract owner). Additive, so no negotiation and no major bump.

- **`AlertScope.grain_column`** (string, optional). Round-2 review, PR #210: a gold row can
  carry more than one grain-key column (a KPI materialised with both `region` and `channel`
  dimensions, say), and `scope.grain_value` alone cannot say which one a definition means --
  a value that happens to equal both silently picked whichever column
  `executors/alerts.py`'s row scan found first, which is "the wrong brand's row, reported as
  a fired alert" with no signal anything went wrong. `grain_column` names the column outright.
  A legacy definition authored before this field existed (`grain_column` absent) is matched
  only when the KPI's rows carry exactly one grain-key column to begin with -- unambiguous on
  shape alone; two or more with no column named is refused rather than guessed, logged and
  reported the same way any other "cannot evaluate" reason is. A screen rendering
  `AlertScope`/`AlertDefinitionWrite` gains one optional field; every existing definition
  (and every existing single-dimension KPI) is unaffected. Numbered 1.1.3, not 1.1.2 --
  R14's `UseCaseExecutionHealth.error` (below) landed on `main` first and already claimed
  1.1.2.

## 1.1.2 — 2026-09-11 — R14: `UseCaseExecutionHealth.error` (additive)

Agreed by: Lane R (contract owner). Additive, so no negotiation and no major bump.

- **`UseCaseExecutionHealth.error`** (string, nullable, optional). Round-2 review, PR #211:
  `assess_execution_health` isolates one malformed use-case entry (a missing `workflow_id`,
  say) so it can't abort every other use case's report, and returns an error record in its
  place -- but that record's `error` key had nowhere to go in the schema, so a caller
  validating through the generated model would have silently dropped it, making a
  genuinely-errored use case indistinguishable from one that has simply never run. Latent
  today (`UseCaseExecutionHealth` has no live instantiation site yet -- `/monitoring/execution-health`
  isn't wired into a router until P5), fixed now so the gap isn't rediscovered once it is.

