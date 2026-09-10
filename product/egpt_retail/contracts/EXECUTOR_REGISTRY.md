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
| `read_gold` | Lane R (R5) | Reads `gold.kpi_<identifier>` for the requested KPIs at `as_of` (default: each KPI's latest materialised date) through the guard and, when configured, the read-only role; notes datasets, statements and freshness in R7's bag | Nothing. A KPI with no gold at that date is listed under `missing` with why, never invented |
| `assess_kpi_feasibility` | Lane K (K11) | Feasibility for every registered KPI against the current semantic layer: `source_tables - available_tables`, computed at read time and never stored. No model, no client, nothing to be persuaded by | Never. A blocked KPI is an answer, not a failure — the step returns counts and rows and lets the caller decide |
| `detect_business_areas` | Lane K (K9) | Which areas this deployment can answer about: every area the shipped ontology covers, each marked supported when a confirmed mapping backs it. A graph query over accepted `INSTANTIATES` edges | Never. An unsupported area is the honest answer during onboarding, and the step returns the unsupported list precisely so a screen can show what connecting one more source would buy |

Note the shape of those six: five measure and one decides. That is deliberate and worth
copying. The alignment rule wants one clearly-named thing doing the blocking, which is easier
to review than three that each might; and if every gate stopped on its own failure, an
operator with two problems on one night would fix them one at a time over two nights, because
the second gate would never have run.

`check_freshness` — this document's own worked example of a silent collision — is deliberately
still unused.

Lane R's R7 executor library landed next: `check_preflight`, `record_provenance`,
`deliver_in_app` above. "Dry run" is not a fourth executor -- it is a flag
(`additional_data["dry_run"]`) `deliver_in_app` alone reads; every other step in a compiled
plan runs for real regardless. `read_gold` and `sample_rows`, named alongside R7's three in
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
