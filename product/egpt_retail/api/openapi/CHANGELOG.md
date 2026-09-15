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

