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

# R4 needs the same Postgres plus the egpt_warehouse database (created on first boot):
docker compose -f deploy/compose.yaml up -d postgres
EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \
  .venv/bin/python -m egpt_retail.entrypoints warehouse-migrate
.venv/bin/python cookbook/94_retail/R04_landing_to_silver.py
.venv/bin/python cookbook/94_retail/R04_conformance_sql.py   # no services
bash cookbook/94_retail/R04_minio_load.sh                    # needs Docker

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
