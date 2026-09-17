# Executor registry

*P0 deliverable. Authored jointly by Lane K and Lane R. A convention, not a contract.*

The named deterministic steps a workflow may compose. The delivery plan is explicit that
trying to *finish* this in P0 is the mistake — the list grows through P4. What P0 fixes is
the signature shape, the naming rule, and the check that stops the one failure mode this
mechanism has.

---

## Why it needs a rule at all

Executors are resolved **by the Python function's `__name__`**, and the namespace is flat.

`Step.to_dict` (`workflow/step.py:298`) stores:

```python
result["executor_ref"] = self.executor.__name__
```

`Step.from_dict` (`workflow/step.py:413`) resolves it:

```python
executor = registry.get_function(config["executor_ref"])
```

And `Registry.get_function` (`registry/registry.py:366`) is:

```python
return next((f for f in self.functions if f.__name__ == name), None)
```

First match by name, over a plain list. Two consequences, both verified:

**A duplicate name resolves silently to whichever was registered first.** Two functions
called `check_freshness`, one authored in each lane, produce no warning at registration and
no warning at resolution — the workflow simply runs the other lane's step. This is worth
contrasting with the *tool* lookup a few lines up in the same file, which explicitly warns
that "multiple distinct tools share the name". `get_function` has no such guard.

**Renaming a Python function is a breaking change.** Every stored workflow holds the old
name as a string. Renaming it means `get_function` returns `None`, and `Step` construction
then fails with "must have one executor" — loudly, which is the good case, but it fails for
every stored workflow at once.

So the namespace is global, silent on collision, and cemented into stored data. That is
exactly the kind of thing two people building in parallel get wrong once and then debug for
a day.

---

## The signature shape

```python
def <verb>_<object>(step_input: StepInput) -> StepOutput:
    ...
```

`StepExecutor` (`workflow/step.py:88`) also permits async and streaming forms, and the engine
detects them at runtime (`inspect.iscoroutinefunction`, `inspect.isasyncgenfunction`).

**One executor, one name.** Where an executor does I/O, author it as `async def` under the
same name. Do **not** author a sync and an async twin.

This is a deliberate departure from the repository's standing sync/async rule, and the reason
is the flat namespace above. That rule exists for public API where a *caller* picks a variant.
An executor has exactly one caller — the workflow engine — which handles both forms already,
so publishing `check_freshness` and `acheck_freshness` would double a namespace that cannot
survive ambiguity, to give a choice nobody makes. Contracts and providers still follow the
standing rule; this exemption is executors only.

---

## The naming rule

- **`<verb>_<object>`, snake_case.** `check_freshness`, `record_provenance`, `evaluate_alert`.
- **Globally unique across both lanes.** Not prefixed by lane: a lane prefix would be baked
  into stored workflows forever and would have to survive someone changing lanes. Uniqueness
  is enforced by the check below instead.
- **The name is the API.** Renaming one is a breaking change under the change protocol: both
  lanes agree first, and someone migrates the stored workflows. The check below cannot
  protect you here: `check_registry` inspects live functions and cannot see the
  `executor_ref` strings already persisted in stored workflows, so a rename passes the check
  and breaks every persisted workflow at once. *(Amendment by Lane R, 2026-09-07, on
  accepting the convention.)*
- **Verbs are specific.** `process_data` and `handle_step` are names two people will both
  reach for. Whatever it actually does, say that.

## What may be an executor

Anything deterministic, and — from the alignment rules — **everything that permits or blocks**.
Preflight checks, the SQL guard, feasibility, gap checks, conformance checks, alert
evaluation, delivery, the provenance record. The model explains and proposes; it is never the
thing that says yes.

An executor must not call a model. If it needs judgment, it is an agent step, and the
judgment belongs in a skill under the K14 template.

---

## Registering

Both lanes append to `Registry.functions`. Before the registry is handed to `EgptOS`, run:

```python
from egpt_retail.executors.registry import assert_registry_valid

assert_registry_valid(registry.functions)
```

It refuses duplicate names and non-conforming signatures, with the offending names in the
message. Wire it into the application factory next to K1's memory startup check, for the same
reason: both are misconfigurations that otherwise surface as a wrong answer rather than as a
failure.

---

## The list

Grows through P4. Each entry: name, owner, one line, and what it refuses.

| Name | Owner | Does | Refuses |
|---|---|---|---|
| `load_landing_files` | Lane R (R4) | Reads the raw drop out of object storage into `landing.raw_row`, as-is, and opens the load's ledger row | A path that does not conform to the landing contract, and an object outside `raw/`. A line that is not a JSON object, and an unknown CRM `record_type`, are reported as row defects rather than raised — one bad line must not cost a whole file |
| `conform_silver_entities` | Lane R (R4) | Runs the five conformance statements and records the per-stream counts | Nothing. It reports; the gates weigh |
| `check_row_count_delta` | Lane R (R4) | Measures landed rows against the previous **successful** load, and checks silver did not shrink | Nothing. Measures and persists to `quality.gate_result` |
| `check_key_null_rates` | Lane R (R4) | Measures missing, blank or uncoercible key and watermark columns per stream | Nothing. Measures and persists |
| `check_referential_integrity` | Lane R (R4) | Counts orphans per declared edge over silver | Nothing. Measures and persists |
| `decide_load_verdict` | Lane R (R4) | Aggregates every gate result, stamps `quality.load_run.verdict` and its one-paragraph reason | **Gold.** The only R4 executor that blocks: returns `stop=True`, which breaks the step loop before a gold step (R5) can run |
| `check_preflight` | Lane R (R7) | Assesses every KPI a use case references via `egpt_retail.contracts.kpi.assess_feasibility` (a pure set difference). `tables_exposed` and `guard_role` remain honest placeholders (`passed: true`, naming what is unbuilt); **`gold_fresh` is now real** — R5 landed, so it reads `gold.kpi_run` freshness and can fail | **Any run whose `kpis_ready` check fails.** Returns `stop=True`, breaking the chain before a compiled plan's later steps (`read_gold`, `sample_rows`, an explanation) run against a KPI that cannot be computed |
| `record_provenance` | Lane R (R7) | Writes one `egpt_answer_provenance` row per run (framework storage, `egpt.answer_provenance` — every deployment needs an answer it can trace), assembled from `additional_data["prov"]`. **Every executor in any lane that reads data or runs SQL on a path ending in `record_provenance` must populate this bag** via the exported helpers in `egpt_retail.executors.provenance`: `note_dataset_read`, `note_sql_execution` (accepts R6's `GuardDecision`), `note_row_sample`, `note_freshness`, `note_retrieval_hit` — a private per-package bag would hide exactly the data this step exists to collect | Nothing. Writes and returns `provenance_ref` |
| `deliver_in_app` | Lane R (R7) | Writes one `retail_deliveries` row (retail storage — "delivering in-app" is not a generic framework concern the way provenance is) | Nothing. Under `additional_data["dry_run"]=True` it records what it would have sent instead of sending it (`deliver_in_app.DRY_RUN_KEY`) |
| `materialise_gold` | Lane R (R5) | For every ready, scalar KPI in the registry: binds `{scope}` from the concept's accepted `sql_filter` and `{as_of}` to the run date, passes the assembled statement through R6's guard, compiles it (`LIMIT 0` in a savepoint), executes it into `gold.kpi_value` with `ON CONFLICT` on `(kpi_identifier, as_of, dimension)`, writes a `gold.kpi_run` row and the per-KPI view. Populates R7's bag with every source table, statement and freshness stamp | **A run after a blocked or failed load** (the last finished `quality.load_run` is not `passed`) and **a run while a load holds the advisory lock** -- `stop=True`, naming which. Never a KPI's own failure: that is a `failed` row in `gold.kpi_run`, next to the KPIs that materialised |
| `read_gold` | Lane R (R5) | Reads `gold.kpi_<identifier>` for the requested KPIs at `as_of` (default: each KPI's latest materialised date) through the guard and, when configured, the read-only role; notes datasets, statements and freshness in R7's bag. With `additional_data["history_days"]` it also returns each KPI's rows for the preceding business dates under `history` (same view, same guard) -- what R13's `series`/`delta` are read from; `additional_data["history_for"]` narrows that second read to a subset of `kpi_identifiers` (omitted = every identifier, unchanged) | Nothing. A KPI with no gold at that date is listed under `missing` with why, never invented; a history read that fails leaves `history` empty and logs, never drops the current value |
| `assess_kpi_feasibility` | Lane K (K11) | Feasibility for every registered KPI against the current semantic layer: `source_tables - available_tables`, computed at read time and never stored. No model, no client, nothing to be persuaded by | Never. A blocked KPI is an answer, not a failure — the step returns counts and rows and lets the caller decide |
| `detect_business_areas` | Lane K (K9) | Which areas this deployment can answer about: every area the shipped ontology covers, each marked supported when a confirmed mapping backs it. A graph query over accepted `INSTANTIATES` edges | Never. An unsupported area is the honest answer during onboarding, and the step returns the unsupported list precisely so a screen can show what connecting one more source would buy |
| `check_spec_gaps` | Lane R (R9) | The spec against the semantic layer and the registry: unknown/blocked/partial KPIs (with each missing table), unknown concepts, an unsupported area, no join path between a KPI's source tables, and the chosen autonomy shape's unfilled parameters (`missing_parameter`) -- set differences and lookups, no model | Nothing. A gap is the answer; `feasible` is false and every gap says what to connect or confirm |
| `derive_plan` | Lane R (R9) | The stored workflow component's config, walked recursively (`steps`, `else_steps`, `choices`), as the contract's `Plan`: executors with their metadata from `executors/metadata.py`, agents with their tier from R8's routing table | **A reference this deployment cannot resolve** -- an `executor_ref` `build_registry()` does not hold, an `agent_id` the routing table does not name -- since `POST /components` checks step types only and `Step.from_dict` would yield `None` at run time |
| `sample_rows` | Lane R (R10) | Between `read_gold` and the `funnel_explanation` agent step in the RFQ chain: per KPI, the immediately-prior period's per-brand values, ranked by size of change (`movers`), and rage/dead-click and script-error counts joined from `silver.session_replay_session` to `silver.analytics_event` on `identity_key` -- the only column both carry (`friction`). Populates R7's bag through `note_row_sample` | **A guard refusal**, `stop=True` -- the same mechanism `check_preflight` uses. A friction join that returns nothing is never a refusal: `friction.available=False` is the honest answer while the shared identity hash (build plan, "Blocked, and not by engineering") is unpopulated, and the agent step is told so rather than handed a guess |
| `check_explanation_grounding` | Lane R (R10) | Between the `funnel_explanation` agent step and `record_provenance` in the RFQ chain: re-runs `citations_within_input` and `movers_match_input` -- the schema's own deterministic checkers, previously cookbook/test-only -- against the agent's real output, reached by name (`sample-rows`) rather than through `previous_step_content` alone, since neither the movers input nor the explanation survives past the one step directly after it | **Either checker failing**, `stop=True` -- an edited `top_movers` row or a citation naming a brand or friction event absent from the agent's own input, breaking the chain before a hallucination reaches `deliver_in_app` |
| `evaluate_alert_condition` | Lane R (R12) | Compares one KPI's current gold value (read straight out of `read_gold`'s own `previous_step_content`, no separate compute step) to a definition's operator and threshold. Always returns content, fired or not -- "an alert that does not fire is still logged" | **The condition not being met**, `stop=True` -- breaks the chain before `write_alert`/`deliver_in_app` run for a quiet reading, the same mechanism `decide_load_verdict` uses |
| `write_alert` | Lane R (R12) | Writes one `alert_events` row in the contract's `AlertEvent` shape, only reached once `evaluate_alert_condition` did not stop the chain | Nothing directly -- called with a non-firing `previous_step_content` it is a documented no-op rather than a write |
| `evaluate_alerts` | Lane R (R12) | R12's own deliverable: every enabled `alert_definitions` row, evaluated against gold once per pass. Calls `read_gold`, `evaluate_alert_condition` and, when it fires, `write_alert` per definition, then delivers through `egpt_retail.delivery.awrite_delivery` -- the same function `deliver_in_app` calls -- once per recipient. Bumps `last_evaluated_at` on every definition it looks at, fired or not | A definition whose KPI is missing or no longer `scalar` is reported as unevaluable rather than raised, and still gets its `last_evaluated_at` bumped |
| `assess_execution_health` | Lane R (R14) | Per use case, reduces real `WorkflowSession` run history (`get_sessions(session_type=WORKFLOW, component_id=<workflow_id>)`), merged with the named schedule's real `ScheduleRun` history when it has one (the ledger that actually sees a hard step failure -- `arun()`'s own non-streaming path re-raises past the code that would persist a `WorkflowSession`), to last status, 7-day run/failure counts, approval gates paused past their wait threshold, token-cost drift against the prior 7 days, and whether it has gone silent | Never. Execution health is a report; `/monitoring/execution-health` decides nothing |
| `list_silent_schedules` | Lane R (R14) | Compares each named schedule's cron cadence (`expected_previous_fire`, independent of the schedule row's own mutable `next_run_at`/`enabled`) against its real `ScheduleRun` history and reports the ones with nothing fired -- any attempt, not only a successful one -- at or after their expected fire | Never. A disabled schedule with an elapsed window is reported exactly the same as an enabled one that crashed silently -- the point is catching both |
| `assess_quality` | Lane R (R14) | Per use case, the verdict from whichever of R15's `golden_pass_rate` and this package's `drift_score` (a deterministic distance between two `input_signature` row-sample fingerprints) are available -- a deterministic threshold over each, never a model's opinion | Never. `golden_pass_rate` is R15's number, passed through unmodified; a use case with neither signal reports `unknown` rather than a guess |
| `check_golden_gate` | Lane R (R15) | May an agent skill's prompt version be published: `golden_set_passed` (the measured pass rate from `egpt_retail.evals.golden.run_golden_set`, over a threshold) and `rehearsal_reviewed` (a person has approved *this* score in `egpt_retail.evals.rehearsal.RehearsalReviewStore`). Never calls a model itself -- the golden set's own run is where the model call happens, before this executor sees a number | **Either check failing**, `stop=True` -- a pass rate under threshold, no golden run at all, or a pending/rejected/absent rehearsal review, naming which |
| `receive_messages` | Lane K (K15) | The document-to-record template's Receive stage: new messages from the `source mailbox` slot matching the `message filter` slot, over IMAP with `BODY.PEEK`; the only write it can make to the mailbox is a `\Seen` flag, and only when `mark_seen` is set -- the platform's runs (K16) leave it off and remember message ids instead, so the mailbox is only ever read (never a delete, never a move); `additional_data["messages"]` hands a batch in directly for the rehearsal. Drops ids in `processed_ids` so a message is carried once | Never. An empty poll is content (`count: 0`), so a quiet hour is visibly quiet |
| `screen_documents` | Lane K (K15) | Deterministic gate before any model is called: exactly one PDF, under the size limit, not from an ignored domain, with a text layer -- read here once through `document_read`. Everything else is an exception on that message (`NO_DOCUMENT`, `MULTIPLE_DOCUMENTS`, `NOT_A_PDF`, `TOO_LARGE`, `SENDER_IGNORED`, `SCANNED_DOCUMENT`) | **No messages at all**, `stop=True`. A batch with exceptions and no documents goes on, so the Notify stage reports them -- the rule every branch below follows |
| `resolve_entities` | Lane K (K15) | Scored matching in a fixed order of attempts (`documents/matching.py`): customer by sender domain, contact email, then name; ship-to among that customer's sites by exact street, postcode plus street number, then address text; product by the customer's own part number, our code, then description against name and aliases. Master data from the `master data` slot's rows or loaded from Salesforce (`documents/salesforce.py`, noted through `note_dataset_read`). Merges a preceding model step's `{"extractions": [...]}` by message id and validates it against the extraction template | **Nothing left** (no documents, no exceptions), `stop=True`. A score under threshold or two candidates within the ambiguity margin is an exception carrying the closest candidate (`*_NOT_FOUND`, `*_AMBIGUOUS`), never a guess |
| `apply_hard_limits` | Lane K (K15, Act) | Document batch: the six record guardrails from the `guardrails` slot (`documents/guardrails.py`: duplicate PO including within this run, unknown sender, first-time product, quantity anomaly, due-date sanity, restricted product), evaluated in a fixed priority order so the most serious class heads a multi-failure. Proposal: each change's amount against the bound. A guardrail whose master-data role is not loaded is reported `skipped`, never passed | **Nothing left**, `stop=True`. A failing document is held with its guardrail's exception class; the rest of the batch goes on -- twelve orders with one bad due date create eleven |
| `build_record` | Lane K (K15) | Maps each resolved document onto the target's objects through the mapping the `target objects` slot names (`documents/mapping.py`; `salesforce/order_orderitem` today), created as the `created as` slot says | Never stops. A required field the mapping cannot fill is `RECORD_INCOMPLETE` naming the field, not a blank on the record |
| `apply_per_run_cap` | Lane K (K15, Act) | At most N writes this run from the `per-run cap` slot (`50`, `"50 records"`, `"10 SKUs"`); everything past the cap is held with `PER_RUN_CAP` in arrival order and reported, not dropped | **Nothing left**, `stop=True` |
| `write_to_target` | Lane K (K15, Act) | Creates each built record tree in the `write target` (Salesforce through client-credentials OAuth; parent, then children with the parent id, then the finishing status), or under `additional_data["dry_run"]` -- the same flag `deliver_in_app` reads -- marks each `previewed` and touches nothing. A target the platform cannot write is `NOT_CONNECTED` | Never stops. A failed create is `WRITE_FAILED` on that document and the rest still go; a partial run is reported as partial |
| `record_rollback_point` | Lane K (K15, Act) | One `retail_rollback_points` row per record created, with the reverse action the mapping declared (`delete` the Order; Salesforce cascades its items), persisted when `EGPT_RETAIL_DB_URL` is set and kept on the run's `additional_data["rollback_points"]` with `persisted: False` otherwise. The `rollback` slot's prose is recorded alongside; the reverse action comes from the mapping, not from prose | Never |
| `send_email` | Lane K (K15) | The run's created records and its exceptions grouped by class, to the `recipient` slot through the source mailbox's own SMTP; composed and returned unsent under dry run; nothing sent and said so when there is nothing to report | Never stops; `success=False` with `no_recipient` when the slot is empty, so a run with nobody to tell is a visible failure rather than a silent one |
| `compare_to_baseline` | Lane K (K23) | The Inform shape's comparison: each KPI at the run's `as_of` (the previous `read_gold` output, or gold directly) against gold at the baseline date -- the KPI's own declared window back for `prior_period`, 365 days for `prior_year` -- per KPI and per breakdown row, with deltas and the rows that moved most. A baseline date gold does not hold is materialised for it once through `materialise_gold` (`backfill_baseline`, default on), so a first run has a comparison | Only an empty KPI list. A KPI with no baseline is reported with `baseline: null` and the reason, never dropped |
| `check_report_grounding` | Lane K (K23) | Between the `explanation` model step and `render_report`: every figure in the narrative's headline, findings and caveats must be one the comparison carried (current, baseline, delta, delta percent, at whole, one- or two-decimal precision; dates and years excluded). A finding naming a KPI the comparison did not cover has its id repaired to the one it names, or blanked -- the id is a label, not a figure. A narrative that fails is withheld and recorded as `explanation_rejected`; the run continues with the numbers | Nothing stops the run: the report goes out either way, with or without the narrative |
| `render_report` | Lane K (K23) | The comparison and the grounded narrative as an email-safe HTML report (tiles for scalar KPIs, tables with bars for breakdowns, the dashboard link) and a plain-text twin; sets `headline`/`provenance_ref` for `deliver_in_app` | No comparison to render |
| `send_report` | Lane K (K23) | The HTML report to the `recipient` slot through the mailbox's SMTP as a multipart message (text + HTML); composed and returned unsent under dry run; no recipient is `no_recipient`, not a failure | Nothing sent when the mailbox refuses; the failure is on the run's `delivery` |

Note the shape of those six: five measure and one decides. That is deliberate and worth
copying. The alignment rule wants one clearly-named thing doing the blocking, which is easier
to review than three that each might; and if every gate stopped on its own failure, an
operator with two problems on one night would fix them one at a time over two nights, because
the second gate would never have run.

`check_freshness` — this document's own worked example of a silent collision — is deliberately
still unused.

Lane R's R7 executor library landed next: `check_preflight`, `record_provenance`,
`deliver_in_app` above. "Dry run" is not a fourth executor -- it is a flag
(`additional_data["dry_run"]`) that only the steps with a side effect read: `deliver_in_app`,
and since K15's write-side executors landed, `write_to_target` and `send_email`; every other
step in a compiled plan runs for real regardless. `read_gold` and `sample_rows`, named alongside R7's three in
the API contract's own example compiled plan, belong to R5 and R10 respectively, not R7.
`GET /runs/{run_id}/provenance`'s router is not built by R7 either -- no FastAPI router for
`/use-cases`/`/playground`/`/runs` exists anywhere yet; R11 (the playground's citation panel)
is the natural owner, and needs only `egpt.answer_provenance.aread_provenance` to wire it.
Lane K's first are feasibility (K11) and business-area detection (K9), both already
deterministic by design.

Add a row when you add an executor. A registry nobody updates is one people stop reading, and
then two lanes name the same step twice — which is the failure this whole document exists to
prevent.

---

## Verification

`cookbook/94_retail/K00_executor_registry.py` demonstrates the collision against the real
`Registry`, then shows the check catching it.
