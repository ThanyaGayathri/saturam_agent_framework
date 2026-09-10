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

