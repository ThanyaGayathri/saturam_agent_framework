# AKS cutover — the checklist for the day the cluster exists

Written in phase zero, while the scenarios are fresh, so that AKS day is execution rather than
design. Every step below either provisions something the chart already has a values key for, or
re-runs a scenario that already passed on kind. Nothing here is new behaviour.

## 1. Provision (Azure)

| Resource | Notes | Values key it fills |
|---|---|---|
| AKS cluster, one VNet, workload identity enabled | per the Azure deployment architecture | — |
| Azure Database for PostgreSQL Flexible Server (control plane), pgvector extension allowed, zone-redundant | PgBouncer as a Deployment in front, **session** mode | `database.url`, `database.directUrl` |
| Second Flexible Server (warehouse) | or a second database if contention is acceptable at first | `database.warehouseUrl` |
| Key Vault + Secrets Store CSI driver on the cluster | secrets: `egpt-internal-service-token`, `azure-openai-api-key`, `azure-embedder-api-key` | `secrets.provider: keyVault`, `secrets.keyVault.*` |
| Entra ID app registration | app roles `viewer`, `analyst`, `admin` mapped to the scope sets in the API contract's `x-egpt-roles`; `aud` = `api://egpt-retail`. **Cannot be completed by Entra configuration alone:** the middleware reads a `scopes` list claim only (`AuthorizationConfig` has no `scopes_claim` option), and Entra emits app roles as `roles: [...]` and delegated permissions as `scp` (a space-delimited string) -- never `scopes`. Every token would arrive with zero scopes and every non-excluded route would 403 until the framework gains a configurable `scopes_claim` plus a roles/scp-to-scopes expansion (or a product-side claim mapper). Named Lane R framework item, alongside the JWKS-URL gap below. | `auth.jwks` (Entra's `discovery/v2.0/keys` document, passed with `--set-file`; the framework reads a file, not a URL), `auth.audience` |
| Azure OpenAI, two deployments (small tier, frontier tier) + one embedding deployment | private endpoint | `model.*` |
| Neo4j Enterprise on AKS, two databases | **licence required**; this is the one path with no local rehearsal | `neo4j.*` |
| Storage account, ADLS Gen2, private endpoint | Lane C's landing contract decides the layout | `storage.*` |
| Azure Cache for Redis, Standard | budget counters, cross-replica run cancellation | `redis.host` |
| Container registry | `docker push`; `image.repository`, `image.tag` | `image.*` |

## 2. Values

Copy `values.yaml` to `values.azure.yaml`; fill every key in the table above; set
`secrets.provider: keyVault`, `auth.enabled: true`, `auth.jwks` from Entra's JWKS document (`curl https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys > entra-jwks.json`, then `--set-file auth.jwks=entra-jwks.json`; re-apply on key rotation until the framework gains a JWKS URL option),
`image.pullPolicy: IfNotPresent`. Do **not** install `egpt-local-infra`.

## 3. Install

```bash
helm upgrade --install egpt deploy/helm/egpt -f deploy/helm/egpt/values.azure.yaml --namespace egpt --create-namespace
kubectl -n egpt logs job/egpt-migrate            # the hook Job: real migrations on a clean database
```

The `SecretProviderClass` is rendered for the first time here. Expect to iterate on
`secrets.keyVault.clientId` / workload-identity binding; that is the one part of the chart that
has never run.

## 4. Re-run the four R2 scenarios, unchanged

`cookbook/94_retail/R02_cluster_evidence.sh` with `KUBE_CONTEXT` pointed at AKS and tokens from
Entra instead of `devauth`:

1. All pods running.
2. A token with `config:read` → 200 on `/config`; one without → 403; `/health` with no token → 200.
3. Delete an api pod under a request loop → no non-200.
4. `kubectl scale deploy/egpt-consolidator --replicas=2` → the second pod exits 3 naming the customer.

Record the results in `TEST_LOG.md` as an `R2 on AKS` entry. The chart README's ledger moves
each row from NEVER to yes as it passes — and not before.

## 5. Not part of the chart

Front Door and API Management (streaming routes unbuffered, sticky sessions on the streaming
path only) are edge configuration outside this chart. Observability: traces to Postgres in
phase one; Application Insights for infrastructure only.
