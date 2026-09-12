# Azure cutover — the checklist for the day the environment exists

Supersedes `deploy/AKS_CUTOVER.md` (kept as a pointer to here, not deleted, since it recorded
real history — the earlier draft of the Azure architecture doc said Container Apps, the plan
briefly followed AKS instead, and this doc is that decision reversing back per
`docs/plan/build-plan.md`'s D15 and `docs/plan/azure-migration-plan.md`). Written the same way
its predecessor was: every step below either provisions something the target chart/deployment
has a config key for, or re-runs a scenario that already passed locally. Nothing here is new
behaviour beyond what `docs/plan/azure-migration-plan.md`'s five packages (AZ-P0 through AZ-D)
deliver.

> **Status: nothing past AZ-P0 has landed.** Every AZ-A/AZ-B/AZ-C/AZ-D reference below describes
> in-flight work, not code on `main` -- check the PR before relying on it. Same idiom as
> `deploy/helm/egpt/README.md`'s ledger: a row leaves "planned" when the package has merged *and*
> its scenario is recorded in `cookbook/95_azure_migration/TEST_LOG.md`, and not before.
>
> | Package | State | Evidence recorded |
> |---|---|---|
> | AZ-P0 -- these doc updates | in review | -- |
> | AZ-A -- `AzureBlobObjectStore` | **planned** - PR #213 open, unmerged | **NEVER** |
> | AZ-B -- Azure AI Search memory substrate | **planned** - PR #216 open, unmerged, carrying an unresolved Critical review finding (cross-tenant document-key collision in `_document_key`) | **NEVER** |
> | AZ-C -- K2 knowledge-prose migration | **NEVER started** -- P2, gated on AZ-B | **NEVER** |
> | AZ-D -- Entra `scopes_claim` + JWKS URL | **planned** - PR #214 open, unmerged, carrying an unresolved Critical review finding (blocking, un-cached JWKS fetch on the event loop) | **NEVER** |
>
> `cookbook/95_azure_migration/` does not exist yet either; AZ-A/AZ-B/AZ-C/AZ-D each create their
> own script and log entry in it.

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
| Azure Database for PostgreSQL Flexible Server (control plane) | zone-redundant, PgBouncer in front, **session** pool mode (not transaction — psycopg3 prepared statements and the consolidator's advisory lock both need a stable session). **Scoped down from the AKS plan**: operational/config/workflow/audit metadata only. Knowledge and memory vectors are planned to move to Azure AI Search once AZ-B (PR #216, open) and AZ-C (P2, not started) land; pgvector is a dependency of this server until they do — `product/egpt_retail/memory/config.py` still reads `"backend": "pgvector"`, and `registry/_declarations/vectorstores.py` registers no `azure_ai_search` backend | `database.url`, `database.directUrl` |
| Second Flexible Server (warehouse) | unchanged from the AKS plan — landing/silver/gold, separate from control-plane state so nightly transforms never contend with agent state | `database.warehouseUrl` |
| Azure AI Search service | the planned replacement for pgvector on K1 memory-vector and K2 knowledge-prose retrieval (AZ-B, PR #216 open and unmerged; AZ-C, P2, not started). Confirm the index vector-field dimension against AZ-B's decision before creating the index; it is a schema decision baked in at creation time. **That decision is not made yet** — `docs/plan/azure-migration-plan.md`'s AZ-B section settles it with a real benchmark, which AZ-B's own Evidence step produces and which has not been run. The plan's starting point is 1024, kept from Mode B's existing truncation (`libs/agno/egpt/memory/embedding.py`'s `MODE_DEFAULTS`); on the day, read the benchmark AZ-B records in `cookbook/95_azure_migration/TEST_LOG.md`, not this line | `search.*` |
| Key Vault + Secrets Store CSI driver (or the Functions/Container Apps native Key Vault reference, whichever the eventual IaC choice uses) | secrets: `egpt-internal-service-token`, `azure-openai-api-key`, `azure-embedder-api-key`, `neo4j-password`, plus the four DB DSNs (`database.url`, `database.directUrl`, `database.warehouseUrl`, `database.warehouseReadonlyUrl` — including R6's `egpt_reader` password) and `search.apiKey` — this was already a named gap on the AKS plan (four secrets shipped as literal `values.yaml` text) and applies unchanged here; provision real credentials through the vault from day one rather than carrying the local-dev literal forward | `secrets.provider`, `secrets.keyVault.*` |
| Entra ID app registration | app roles `viewer`, `analyst`, `admin` mapped to the scope sets in the API contract's `x-egpt-roles`; `aud` = `api://egpt-retail`. **The framework gaps this needs are not closed yet** — AZ-D is PR #214, open and unmerged, and currently carries an unresolved Critical finding (a blocking, un-cached JWKS fetch on the event loop). Today `AuthorizationConfig` (`libs/agno/egpt/os/config.py`) has `jwks_file` only: no `scopes_claim`, no URL mode — the two **NEVER** rows `deploy/helm/egpt/README.md`'s ledger already records, unchanged. Once #214 merges, `AuthorizationConfig.scopes_claim`/`scopes_claim_format` will handle Entra's `roles`/`scp` claims and `jwks_file` will accept a URL (via `PyJWKClient`, fetch + TTL cache + auto-refresh on an unrecognized `kid`), so it can point at `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys` directly instead of carrying the manual re-fetch-on-rotation workaround. Do not schedule this row before #214 merges — every route 403s on an Entra token until it does | `auth.jwks` (a file today, a URL once #214 merges), `auth.scopesClaim`, `auth.scopesClaimFormat` (both arrive with #214), `auth.audience` |
| Azure OpenAI, two deployments (small tier, frontier tier) + one embedding deployment | unchanged from the AKS plan, private endpoint | `model.*` |
| Neo4j Community Edition on Container Apps, two logical instances (memory + semantic layer, domain ontology) | **This is what local dev already runs** — the AKS plan's "Enterprise, one cluster, two databases, licence required" is no longer the shape; two Community instances is both the local rehearsal and the production target now, so there is nothing left to prove that wasn't already proven locally | `neo4j.*` |
| Storage account, Blob Storage (frontend static hosting) + a separate container for the landing zone | landing-zone access is **planned** to go through `AzureBlobObjectStore` (AZ-A, PR #213, open and unmerged — `product/egpt_retail/storage/azure_blob.py` does not exist yet, and `storage/factory.py` today knows only `local` and `s3`) — not S3-compatible, a genuine new backend, not a config change from the AKS plan's ADLS Gen2 assumption. Lane C's landing contract still decides the layout | `storage.*` |
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
