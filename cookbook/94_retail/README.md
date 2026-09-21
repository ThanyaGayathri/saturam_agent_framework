# 94 · Retail (Enterprise GPT)

Runnable checks for the Lane K and Lane C contracts in `product/egpt_retail/contracts/` and
Lane R's API contract in `product/egpt_retail/api/`. One folder, one `TEST_LOG.md`, entries
tagged by package.

These are product examples, not framework examples. They import from `product/`, which is
outside the published `egpt` wheel, so each script puts `product/` on `sys.path` itself.

| Script | Package | What it proves |
|---|---|---|
| `C01_landing_contract.py` | C1 | The landing contract, by playing Lane R's loader against three hand-written fixture files: loads all three sources with no source-specific code, reports a known-dirty row instead of dropping it, and the re-run dedup rule collapses a duplicate and resolves that row's defect. |
| `C01_minio_roundtrip.sh` | C1 (object storage) | The same fixtures, uploaded to and downloaded back from a real MinIO bucket, then re-verified with `C01_landing_contract.py` unmodified -- proves the verify step's "upload it" against actual object storage, not just local disk. |
| `K11_kpi_formula_shape.py` | K11 | The KPI formula shape against nineteen real formulas: what fits, what has to be rewritten, and that feasibility is a set difference. |
| `K08_proposers.py` | K8 | The enrichment and mapping proposers drafting against the real harvest, and the graph proving it does not change. Needs Azure, ontology, warehouse and Neo4j. |
| `K09_business_areas.py` | K9 | Which areas a deployment supports: they exist before any source, and a proposal alone supports nothing. Needs a warehouse and Neo4j. |
| `K10_unified_retrieval.py` | K10 | One question across memory, prose, the ontology and the semantic layer, fused by rank; disable a leg and watch the answer degrade. Needs prose, ontology, a warehouse and Neo4j. |
| `K11_kpi_registry_and_feasibility.py` | K11 | The registry in Postgres, mirrored to the graph, and feasibility as a set difference: remove a table and watch one KPI block while another goes partial. Needs a warehouse and Neo4j. |
| `K12_kpi_suggestion.py` | K12 | Candidate KPIs proposed from the connected data, with a feasibility the agent's schema cannot express. Three runs, a stable core, and `K12_BASELINE.md`. Needs Azure, a warehouse and Neo4j. |
| `K13_use_case_suggestion.py` | K13 | Use case candidates across all four shapes, with the shape gated by what a deployment has configured rather than by the model. Two deployment profiles, a control that promotes a real draft to `act`, and `K13_BASELINE.md`. Needs Azure, a warehouse and Neo4j. |
| `K13_suggest_on_connect.py` | K12+K13 | `egpt-retail suggest`, live: both agents through R8's routing with the harvest, ontology and K10 prose; surviving suggestions filed as K7 proposals for a person to adopt; dry run, filing run, idempotent second run; rows removed at exit. Needs Azure, Neo4j, the loaded corpus and the local embedder. |
| `K15_orchestration.py` | K15 | A frozen spec compiled into a workflow definition: the shape fixes the steps, a template fixes the values, and every parameter records where it came from. Diffs against R9's hand-built skeleton and is checked by R9's plan walker. `K15_BASELINE.md`. Needs nothing -- no warehouse, no Neo4j, no model. |
| `K00_executor_registry.py` | — | The executor naming convention: the silent collision it prevents, and the check that enforces it. |
| `K01_memory_scope_convention.py` | K1 | The memory scope convention against the real `Scope`: write scopes, read scopes, and the isolation matrix. |
| `K02_domain_prose.py` | K2 | The curated retail corpus into pgvector, split on headings and tagged by origin. Needs pgvector + the corpus. |
| `K03_ontology.py` | K3 | The shipped retail ontology: synonyms and plain language reaching concepts by meaning, and an overlay surviving a core upgrade. Needs Neo4j. |
| `K04_metadata_harvest.py` | K4 | The metadata harvest against R4's silver schema: types, keys, comments, and column-name collisions. Needs a warehouse. |
| `K05_semantic_graph.py` | K5 | The real semantic graph store: harvest in, contract out, G20's authoritative source, and promotion rebuilt from the graph. |
| `K06_snapshots_and_drift.py` | K6 | Versioned harvests, the diff between two, and the KPIs a schema change moves into review. Needs a warehouse. |
| `K06_refresh_entrypoint.py` | K6 | `refresh_semantic_layer` and `egpt-retail harvest`: warehouse to snapshot to graph under one version, drift reported, the real CLI run end to end. |
| `K07_proposals_and_review.py` | K7 | The proposals table and the review between an agent and the graph: twenty proposed, twelve accepted, eight rejected and absent. Needs a warehouse and Neo4j. |
| `K05_semantic_read_api.py` | K5 | The semantic read API stub: unloaded behaviour, proposal visibility, join paths, and the seam to feasibility. |
| `K14_skill_template.py` | K14 | The skill template, by running an agent written against it: closed sets agree, output conforms, refusal is schema-shaped. |
| `K14_join_path_explainer.py` | K14 (second half) | The template's other half: an agent Lane R wrote from the template alone. Verdict mirrors a deterministic input, twice; a null path invents nothing; a missing input refuses as `unknown`. |
| `R03_build_contract_fixture.py` | R3 | The contract fixture the stub serves, derived from the shipped ontology and the vendored KPI corpus: generated not edited, and every behaviour the contract's examples claim is live in it. |
| `R03_stub_server.py` | R3 | The API contract, running: every operation mounted, every one returning its declared example, and the example selector the empty and populated screens depend on. |
| `R03_semantic_over_http.py` | R3 | The semantic read API over HTTP: seven reads live from the fixture, three join-path outcomes distinct on the wire, `include_proposed` scope-gated. |
| `R01_local_stack.py` | R1 | The API entrypoint against a real Postgres: `/health` 200, `/config` lists the registered agent and the control-plane database. In-process by default; `EGPT_RETAIL_URL` for a running server. |
| `R01_consolidator_no_work.py` | R1 | The consolidator's clean no-work exit in both shapes (slug unset; slug set with a real vector-only provider) and the single-instance refusal via the advisory lock. |
| `R02_cluster_evidence.sh` | R2 | The four cluster scenarios on kind, end to end in `kubectl` and `helm`: infra chart, offline RS256 tokens, app chart with the migrate hook, 200/403/open `/health`, pod kill under an in-cluster request loop, consolidator scale refusal, render guard. Shell on purpose -- wrapping `kubectl` in `subprocess.run` to satisfy a file extension makes it harder to rerun by hand. |
| `R02_wheel_contents.py` | R2 | Builds the `egpt` wheel and lists its members: nothing from `product/`, `frontend/` or `deploy/`. The regression guard CI runs after `python -m build`. |
| `R04_landing_to_silver.py` | R4 | The four Verify scenarios against a real warehouse: load the sample files, load them again, null a key column, and load a fraction the size of the previous pull. |
| `R04_conformance_sql.py` | R4 | The conformance specification per entity, printed beside the contract rules it implements. No database. |
| `R04_minio_load.sh` | R4 | The same four scenarios loading from a real S3 bucket rather than a directory -- the half a local path cannot prove. |
| `R03_prototype_screen.md` | R3 | The other half of R3's verification -- a prototype screen rendered from the stub. A procedure run in the prototype repository, with what to record. |
| `R06_sql_guard_parsing.py` | R6 | The SQL guard's classification rules: reads pass, every write kind refuses, a CTE alias is not mistaken for a table, a function call in table position is refused, stacked statements refuse before either half is inspected, an unresolved template placeholder is caught explicitly, and the row cap overrides whatever is requested. No database, no model. |
| `R06_sql_guard_live.py` | R6 | The same guard against the real warehouse: delete refused with the row count unchanged, an out-of-scope read refused, an unbounded read capped, a real Postgres statement timeout enforced -- then the chat-interface half (an agent, a prompt-injection paste) once model credentials are sourced. |
| `R07_executor_library.py` | R7 | A throwaway workflow of `check_preflight` -> `record_provenance` -> `deliver_in_app`, no agent step, driven only by `.arun()`: completes with no model, the provenance record is readable back by `run_id`, and a second run with the dry-run flag shows delivery previewing instead of sending. |
| `R07_preflight_blocked_kpi.py` | R7 | Preflight against a use case referencing a blocked KPI, reproducing the API contract's own worked example: blocks and names the missing table, then passes once it is exposed. No database, no model. |
| `R05_gold_materialisation.py` | R5 | The Verify line against a real warehouse: the nightly chain by hand (R4's six steps, then gold) over K11's six definitions -- two materialise, three fail at compile with Postgres's own words in `gold.kpi_run`, one is skipped as blocked; gold versus the same number in SQL, and the stamp on every row; one good formula broken deliberately and failing alone while the other still materialises; `read_gold` through the guard; a blocked night as a COMPLETED run with no gold step and gold untouched; then `schedule-nightly` and a trigger through `ScheduleExecutor` against the in-process pipelines app, whose `additional_data` arrives as a dict. |
| `R05_gold_health.py` | R5 | The contract's `KpiHealth` assembled in Python from the registry and gold: all five statuses reachable, every row validated against the generated model, and `gold_fresh` as preflight computes it. No services. |
| `R08_model_routing.py` | R8 | The routing table covers every registered agent (an unrouted one refuses assembly by name); offline, an input over the small tier's declared context window escalates *before* the small tier is resolved and the reason lands in `RunOutput.metadata["egpt_escalation"]`; live, `join_path_explainer` on both tiers with `RunOutput.model` naming each deployment, then a deliberately small declared window forcing a real pre-call escalation. |
| `R09_spec_gap_plan.py` | R9 | The three sentences of the Verify line -- plain, one naming a metric that does not exist, one ambiguous about acting -- as hand-written correct specs through the deterministic gap check over the contract fixture (feasible, nothing coined; a coined near-miss becomes gaps), then live through the spec extractor on the small tier with every closed-set value checked against the lists rendered into its prompt: the unknown metric is a question, the action verb is a question. |
| `R09_plan_view.py` | R9 | The plan as a view: an Inform-shape workflow stored as `Workflow.to_dict()`, derived and compared ref for ref; a `Condition` with `else_steps` and a `Router` with choices still compared; an executor nobody registered refused by name; and, with the control plane up, the same plan derived by `derive_plan` from the stored component row. |
| `R10_rfq_workflow.py` | R10 | The RFQ chain end to end against a real warehouse and semantic layer: a real mover forced by hand-editing a prior gold value, `sample_rows`'s movers checked against direct SQL, friction honestly unavailable over the sample data then made available by populating `identity_key` on two rows and unavailable again once it is removed; then the registered `rfq-funnel` workflow driven with `arun`, live when Azure credentials are configured and against a hand-built explanation otherwise. |
| `R12_alerts.py` | R12 | Alert definitions and evaluation against a real warehouse and control plane: a rowset KPI refused at creation (`kpi_not_scalar`, 422); a threshold set below a real materialised gold value fires through the registered `alert-evaluation` workflow (`evaluate_alerts` then `record_provenance`), writes one `alert_events` row and delivers through R7's own `retail_deliveries` table; the threshold raised above the value stays silent -- no new event, no delivery -- but `last_evaluated_at` still advances and its own provenance record still exists, so a quiet week is legible; then the same two cases again through `evaluate_alert_condition`/`write_alert` called directly with `read_gold`'s own output. Round-2 review, PR #210: re-evaluating the same still-breaching definition for the same window a second time writes no second `alert_events` row and pages recipients once, not twice; a malformed recipient on one definition never stops the definition after it from evaluating and firing; `AlertDefinitionStore.update` re-validates and refuses retargeting to a rowset KPI, and the threshold-edit scenario goes through it rather than raw SQL. |
| `R14_monitoring.py` | R14 | Execution health from real run history, plus the input-drift half of the quality assessment (golden pass rate is R15's): `nightly-refresh` driven to two real successes then a real lock-contention failure, recorded as a real `ScheduleRun` the same way the poller itself would (no HTTP router is wired up yet); a real schedule whose cadence's window closed long ago flagged silent, and flagged the same way once disabled; a real mover's row hand-degraded while the delivered explanation text stays byte-for-byte the same, flagged by `assess_quality` on drift alone. |
| `R15_golden_sets.py` | R15 | A golden question set for every real agent skill in `product/egpt_retail/skills/` (K8's two proposers, K12, K13, R9's spec extractor, R10's explanation, R11's playground, plus K14's own `synonym_proposer`/`join_path_explainer`), scored live and each recording a rehearsal-review row; `check_golden_gate` blocking an unreviewed score and allowing a reviewed, passing one; then the Verify scenario -- `join_path_explainer`'s prompt deliberately weakened (the exact rule against inventing a join for a null path), republished, the score drop and the gate's block both observed, then the prompt restored and the score confirmed recovered. Live against Azure when configured, otherwise a local Ollama model -- a real model call either way, never a mock. |
| `P0_data_source_registration.py` | #304 (self-serve data sources, part of #302) | A user-registered data source against a real control-plane Postgres: the returned `DataSource` carries no credential field, the stored ciphertext does not contain the plaintext password, `SecretStore.read()` recovers the exact credential written, rotating a credential deletes the old secret and makes it unreadable, and deleting a source deletes its secret too. |
| `P1_sources_router.py` | #305 (self-serve data sources, part of #302) | The real `/configure/sources` router (`InternalSource`), against the real store and real Postgres: create/get/list/patch/delete over real HTTP with no credential ever in a response body, and `POST .../test` refusing a real loopback host before attempting a connection -- proving the SSRF guard fires end to end, not just at the unit level. Also a username-only `PATCH`, unresolvable and malformed hosts returning results rather than 500s, and (with `P1_SOURCE_HOST`) a real successful test-connection through the pinned address. |
| `P2_harvest_source.py` | #306 (self-serve data sources, part of #302) | The `harvest-source` workflow step against a real warehouse schema and a real Neo4j graph: `harvest_schema` reads real tables, `write_harvest` tags the real node with `source_id` (verified by a direct read), `write_harvest` itself refuses a source_id claiming `silver` (already held by the operator's own harvest) and rolls the write back, and the real `harvest_source()` entry point refuses a real loopback host end to end, moving the source to `harvest_state=error`. |
| `P3_source_scoping.py` | #307 (self-serve data sources, part of #302) | A registered source's tables and the operator's share one graph without seeing each other, over real HTTP against real Postgres and Neo4j: `GET /semantic/tables` never lists the source's tables and a bare `crm_contact` still resolves to silver, `?source_id=` lists only the source's with its own snapshot, an unregistered id is a 404, the operator's snapshot pointer survives the source's harvest, and the source's read guard allows its own table and refuses silver's. Needs a Neo4j already holding the silver harvest. |
| `P4_frontend_connect_flow.mjs` | #308 (self-serve data sources, part of #302) | The real Configure > Data sources screens driven in a headless browser (Playwright) against the real API, Postgres and Neo4j: register a source from the form, "Test Connection" shows the API's verdict, Save starts the `harvest-source` run (202) and the card moves Building -> Modelled by polling `GET /configure/sources`, the semantic-layer screen lists the harvested tables via `GET /semantic/tables?source_id=`; then the loopback refusal, a failed harvest with Retry, a 422 shown as written, Remove, and "Save Changes" on a saved source (a rename, a username edit refused without the password, username + password persisting). Any unexpected console error or API response >= 400 fails the run (exit 1). Node + Playwright, not the demo venv; see its header. |
| `P5_self_serve_source_end_to_end.py` | #309 (self-serve data sources, part of #302) | The whole pipeline in one sitting, over HTTP against real Postgres and Neo4j: register (`POST /configure/sources`) -> test (`POST .../test`) -> harvest (the `harvest-source` workflow's executor) -> read back (`GET /semantic/tables?source_id=`) -> re-harvest from the shell (`egpt-retail harvest --source-id`, new here) -> remove. The refusal path (a loopback host, refused at test, harvest and listing) always runs; the success path runs when a Postgres answers on a non-loopback address (the LAN IP for the Docker one, auto-detected; `EGPT_P5_SOURCE_HOST` overrides) and includes a rival source stopping at `needs_review`. Its gated twin is `product/tests/test_data_sources_live.py`. |
| `P6_kpi_suggestions_after_harvest.mjs`, `P6_kpi_suggestion_failure.mjs` | part of #302 (contract 1.2.9) | What a connected source gives the customer once its harvest is over, in a real browser against the running API: KPI suggestions are proposed automatically after the harvest (the `suggest-kpis` workflow step) and the KPIs page shows them, saying so while the run works (there is no "Suggest KPIs" button and Add KPI is the manual form only); they survive a reload with no new run started; a dismissed suggestion stays gone after a reload and after a new run proposes it again; the harvested tables and their columns are in the Data catalog under their source. The second script covers a run that failed after a harvest (banner naming the source and reason, retry for that source) and that a source's suggestion says why it cannot be added yet. The model is replaced by `P6_replay_suggestions.json` (the suggestion replay mode). Its gated DB twin is `product/tests/test_kpi_suggestions_live.py`. |
| `P7_delete_source_cleanup.py`, `P7_needs_review_way_out.mjs` | part of #302 | Deleting a data source removes what its harvest left behind (its tables and columns and their edges in the semantic graph, its saved KPI suggestion runs, its row and its secret), so a new source can take the same schema -- it used to be refused by the collision guard on behalf of a source that no longer existed, and stopped in `needs_review` with no way out. The Python script runs the real router, executor, Postgres and Neo4j through five scenarios: the register/harvest/delete/register/harvest regression (`modelled`, not `needs_review`); a graph orphaned before the fix healing itself; a real holder still respected (and the message naming it); a graph that cannot be reached keeping the source (`503`, retry works); a never-harvested source deleting with the graph down. The browser script drives the Configure screen: the `needs_review` card's "Retry modelling", editing the schema and saving, and removing the holder then retrying. Both check every other registered source is untouched. Gated DB twin: `product/tests/test_source_delete_live.py`. |

Run:

```bash
.venv/bin/python cookbook/94_retail/C01_landing_contract.py
.venv/bin/python cookbook/94_retail/K11_kpi_formula_shape.py
```

The R3 scripts need `product/` installed into the venv (`./scripts/dev_setup.sh` does this) and
no services:

```bash
.venv/bin/python cookbook/94_retail/R03_build_contract_fixture.py
.venv/bin/python cookbook/94_retail/R03_stub_server.py
.venv/bin/python cookbook/94_retail/R03_semantic_over_http.py
```

The R1 scripts need a Postgres on 5532 (`docker compose -f deploy/compose.yaml up -d postgres`)
and no model key:

```bash
.venv/bin/python cookbook/94_retail/R01_local_stack.py
.venv/bin/python cookbook/94_retail/R01_consolidator_no_work.py
PYTHONPATH=product .venv/bin/python cookbook/94_retail/P0_data_source_registration.py
PYTHONPATH=product .venv/bin/python cookbook/94_retail/P1_sources_router.py

# R4 needs the same Postgres plus the egpt_warehouse database (created on first boot):
docker compose -f deploy/compose.yaml up -d postgres
EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \
  .venv/bin/python -m egpt_retail.entrypoints warehouse-migrate
.venv/bin/python cookbook/94_retail/R04_landing_to_silver.py
.venv/bin/python cookbook/94_retail/R04_conformance_sql.py   # no services
bash cookbook/94_retail/R04_minio_load.sh                    # needs Docker

# P2 needs the same Postgres plus egpt_warehouse (created just above) and Neo4j:
EGPT_RETAIL_NEO4J_PASSWORD=password \
  PYTHONPATH=product .venv/bin/python cookbook/94_retail/P2_harvest_source.py

# P3 needs the same, plus a Neo4j already holding the silver harvest (K06_refresh_entrypoint.py):
EGPT_RETAIL_NEO4J_PASSWORD=password \
  PYTHONPATH=product .venv/bin/python cookbook/94_retail/P3_source_scoping.py

  # P4 (#308): a browser check, not a Python cookbook. Needs the API on :7777, `npm run dev` on
  # :3000, a Postgres reachable on a NON-loopback address (the SSRF guard refuses localhost --
  # the machine's LAN IP works for the Docker Postgres) holding schema `p4_ui`, and Playwright.
  P4_HOST=<lan-ip> P4_OUT=/tmp/p4-shots node cookbook/94_retail/P4_frontend_connect_flow.mjs
  # (P4_BASE / P4_API name the dev server and API when they are not on :3000 / :7777)

  # P5 (#309): the whole pipeline; the success path needs a Postgres on a non-loopback address
  EGPT_RETAIL_NEO4J_PASSWORD=password PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/P5_self_serve_source_end_to_end.py

# R6 needs no services for the classification rules alone:
.venv/bin/python cookbook/94_retail/R06_sql_guard_parsing.py

# R6's live script needs the same Postgres and egpt_warehouse (warehouse-migrate above also
# creates the egpt_reader read-only role, R06_sql_guard_live.py's read-only connection) plus
# R04's data:
EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \
EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL=postgresql+psycopg://egpt_reader:egpt_reader@localhost:5532/egpt_warehouse \
  .venv/bin/python cookbook/94_retail/R06_sql_guard_live.py
# add `set -a && source azure_key.env && set +a` first to also run its chat-interface section

# R7's preflight scenario needs no services:
.venv/bin/python cookbook/94_retail/R07_preflight_blocked_kpi.py

# R7's executor-library workflow needs the same Postgres, with the control-plane database
# migrated (creates egpt_answer_provenance and retail_deliveries):
EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \
  .venv/bin/python -m egpt_retail.entrypoints migrate
EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \
  .venv/bin/python cookbook/94_retail/R07_executor_library.py

# R8's table and pre-call escalation need no services or credential:
.venv/bin/python cookbook/94_retail/R08_model_routing.py
# add `set -a && source azure_key.env && set +a` and EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT=<a
# second, cheaper deployment> to also run the two live sections
# R5's health cookbook needs no services:
.venv/bin/python cookbook/94_retail/R05_gold_health.py

# R5's materialisation needs Postgres (control plane migrated, egpt_warehouse migrated) and
# the compose stack's Neo4j; the script defaults every URL to the compose ports:
docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j
.venv/bin/python cookbook/94_retail/R05_gold_materialisation.py

# R9's gap check and plan view need no services (R09_plan_view's last section also stores
# the workflow in the control plane when EGPT_RETAIL_DB_URL is reachable):
.venv/bin/python cookbook/94_retail/R09_spec_gap_plan.py
.venv/bin/python cookbook/94_retail/R09_plan_view.py
# add azure_key.env and EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT to also run the live extraction

# R10 needs the same Postgres (control plane migrated, egpt_warehouse migrated) and Neo4j as
# R5; add azure_key.env to also run the funnel-explanation step's model call, otherwise the
# deterministic chain runs and the model step is reported as skipped:
docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j
.venv/bin/python cookbook/94_retail/R10_rfq_workflow.py

# R12 needs the same Postgres (control plane migrated -- migrate also applies
# alert_definitions/alert_events -- and egpt_warehouse migrated) and Neo4j as R5/R10, on this
# repo's standard compose ports (5532/7687) -- R12_alerts.py itself only defaults to the
# isolated worktree ports (5633/7692) a build-time convenience of this PR's own branch, so a
# reviewer running against the standard stack must export the standard URLs explicitly:
docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j
EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \
  .venv/bin/python -m egpt_retail.entrypoints migrate
EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \
EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \
EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \
EGPT_RETAIL_NEO4J_URI=bolt://localhost:7687 \
  .venv/bin/python cookbook/94_retail/R12_alerts.py

# R14 needs the same Postgres (control plane migrated, egpt_warehouse migrated) as R05/R10;
# no Neo4j-specific data, no model credential:
docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j
.venv/bin/python cookbook/94_retail/R14_monitoring.py

# R15 needs a Postgres for the rehearsal-review table (self-provisioning, like K7's
# ProposalStore -- any reachable Postgres works, this repo's compose one included) and a
# model: Azure if configured, otherwise a local Ollama model (`ollama pull qwen2.5-coder:7b`):
EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \
  .venv/bin/python -m egpt_retail.entrypoints migrate
EGPT_RETAIL_R15_DSN=postgresql://ai:ai@localhost:5532/ai \
  .venvs/demo/bin/python cookbook/94_retail/R15_golden_sets.py
# add `set -a && source azure_key.env && set +a` first to score against Azure instead

# R2 needs Docker, kind, helm and kubectl (brew install kind helm kubectl):
docker build -f deploy/Dockerfile -t egpt-retail:dev .
kind create cluster --name egpt --config deploy/kind/cluster.yaml
kind load docker-image egpt-retail:dev --name egpt
bash cookbook/94_retail/R02_cluster_evidence.sh
.venv/bin/python cookbook/94_retail/R02_wheel_contents.py
```

`C01_minio_roundtrip.sh` needs Docker (it starts `deploy/compose.yaml`'s `minio` service
itself, under the `full` profile, and tears nothing down when it's done -- `docker compose -f
deploy/compose.yaml --profile full down -v` cleans it up):

```bash
bash cookbook/94_retail/C01_minio_roundtrip.sh
```

To run the stub as a server -- for the prototype screen, or to check `R03_stub_server.py`
over a real socket with `EGPT_STUB_URL=http://127.0.0.1:7778`:

```bash
.venv/bin/python -m egpt_retail.api.stub
```

C01 and K11 need no services. C01's fixtures are hand-written at
`product/egpt_retail/contracts/_fixtures/landing/`; K11's corpus is vendored at
`product/egpt_retail/contracts/_fixtures/poc_kpis.json`.

K14 (both halves) calls a model, so it needs credentials:

```bash
set -a && source azure_key.env && set +a
.venv/bin/python cookbook/94_retail/K14_skill_template.py
.venv/bin/python cookbook/94_retail/K14_join_path_explainer.py
```

## Getting a real, non-empty `GET /kpis/suggestions` on a fresh local stack (#253)

`load-knowledge` alone is not enough. `kpi_suggestion`/K9 only see a table as usable once it is
*mapped* to an ontology concept, and nothing runs that mapping step on a fresh deployment --
`harvest` and `suggest` only ever run K12/K13, never K8. The full sequence, verified live
against `deploy/compose.yaml`'s Postgres + Neo4j with a plain `OPENAI_API_KEY`:

```bash
docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j

EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \
  .venv/bin/python -m egpt_retail.entrypoints migrate
EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \
  .venv/bin/python -m egpt_retail.entrypoints warehouse-migrate

# 1. Seed the warehouse -- R10's step 0 (apply_warehouse_ddl + the nightly-refresh workflow
#    over product/egpt_retail/contracts/_fixtures/landing), or a real connected warehouse.
# 2. egpt-retail load-knowledge          # ontology into Neo4j, prose into pgvector
# 3. egpt-retail harvest --schema silver # K4: tables into the graph
# 4. Run BOTH of K8's proposers over the harvest + ontology -- mapping_proposer AND
#    enrichment_proposer -- and file both sets of proposals (mapping.to_proposals /
#    enrich.to_proposals, then ProposalStore.propose_many). Mapping alone is not enough: a
#    MAPPING proposal targets a SemanticConcept node that only an accepted ENRICHMENT proposal
#    creates, so running mapping by itself reproduces the issue's own symptom one layer
#    deeper. Neither proposer has a CLI entrypoint today -- K08_proposers.py is the reference.
# 5. Accept and apply both proposal kinds (ProposalStore.accept then .apply_accepted). The F2
#    review API (POST /semantic/proposals/accept) does this over HTTP once PR #235 merges to
#    main; until then, call the store methods directly.
.venv/bin/python -m egpt_retail.entrypoints suggest
# or: GET /kpis/suggestions?business_area=...
```

Exact commands, the ordering dependency above, two pre-existing bugs this surfaced (a broken
`%I` placeholder in `harvest_schema(..., sample=True)`, and no working settings-driven
plain-OpenAI model/embedder path despite #252), and the real captured evidence are all recorded
in `TEST_LOG.md`'s `K253` entry.
