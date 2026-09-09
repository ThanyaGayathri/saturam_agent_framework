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
| `K00_executor_registry.py` | — | The executor naming convention: the silent collision it prevents, and the check that enforces it. |
| `K01_memory_scope_convention.py` | K1 | The memory scope convention against the real `Scope`: write scopes, read scopes, and the isolation matrix. |
| `K02_domain_prose.py` | K2 | The curated retail corpus into pgvector, split on headings and tagged by origin. Needs pgvector + the corpus. |
| `K03_ontology.py` | K3 | The shipped retail ontology: synonyms and plain language reaching concepts by meaning, and an overlay surviving a core upgrade. Needs Neo4j. |
| `K04_metadata_harvest.py` | K4 | The metadata harvest against R4's silver schema: types, keys, comments, and column-name collisions. Needs a warehouse. |
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
| `R03_prototype_screen.md` | R3 | The other half of R3's verification -- a prototype screen rendered from the stub. A procedure run in the prototype repository, with what to record. |

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
