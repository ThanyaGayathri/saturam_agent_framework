# How a document-to-record use case comes to life

*From a sentence typed on the form to a scheduled poll that creates records in Salesforce.
What each part of the platform does at every step: the person, the browser, the AI agent,
the orchestration compiler, the Agno workflow engine, the executors, the store.*

The example throughout is **"Purchase order → Salesforce"**: PDFs arrive by email, become
Draft Orders. **"Return request → Salesforce Case"** goes through exactly the same steps —
only the data (template, mapping, guardrails) differs.

---

## The cast

| Who | What it is | Where |
|---|---|---|
| **Person** | Describes the use case, confirms the form, approves the plan, activates. | the browser |
| **Reader** | Deterministic text reader that fills the form from a description. Not an AI model. | `frontend/src/domain/use-case-extract.js` |
| **Assistant** | Deterministic suggestions for whatever the reader could not fill (options with a "why"). | `frontend/src/domain/use-case-suggest.js` |
| **AI agent (drafting)** | An Agno `Agent` that proposes a field list for a document kind the platform has never seen. Used only when a person clicks "New document kind". | `product/egpt_retail/documents/template_draft.py` |
| **Platform service** | The FastAPI backend: stores use cases, compiles, probes, runs. | `product/egpt_retail/usecases/service.py`, router `api/routers/use_cases.py` |
| **Orchestration compiler** | Pure code that turns a spec into a workflow definition. Consults no model. | `product/egpt_retail/orchestration/compiler.py` |
| **Agno** | The workflow engine: a `Workflow` of `Step`s run in order, each step either an executor function or an `Agent`. | `libs/agno/egpt/workflow/` |
| **Executors** | Deterministic Python functions, one per stage (`receive_messages`, `resolve_entities`, `write_to_target`…). Same input, same output. | `product/egpt_retail/executors/documents.py`, `executors/act.py` |
| **AI agent (extraction)** | The one model step in the run: reads a PDF's text and fills the template's fields. | `usecases/runtime.py::build_extraction_agent` |
| **Store** | Three tables: `retail_use_cases`, `retail_use_case_runs`, `retail_use_case_cases`. | `usecases/store.py`, `usecases/ddl.py` |
| **Poller** | In-process scheduler that starts a live run when a use case's cadence is due. | `usecases/poller.py` |

Rule of thumb: **an AI model runs in exactly two places** — drafting a *new* template on the
form, and extracting fields from a document during a run. Everything else is deterministic
code.

---

## Step 1 — Describe it, and the form fills itself

**Who:** person → reader → assistant (all in the browser, no model).

The person types one sentence: *"Create sales orders in Salesforce from purchase orders that
arrive as PDFs in the orders mailbox, checking every 5 minutes. Send exceptions to
ops@… At most 20 orders per run."*

The **reader** (`extract()`) scans it for cues and fills the spec:

- category *Process Automation* → shape **Act** → template **document_to_record**
- "arrive as PDFs in the orders mailbox" → mailbox + document type
- "purchase orders" → extraction template `purchase_order` from the catalogue (or a proposed
  new kind if no template matches)
- "every 5 minutes" → schedule; "exceptions to …" → recipient; "at most 20" → per-run cap
- "Salesforce", "sales orders" → write target and a candidate record mapping

Every field it filled is marked *proposed*; every field it could not fill becomes a **gap**
(`use-case-spec.js::gapsFor`). For each gap the **assistant** offers options with a reason
(which mailbox is connected, which mapping matches the template's `record`, which
guardrails the template's field names call for). The person picks or types.

Three sections are *data the person can extend*, still without a model:

- **Extraction template** — pick a shipped one, copy-and-edit, or **New document kind**.
  Only that last button calls the platform: `POST /extraction-templates/draft` runs the
  **drafting agent** (an Agno `Agent` with `output_schema=TemplateDraft`) over the
  description and an optional example PDF, and returns a field list the person edits.
- **Business guardrails** — `suggestRules(template)` derives rules from the field names
  (a reference field → duplicate check, a due date → date window, an item field → restricted
  / first-time item…). Eight rule kinds exist; every guardrail is one of them with parameters.
- **Record mapping** — **New mapping** reads the target's live objects
  (`GET /targets/salesforce/objects/{name}`, a real `describe` call) and
  `POST /record-mappings/suggest` proposes rows by name and by reference type
  (`AccountId ← customer.id`, `Subject ← rma_reference`, picklists left to a literal).
- **Master data** — the roles the use case resolves against. Which roles are *needed* is
  derived, not fixed: the roles the mapping's source expressions read plus what the
  guardrails need (`use-case-roles.js`, mirror of `documents/roles.py`). The form says
  *"read by the mapping"* / *"needed by a guardrail"* / *"not used by this use case"*.

**Output of the step:** a `spec` object in the browser draft — `shape`, `template`, and
`parameters` keyed by the template's slot names (`source mailbox`, `message filter`,
`extraction template`, `guardrails`, `target objects`, `created as`, `per-run cap`,
`recipient`, `schedule`…). Credentials are never in it — a source carries only
`credentialRef: "env:gmail"`.

---

## Step 2 — Save & validate: the spec reaches the platform

**Who:** browser → platform service → store.

`syncUseCaseToPlatform()` makes three calls:

1. `POST /use-cases` — one row in `retail_use_cases` (title, area, autonomy, status
   `draft`, version 0). Later saves use `PATCH`.
2. `PUT /use-cases/{id}/draft` — the form's raw answers, for reopening later.
3. `PUT /use-cases/{id}/spec` — the frozen spec (`specPayload`): question, entities
   (derived from the roles and the mapping's object), shape, template, parameters.

Saving a spec **drops any compiled snapshot a rehearsal left on a draft**, so what follows
always reads the spec just saved. If the template, guardrails, mapping or thresholds changed
materially and golden cases exist, the service queues a **regression** rehearsal in the
background (trigger `regression`) so the golden verdicts reflect the new spec.

---

## Step 3 — Readiness: real probes, one at a time

**Who:** browser (local checks) → platform service (live probes).

The readiness screen has two layers.

**Local checks** (browser, instant): every gap closed; the master-data roles the mapping
reads are bound; at least one guardrail; exceptions address set; cap and rollback chosen.

**Platform checks** — `POST /use-cases/{id}/preflight`:
`spec_present`, `workflow_compiles` (the compiler runs on the spec — see step 4),
`mailbox_credentials` and `target_credentials` (the `credentialRef`s resolve from the
server's environment — `gmail.env`, `salesforce.env`), `extraction_model`
(`AZURE_OPENAI_API_KEY` present), `golden_cases` (how many pass).

**Live probes** — `POST /use-cases/{id}/preflight/{probe}`, one request each, so the rows
land one by one with their timings:

| Probe | What actually happens |
|---|---|
| `mailbox` | IMAP login to the Gmail account; a headers-only search with the use case's own filter (unread / subject phrase / not our domain). Reports *"N message(s) match the filter right now"*. Nothing is flagged or moved. |
| `target` | OAuth client-credentials token from the org's My Domain; `SELECT COUNT() FROM <the mapping's object>`. Reports *"can read and create Order records"* (or Case, …). |
| `master_data` | Loads accounts (with email domains), contacts, ship-to sites, products with price-book entries, and order history; reports counts **for the roles this use case needs**. |
| `model` | One real call to the Azure OpenAI deployment ("Reply with the single word OK"). |
| `golden` | Summary of the golden cases: with expected values, passing, failing, not run. |

Every probe compiles the *current* spec first (`UseCaseService.current_parameters`), so an
edit on the form is what gets probed.

---

## Step 4 — The plan: the orchestration compiler builds the flow

**Who:** orchestration compiler (pure code, no model).

`compile_workflow(spec)` in `orchestration/compiler.py`:

1. **Shape → template → stages.** The Act shape with the `document_to_record` template is a
   fixed sequence of **11 stages**: Receive, Screen, Extract, Resolve, Apply guardrails,
   Build record, Apply per-run cap, Write, Record rollback point, Notify, Record provenance.
   The shape *is* the template: two document use cases are the same sequence with different
   slot values.
2. **Stage → step.** Each stage becomes a step config naming its **executor** (`receive_messages`,
   `screen_documents`, …) and the **slots it consumes** (`source mailbox`, `message filter`,
   `extraction template`, `guardrails`, `target objects`…). The compiler checks every executor
   exists in the registry (`build_registry()`), and refuses to publish if a required slot is
   empty (`unfilled_slots`).
3. **The one model step.** Extract is not an executor: it is a model step with agent id
   `<use_case_id>__extraction` and role `extraction`. The role decides the model tier
   (`model_tier_for` → frontier); no hand-written routing row per use case.
4. **Result:** a `CompiledWorkflow` — `config.steps`, `parameters` (the slot values, with
   their origin: spec, default, or derived), `model_steps`, `publishable`.

**"How it will run"** shows this in two views — the pipeline (phases Intake / Understand /
Check / Write / Report) and the orchestration canvas (trigger → 11 nodes with their
executors, inputs and branch edges). The browser draws it from `planFor(spec)`, a mirror of
the compiler's stage table; the platform answers the same from `GET /use-cases/{id}/plan`
(`plan_from_config`, the R9 planner). The same views are on a built use case's page under
**How it runs**. The person approves; nothing here is editable, because a plan edited by hand
stops matching the spec.

---

## Step 5 — Rehearse: the real run with the side effects held

**Who:** platform service → Agno → executors + extraction agent; person reads the verdicts.

Three sources on the rehearse screen: **From the mailbox** (tick real messages, read
headers-only, nothing flagged), **Upload documents** (PDFs with a "treated as sent from"
address and expected values keyed in), **Generated samples** (`documents/golden.py` invents
cases from the template and the org's master data: clean ones, one per enabled guardrail,
an unplaceable item, a scanned copy — each with its expected outcome).

`POST /use-cases/{id}/runs?mode=rehearse` → the service:

1. runs the preflight in rehearse mode (must at least compile),
2. **compiles** the spec and stores the snapshot on the row,
3. inserts a run row (`retail_use_case_runs`: `queued`, mode `rehearse`, trigger `manual`),
4. builds the run options with **every side effect held**: the picked messages / cases are
   *injected* as `additional_data["messages"]` instead of polled; `dry_run=True` so
   `write_to_target` previews the record and `send_email` composes but does not send,
5. executes exactly as in step 8, then **scores each golden case** (`score_case`: outcome
   first, then each expected header value, then the line count) and stores the verdicts on
   the case rows.

*"Accept as expected"* copies a run's actual output onto the case as its expected values —
the way a golden record is authored without typing. A later material spec change re-runs them
(step 2).

---

## Step 6 — Activate: the version is frozen

**Who:** platform service → store.

`POST /use-cases/{id}/activate`:

1. preflight in **live** mode must pass (credentials must resolve, model configured, no
   failing golden case),
2. `compile_workflow` runs once more; the result is the **activated snapshot**,
3. the row is updated: `status=activated`, `version+1`, `compiled=<snapshot>`,
   `workflow_component_id="usecase__<id>"`, schedule normalised (`every_5_minutes` → 300 s).

From here on, **live runs execute the activated snapshot**, not the editable spec: an edit
on the form changes what the readiness screen probes, but not what runs, until the use case
is activated again (new version). *Pause* flips the status; *Resume* re-activates.

No Agno object is written to a database at activation. The workflow, its steps and the
extraction agent are **rebuilt from the compiled config at the start of every run** — the
snapshot is the source of truth, and the component id is what the run's provenance and
the plan view name. (In the cloud deployment the orchestrator — a Logic App on Azure, a
Step Functions task on AWS — calls the same run endpoint on the cadence; nothing below
changes.)

---

## Step 7 — The trigger: the poller decides a run is due

**Who:** poller (in-process, started by `scripts/run_local_api.sh`).

`UseCasePoller.tick()` every 30 s: for each **activated** use case, look up its cadence in
`SCHEDULE_SECONDS`, compare with the last run's `started_at`; when due, call
`start_run(use_case_id, mode="live", trigger="schedule")` and launch it in the background.
"Run now" on the use case page is the same call with trigger `manual`.

`EGPT_RETAIL_SCHEDULE_TRIGGER=orchestrator` tells the Monitor that the cloud orchestrator,
not the poller, is what fires runs.

---

## Step 8 — The run: Agno drives the eleven steps

**Who:** platform service builds it; Agno runs it; executors and one agent do the work.

`UseCaseService.execute(run_id)`:

1. **Build the workflow** — `workflow_from_compiled(compiled)` creates an Agno
   `Workflow` whose `steps` are `Step(name, executor=<registered function>)` for every
   executor stage, and for the Extract stage a `Step` that owns an **`Agent`** built by
   `build_extraction_agent`: model resolved by tier, `instructions` written from the
   template ("fill exactly these fields; null when the document does not show it"), and
   `output_schema` generated from the template's field list.
2. **Resolve the parameters** — `run_parameters(compiled.parameters)` expands each source
   reference into a connection with its credentials read from the environment
   (`credentialRef: env:gmail` → user + app password), normalises `master data` to the
   loader's role names, and hands the lot to the workflow as `additional_data`.
3. **`await workflow.arun(additional_data=...)`** — Agno calls each step in order, passing
   the previous step's content forward. A step may say `stop=True` (nothing left to do —
   e.g. no messages), and Agno ends the run early. Every step's output is recorded.

What each step does (the batch shape is `{documents, exceptions, stats}`; a document that
fails a stage moves to `exceptions` with a class and a reason, and the rest carry on):

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | `receive_messages` | executor | IMAP search with the message filter; skips ids a previous succeeded live run already carried (the ledger is the runs table); never flags or deletes. |
| 2 | `screen_documents` | executor | Keeps messages with a readable PDF; a scanned copy with no text layer is `SCANNED_DOCUMENT` — refused *before* a model is called. |
| 3 | Extract | **AI agent** | One call per document: PDF text → the template's fields. Output validated deterministically (`validate_extraction`): required fields present, no invented fields, quantities positive. |
| 4 | `resolve_entities` | executor | Scored matching against master data for **only the roles this use case needs**: sender account (email domain → contact → name), then ship-to (if the mapping reads one), then each line's product (if it reads one). Below threshold → exception with the closest candidate and its score. |
| 5 | `apply_hard_limits` | executor | The enabled guardrails as pure checks over the resolved document and history (duplicate reference, unknown sender, restricted item, first-time item, quantity outlier, date window…). History is the Order's items for an Order target, the target's own records otherwise. |
| 6 | `build_record` | executor | The mapping interpreter fills the target object(s) from source expressions (`customer.id`, `line.quantity`, `$pricebook_id`, `=Draft`); a required field it cannot fill is `RECORD_INCOMPLETE`, never a blank. |
| 7 | `apply_per_run_cap` | executor | Holds anything beyond the cap (`PER_RUN_CAP`), in arrival order. |
| 8 | `write_to_target` | executor | Salesforce REST: create the parent, each child with the parent id, then set the status (`Draft`). Under `dry_run` it previews instead. |
| 9 | `record_rollback_point` | executor | One row per created record with how to undo it (delete). |
| 10 | `send_email` | executor | The run summary and every exception, by class, to the recipient through the mailbox's SMTP. |
| 11 | `record_provenance` | executor | The provenance row (`egpt_answer_provenance`): what ran, which tier, what it read. |

4. **Finish the run row** — `status=succeeded|failed`, `result` (the final batch: documents
   with their record, written id and URL; exceptions with reference, class, stage, detail;
   `reference_field`, `roles_needed`), `summary` ("2 received, 2 created, 0 exception(s)"),
   `provenance_ref`. A rehearsal also stores the golden verdicts.

---

## Step 9 — What the person sees afterwards

**Who:** browser, reading the platform.

- **Use case page** (`/use-cases/platform/:id`) — status, version, last run, *Run now*,
  *Pause*, *Edit*, **Runs** (every run with its result: reference, outcome, the record's
  fields, the Salesforce link) and **How it runs** (the plan from the stored spec).
- **User built use cases** — only what `GET /use-cases` holds.
- **Monitor** — `GET /monitoring/execution-health` from the runs table: cadence, what fires
  it (poller or orchestrator), last run, next expected, 7-day counts, and a *Silent* verdict
  when one and a half cadences pass with nothing fired.
- **The mailbox** — the summary email per run; the source messages untouched.
- **Salesforce** — the records, linked from each run row.

---

## The same steps for a second document kind

For **Return request → Case** nothing above changes. The person picks the `return_request`
template, suggests a mapping onto `Case` (`AccountId ← customer.id`, `ContactId ←
contact.id`, `Subject ← rma_reference`, `Description ← reason`), turns on *Sender known* and
*Duplicate rma reference*, binds the sender account and contact. The compiler emits the same
eleven steps; `resolve_entities` resolves only the account and the contact — no ship-to, no
product — because that is all the mapping reads; the history guardrail reads Cases; the
results say "Case RR-1001" instead of "PO". `cookbook/94_retail/PA03_return_request_cases.py`
runs exactly this, offline and against the org.

---

## Where things live

| | |
|---|---|
| Form, reader, assistant, plan views | `frontend/src/domain/use-case-*.js`, `frontend/src/components/usecase/` |
| Templates, guardrails, mappings, roles as data | `product/egpt_retail/documents/{templates,guardrails,mapping,roles}.py` |
| Matching, mailbox, PDF reader, Salesforce client | `product/egpt_retail/documents/{matching,mailbox,reader,salesforce}.py` |
| Compiler and planner | `product/egpt_retail/orchestration/compiler.py`, `executors/plan.py` |
| Executors | `product/egpt_retail/executors/{documents,act,provenance}.py`, registry in `app/components.py` |
| Platform service, runtime, poller, store | `product/egpt_retail/usecases/` |
| API routes and contract | `product/egpt_retail/api/routers/{use_cases,extraction_templates,targets,monitoring}.py`, `api/openapi/` |
| Evidence | `cookbook/94_retail/TEST_LOG.md` (K15–K21), `PA02`, `PA03` |
