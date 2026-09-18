# Azure cutover — the checklist for the day the environment exists

Supersedes `deploy/AKS_CUTOVER.md` (kept as a pointer to here, not deleted, since it recorded
real history — the earlier draft of the Azure architecture doc said Container Apps, the plan
briefly followed AKS instead, and this doc is that decision reversing back per
`docs/plan/build-plan.md`'s D15 and `docs/plan/azure-migration-plan.md`). Written the same way
its predecessor was: every step below either provisions something the target chart/deployment
has a config key for, or re-runs a scenario that already passed locally. Nothing here is new
behaviour beyond what `docs/plan/azure-migration-plan.md`'s five packages (AZ-P0 through AZ-D)
deliver.

> **Status: AZ-P0, AZ-A, AZ-B and AZ-D are on `main`; AZ-C is in review.** "Merged" is not
> "run against Azure": no environment this repository has been developed in holds an Azure
> subscription, so every package's evidence is against a local emulator or a mocked transport,
> as its `TEST_LOG.md` entry states. Same idiom as `deploy/helm/egpt/README.md`'s ledger: a row
> leaves "planned" when the package has merged *and* its scenario is recorded in
> `cookbook/95_azure_migration/TEST_LOG.md`; it reaches "yes" only when that scenario has run
> against the real service.
>
> | Package | State | Evidence recorded |
> |---|---|---|
> | AZ-P0 -- these doc updates | merged, #217 | -- |
> | AZ-A -- `AzureBlobObjectStore` | merged, #213 | `AZ_A_blob_roundtrip.py` against Azurite; **never** against Blob Storage |
> | AZ-B -- Azure AI Search memory substrate | merged, #216 (substrate + registry); the product-side flip -- `EGPT_RETAIL_MEMORY_BACKEND` defaulting to `azure_ai_search`, `memory.backend`/`search.*` here -- is this change | `AZ_B_ai_search_substrate.py` and the `AZ-B memory backend flip` entry, both against a mocked transport; **never** against a real index |
> | AZ-C -- K2 knowledge-prose migration | **in review** -- the parallel PR, gated on AZ-B's dimension and scoring decisions, which are recorded | pending that PR's log entry |
> | AZ-D -- Entra `scopes_claim` + JWKS URL | merged, #214 | `AZ_D_entra_scopes.py` against a local JWKS server; **never** against Entra |

**Compute is split by statelessness, not five entrypoints forced onto one platform.** `api` is a
plain ASGI app with no in-process background work — it runs on Azure Functions Flex Consumption.
`pipelines` (its in-process scheduler poller), `consolidator` (a Postgres-advisory-lock-guarded
process), and the three one-shot scripts (`migrate`, `warehouse_migrate`, `schedule_nightly`) stay
long-lived/cron-triggered container workloads on Azure Container Apps, alongside Neo4j — the same
platform already required for a stateful graph database. No `libs/agno/egpt/scheduler/` redesign
is needed to make this split; see `docs/plan/azure-migration-plan.md` for the reasoning this
recommendation is based on.

## 1. Provision (Azure)

| Resource | Notes | Config it fills |
|---|---|---|
| Front Door Premium + WAF | edge ingress, routing, Private Link origins to the Functions app and Container Apps environment | — |
| VNet, Private Endpoints, Private DNS Zones | private connectivity for Postgres, Azure AI Search, Key Vault, Container Apps environment | — |
| Azure Functions app, Flex Consumption plan, VNet-integrated | hosts `api` only, via the Python ASGI adapter (`func.AsgiMiddleware`) wrapping `egpt_retail.app.main:app` unchanged. **Cold-start is the named risk**: DB pool init, schema provisioning, and the `assert_memory_wired` fail-fast check all run in ASGI lifespan startup, paid once per long-lived pod today but per cold worker here — mitigate with Flex Consumption's always-ready instances setting, or trim lifespan work, before relying on scale-from-zero in production | `functionapp.*` |
| Azure Container Apps environment | hosts Neo4j (Community, two logical instances — see the Neo4j row below), `pipelines` (regular Container App, min-replicas ≥ 1, unchanged process), `consolidator` (Container Apps Job, cron-triggered on the existing 5-minute interval, running `consolidator --once` — its advisory-lock design already fits a per-invocation trigger with no redesign), and `migrate`/`warehouse_migrate`/`schedule_nightly` (one-shot Container Apps Jobs) | `containerapps.*` |
| Azure Database for PostgreSQL Flexible Server (control plane) | zone-redundant, PgBouncer in front, **session** pool mode (not transaction — psycopg3 prepared statements and the consolidator's advisory lock both need a stable session). **Scoped down from the AKS plan**: operational/config/workflow/audit metadata only. Memory vectors (K1) now default to Azure AI Search: `registry/_declarations/vectorstores.py` registers `azure_ai_search` (AZ-B, #216) and `product/egpt_retail/memory/config.py` selects it through `EGPT_RETAIL_MEMORY_BACKEND`, whose default is `azure_ai_search` (`memory.backend`); `pgvector` is the explicit local option, which compose and `values.local.yaml` set. Knowledge-prose vectors (K2) follow with AZ-C (in review), so pgvector remains a dependency of this server until that PR merges and both indexes exist | `database.url`, `database.directUrl`, `memory.backend` |
| Second Flexible Server (warehouse) | unchanged from the AKS plan — landing/silver/gold, separate from control-plane state so nightly transforms never contend with agent state | `database.warehouseUrl` |
| Azure AI Search service | the replacement for pgvector on K1 memory-vector retrieval (AZ-B, merged as #216; the product-side switch is `EGPT_RETAIL_MEMORY_BACKEND=azure_ai_search`, the default) and on K2 knowledge-prose retrieval (AZ-C, in review). One service; K1 writes the index `search.memoryIndex` (`egpt-retail-memory`), created by the substrate on first boot via `create_or_update_index`, so the pods' identity or `search.apiKey` needs index-create rights, not only document rights. The vector-field dimension is baked in at creation time and **is decided**: 1024, Mode B's existing truncation, kept deliberately -- `cookbook/95_azure_migration/AZ_B_dimension_and_scoring_decisions.md` records why (a 1024-vs-768 tie, no measured gain from widening, and the embedding-version tag that gates re-embedding is keyed on dimension). What is *not* done is any run against a real service: the substrate was proven against a mocked transport only, and the memory-backend flip likewise (`TEST_LOG.md`, `AZ-B memory backend flip`). The first boot against a real index is therefore an AZ-B verification step of this checklist, not a rerun | `memory.backend`, `search.endpoint`, `search.memoryIndex`, `secrets.values.searchApiKey` |
| Key Vault + Secrets Store CSI driver (or the Functions/Container Apps native Key Vault reference, whichever the eventual IaC choice uses) | secrets: `egpt-internal-service-token`, `azure-openai-api-key`, `azure-embedder-api-key`, `neo4j-password`, `search-api-key` (the Azure AI Search admin key, `secrets.values.searchApiKey`; rendered into `EGPT_RETAIL_MEMORY_SEARCH_API_KEY` only when non-empty, and mapped by the `keyVault` provider's `SecretProviderClass` alongside the other four), plus the four DB DSNs (`database.url`, `database.directUrl`, `database.warehouseUrl`, `database.warehouseReadonlyUrl` — including R6's `egpt_reader` password) — this was already a named gap on the AKS plan (four secrets shipped as literal `values.yaml` text) and applies unchanged here; provision real credentials through the vault from day one rather than carrying the local-dev literal forward | `secrets.provider`, `secrets.keyVault.*` |
| Entra ID app registration | app roles `viewer`, `analyst`, `admin` mapped to the scope sets in the API contract's `x-egpt-roles`; `aud` = `api://egpt-retail`. The framework gaps this needed are closed by AZ-D (merged, #214): `AuthorizationConfig.scopes_claim`/`scopes_claim_format` (`libs/agno/egpt/os/config.py`) handle Entra's `roles`/`scp` claims and `jwks_file` accepts a URL (via `PyJWKClient`, fetch + TTL cache + auto-refresh on an unrecognized `kid`), so it can point at `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys` directly instead of the manual re-fetch-on-rotation workaround. Proven against a local JWKS server only (`AZ_D_entra_scopes.py`); the first real Entra token is a step of this checklist. **Still open on the product side:** `product/egpt_retail/app/factory.py::build_agent_os` passes `jwks_file` (so `EGPT_RETAIL_AUTH_JWKS_FILE` may already carry the Entra URL) and `audience` only -- it sets no `scopes_claim`/`scopes_claim_format`, and `RetailSettings` has no field for them, so an Entra token's `roles`/`scp` still resolves to no scopes until a `retail_auth_scopes_claim`/`_format` pair is added and threaded through. Do not schedule this row before that lands | `auth.jwks` (file or URL), `auth.audience`; no chart or settings key yet for the scopes claim -- the gap named above |
| Azure OpenAI, two deployments (small tier, frontier tier) + one embedding deployment | unchanged from the AKS plan, private endpoint | `model.*` |
| Neo4j Community Edition on Container Apps, two logical instances (memory + semantic layer, domain ontology) | **This is what local dev already runs** — the AKS plan's "Enterprise, one cluster, two databases, licence required" is no longer the shape; two Community instances is both the local rehearsal and the production target now, so there is nothing left to prove that wasn't already proven locally | `neo4j.*` |
| Storage account, Blob Storage (frontend static hosting) + a separate container for the landing zone | landing-zone access goes through `AzureBlobObjectStore` (AZ-A, merged as #213: `product/egpt_retail/storage/azure_blob.py`, selected by `EGPT_RETAIL_STORAGE_PROVIDER=azure_blob`; proven against Azurite only, never against Blob Storage) — not S3-compatible, a genuine new backend, not a config change from the AKS plan's ADLS Gen2 assumption. Lane C's landing contract still decides the layout | `storage.*` |
| Azure Cache for Redis, Standard | unchanged — budget counters, cross-replica run cancellation | `redis.host` |
| Container registry | for the Container Apps images (Neo4j sidecar config, `pipelines`/`consolidator`/one-shot job images); the Functions app deploys by zip/container separately | `image.*` |
| Application Insights, Log Analytics Workspace, Azure Monitor | unchanged — infrastructure telemetry; app-level tracing stays on Postgres in phase one, per the standing decision | — |

## 2. Configuration

**IaC tooling (Bicep vs. Terraform) is not yet decided** — Helm doesn't target Functions or
Container Apps the way it targets Kubernetes, so `deploy/helm/egpt/`'s templates do not carry
forward as-is. This section names the values every config needs, not a file format. Once decided:
fill every key in the table above; secrets route through Key Vault from the start (no
"literal-text-first, vault-later" step this time); once AZ-D (#214) merges `auth.jwks` becomes a
URL, and only then does the `--set-file`/manual-remount step from the AKS plan stop being
required.

## 3. Install

Two independent deploys, since they're two platforms:

```bash
# Functions app (api only)
func azure functionapp publish <app-name> --python

# Container Apps environment (Neo4j, pipelines, consolidator, one-shot jobs)
az containerapp job start --name egpt-migrate --resource-group <rg>
az containerapp job start --name egpt-warehouse-migrate --resource-group <rg>
az containerapp update --name egpt-pipelines --resource-group <rg> --image <registry>/egpt-retail:<tag>
```

The exact commands depend on the IaC tooling decision above; these name the deploy units, not a
finished script. `egpt-migrate`/`egpt-warehouse-migrate` are one-shot Jobs and gate the release
the same way the AKS plan's pre-install hook Jobs did.

## 4. Re-run the four R2 scenarios, adapted

`cookbook/94_retail/R02_cluster_evidence.sh`'s intent, retargeted from `kubectl`/AKS to the
Functions + Container Apps shape, with tokens from Entra instead of `devauth`:

1. All Container Apps running (Neo4j, `pipelines`); the Functions app responding on its endpoint.
2. A token with `config:read` → 200 on `/config`; one without → 403; `/health` with no token →
   200. This is the scenario that most directly exercises AZ-D — a real Entra token, `roles` or
   `scp` claims, resolved to the expected internal scopes — so it cannot run until #214 merges.
3. Recycle a Functions instance under a request loop → no non-200 (the Functions-platform analog
   of "delete a pod under load").
4. Scale `consolidator`'s Container Apps Job trigger to fire twice concurrently → the second
   invocation's `pg_try_advisory_lock` refuses and exits `3` naming the customer, same as the
   AKS plan's `kubectl scale --replicas=2` scenario, now proving it at the Job-trigger level
   instead of the replica level.

Record the results in `TEST_LOG.md` as an `R2 on Azure` entry. The chart/deployment's own ledger
(wherever the eventual IaC lands) moves each row from NEVER to yes as it passes — and not before.

## 5. Not part of this checklist

Front Door and WAF configuration itself (routing rules, WAF policy tuning) is edge configuration
authored once the Functions/Container Apps endpoints exist, not before. Observability: traces to
Postgres in phase one; Application Insights for infrastructure only — unchanged from the AKS plan.
