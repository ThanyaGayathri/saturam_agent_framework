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

## 1.11.1 — 2026-09-17 — `AnalyticsSuggestion.window` (additive)

Agreed by: Lane K. One new, nullable field: the days the numbers should cover when the ask
names a period ("last week", "monthly"), null for each KPI's own declared window. The
suggestion also keeps every breakdown the ask names that some registered KPI carries, and
steps unasked-for breakdowns back behind the overall KPI.

## 1.11.0 — 2026-09-17 — merge: reconcile `usecase_processautomation` with main (additive)

Agreed by: Lane K. Additive on top of both lines -- the use-case work below (1.3.0 .. 1.10.0)
was authored in parallel with the KPI discovery work on main (1.2.2, 1.2.3) and neither had
incorporated the other. This version is the union: every path and schema of both, no
operation or field changed in meaning. Bumped once, to 1.11.0, so a client pinned to either
line sees a strictly later version.

## 1.10.0 — 2026-09-17 — the target objects suggested from the template (additive)

Agreed by: Lane K. One new operation, `suggestTargetObjects` (`POST
/record-mappings/suggest-objects`), with `ObjectSuggestRequest` and `ObjectSuggestion`: the
target's createable objects ranked for a template and a description, the per-line child
named, a shipped mapping named where one fits the pair. Deterministic and explained. The
form's Target objects section offers it before the field mapping.

## 1.9.0 — 2026-09-17 — Analytics Automation: the suggestion from the ask (additive)

Agreed by: Lane K. One new operation, `suggestAnalyticsUseCase` (`POST
/use-cases/analytics/suggest`), with `AnalyticsSuggestRequest`, `AnalyticsKpiSuggestion` and
`AnalyticsSuggestion`. The Analytics Automation form's intelligence: an ask in the person's
own words resolved against the governed KPI registry and the gold the deployment holds --
the KPIs that match and can be reported on, their breakdowns, baseline, cadence, outputs,
recipients, and every word nothing covers. Deterministic and explained per KPI. Nothing
stored. `PreflightProbe` gains the Inform shape's three: `gold` (every KPI has a gold row,
and how fresh), `delivery` (SMTP login for the report), `dry_answer` (the numbers now). The Inform shape's `SHAPE_PARAMETERS` gained three optional slots (`window`,
`title`, `outputs`) bound to defaults when a spec leaves them blank; not on the wire.

## 1.8.0 — 2026-09-17 — merge: `main` (1.1.8–1.2.1, KPI suggestion and dashboards) into Lane K's use-case work (1.2.0–1.7.0) (additive)

Agreed by: Lane K. Both lines were authored in parallel from 1.1.7 and both are additive, so
the union is additive too. Two entries below are both numbered 1.2.0 -- Lane K's
document-to-record template and product's dashboards work -- and every schema and path from
both sides is present unchanged; the version simply moves past both. The bundled spec and
the generated models are regenerated from the merged source.

## 1.7.0 — 2026-09-16 — execution health from the use-case runs table (additive)

Agreed by: Lane K, on Lane R's contract under the additive rule. New optional fields on
`UseCaseExecutionHealth`; the first real router for `getExecutionHealth` and
`listSilentSchedules`.

The Monitor screen was a demo: hashed sparklines and a "last run 23 min ago" derived from the
title. Now `GET /monitoring/execution-health` is computed from `retail_use_case_runs` for every
use case the interface created, and carries what a person watching a polling automation
needs beside the contract's counts: the use case's `status`, its `schedule`, what fires it
(`trigger`: the in-process `poller` locally, the `orchestrator` in the cloud, or nothing),
`next_expected_at`, the last run's summary, and records created and exceptions held over
seven days. `silent` is true when the cadence has elapsed one and a half times since the last
run with nothing fired; `GET /monitoring/schedules/silent` lists the same.

## 1.6.0 — 2026-09-16 — rehearsal sources and golden cases (additive)

Agreed by: Lane K, on Lane R's contract under the additive rule. New operations and schemas,
and an optional request body on an existing operation.

A rehearsal had one input: the connected mailbox, all of it. The rehearse screen now offers
three, and the platform serves them:

| Change | Why |
|---|---|
| **`RunRequest` body on `triggerUseCaseRun`, `RehearsalSource`** | A rehearsal runs over chosen messages from the mailbox, the use case's own golden cases, or the generated set -- the person's choice, not the platform's. A live run ignores the body. |
| **`GET /use-cases/{id}/mailbox/messages`, `MailboxMessage`** | The list a person ticks messages from, read with the platform's credentials and nothing flagged; `processed_before` marks what a live run already carried. |
| **`POST /use-cases/{id}/preflight/{probe}`, `ProbeResult`** | The readiness screen showed verdicts computed from the form and called the mailbox "connected". Now each row is a live probe: log into the mailbox and count what matches, take a token from the target, read the master data, call the model once, read the golden cases -- and the screen shows each verdict as its probe returns. |
| **`GoldenCase`, `GET/POST /use-cases/{id}/cases`, `DELETE .../cases/{case_id}`, `POST .../cases/{case_id}/accept`** | A document with what the person expects of it: uploaded, or generated by the platform from the template's fields. Expected values are authored by accepting a run's output. Cases are re-run when the template, guardrails or mapping change (`RunTrigger` gains `regression`), and preflight adds a `golden_cases` check with the pass rate. `UseCaseRun.result.cases` carries each case's verdict. |

## 1.5.0 — 2026-09-15 — the record mapping as data, over the target's live objects (additive)

Agreed by: Lane K, on Lane R's contract under the additive rule. New operations and schemas
only.

The last hard-coded piece of the document-to-record template was the write side: one code
mapping from a resolved purchase order onto Salesforce's Order and OrderItem. Now:

| Change | Why |
|---|---|
| **`RecordMapping`, `MappingFieldRow`, `ChildMapping`, `GET /record-mappings`, `POST /record-mappings/suggest`** | A mapping is a parent object with field rows -- each a target field and the source expressions its value comes from -- an optional child per line, the status field `created as` sets, and the rollback action. One interpreter evaluates it; the two Salesforce mappings that ship are rows of the shape; a mapping the form assembles is stored on the spec's `target objects` parameter. A suggestion is proposed deterministically from names over the live fields. |
| **`GET /targets/{target}/objects`, `GET /targets/{target}/objects/{object}`, `TargetObject`, `TargetField`** | The form offers the org's own objects and their createable fields, read live with the platform's credentials -- required, type, what a reference points at -- so a person maps to what the org actually has, not to a list in code. |

## 1.4.0 — 2026-09-15 — extraction templates and guardrail rules as data (additive)

Agreed by: Lane K, on Lane R's contract under the additive rule. New operations and new
schemas only; every existing value is still read.

The first document-driven use case shipped with its template and its six guardrails in code
(K15). Onboarding a second kind of document from the interface -- a delivery note tomorrow,
with no engineer -- needs both to be data the person can extend, so:

| Change | Why |
|---|---|
| **`ExtractionTemplate`, `GET /extraction-templates`, `POST /extraction-templates/draft`** | A template is a field list, and the extractor, its schema and its validation are generated from that list. The interface stores a template a person assembled (or the model drafted from the description and an example document) on the spec's `extraction template` parameter; the shipped three are rows of the same shape. |
| **`GuardrailRule`, `GuardrailRuleKind`, `GET /guardrail-rules/kinds`, `POST /guardrail-rules/suggest`** | A guardrail is a *kind* the platform evaluates (eight: duplicate reference, sender known, first-time item, quantity outlier, date window, flagged item, numeric range, total matches lines) applied to a field with parameters. The six purchase-order rows are a preset with their old ids and exception classes, so nothing already authored changes; a new template's rules are suggested from its own field names. |

## 1.3.0 — 2026-09-15 — use-cases: a stored spec, a run mode, the run's own result (additive)

Agreed by: Lane K, on Lane R's contract under the additive rule. Every change has a default
or is a new operation; no P0 caller changes.

The first real `/use-cases` router (K16) needed four things the contract described only for
an agent-driven flow:

| Change | Why |
|---|---|
| **`PUT /use-cases/{id}/spec` (`saveUseCaseSpec`)** | The per-shape form *is* the spec (K15): the interface fills shape, template and parameters itself and never runs the extraction agent for a document-driven use case. `extracted_by: {identifier: interface.form}` keeps a hand-assembled spec distinguishable from an extracted one. |
| **`?mode=live|rehearse` on `triggerUseCaseRun`, `RunMode`** | The rehearse screen and the live run share one code path: the same compiled workflow, with side effects held and the sample set injected. A simulation of its own in the interface was ahead of the platform and had already drifted from it (scanned copies). |
| **`UseCaseRun.result`, `UseCaseRun.mode`** | The interface shows what a run created and what it held. `summary` is prose; `result` is the batch the last executor returned, and the screen reads it instead of re-running anything. |
| **`ScheduleCadence` gains `hourly`, `every_15_minutes`, `every_5_minutes`, `every_minute`** | A mailbox is polled, not read nightly. The interface's poll intervals now have a contract value each. |

Not in this version, on purpose: the orchestrator-facing trigger is still the same
`POST /use-cases/{id}/runs` -- a Logic App on Azure or a Step Functions task on AWS calls it
with the internal service token; the body of the run does not know which one did.

## 1.2.0 — 2026-09-15 — use-cases: `Spec.shape`, `Spec.template`, `Spec.parameters` (additive; closes R-15)

Agreed by: Lane K (kiruthiga). Additive -- three optional fields on `Spec` and two new enums --
so no negotiation; a minor bump rather than a patch because it is the field the whole
create-use-case flow has been waiting on since open item R-15 (10 Sep).

- **`Spec.shape`** -- the shape the form was filled against, mirroring
  `contracts.use_case.UseCaseShape`. Until now `Spec` had no shape and the compiler read it
  from a flat key; the interface has chosen the shape first since the per-shape forms landed.
- **`Spec.template`** -- the sequence within a shape, where a shape has more than one. The
  first is `document_to_record` under Act: a Process Automation that turns an emailed
  document into a record. Same shape, same guarantees around the write; different stages
  before it. `kpi_change` names the Act sequence that already existed. The interface reads
  the template from the requirement's words; it is never a form field.
- **`Spec.parameters`** -- the per-shape form's values keyed exactly by the shape's (or
  template's) parameter list, so the gap check and the compiler read one place for every
  shape instead of nine flat keys and `step8.*`. Value shapes for the structured ones
  (guardrails, candidates, recipient, master data, thresholds) are written into the field's
  description so the interface and the compiler cannot drift. The draft still wins over the
  spec, and the compiled workflow records the origin of every value.

Not changed, deliberately: `UseCaseDraft` keeps `step1.autonomy` and `step8.*`, because the
activation screen reads them back; `Spec.delivery` stays, and `parameters.recipient.channels`
carries the per-shape reading of the same fact. `TimeoutBehaviour`, `Rollback` and the
document-template guardrail catalogue remain strings with their closed sets documented rather
than enums -- they are content the product ships, not contract.
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

