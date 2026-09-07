# deploy/helm/egpt

The application chart. `values.yaml` is Azure-shaped; `values.local.yaml` is what runs on kind.
The point of the split is that AKS day is a values-file change, not a chart change — and that
nobody mistakes an untested Azure path for a tested one.

## Install

```bash
kind create cluster --name egpt --config deploy/kind/cluster.yaml
kind load docker-image egpt-retail:dev --name egpt
helm upgrade --install egpt-infra deploy/helm/egpt-local-infra          # first: the migrate hook needs Postgres
python -m egpt_retail.app.devauth keygen /tmp/egpt-auth                   # offline RS256 key pair + JWKS
helm upgrade --install egpt deploy/helm/egpt -f deploy/helm/egpt/values.local.yaml \
  --set-file auth.jwks=/tmp/egpt-auth/jwks.json
```

## The ledger: which values paths have executed, and where

| Values key | Executed | Where | On AKS |
|---|---|---|---|
| `api.*`, `pipelines.*`, `consolidator.*`, `migrate.*` | **yes** | kind, `R02_cluster_evidence.sh` | unchanged |
| `secrets.provider: kubernetes` | **yes** | kind | replaced by `keyVault` |
| `secrets.provider: keyVault` + `secrets.keyVault.*` | **NEVER** | — | first run on AKS day (`AKS_CUTOVER.md` §3) |
| `auth.enabled` + `auth.jwks` (offline RS256 from `devauth`) | **yes** | kind: 200 / 403 / 401 / open `/health` | same key, Entra's JWKS document |
| `auth.jwks` holding Entra's document, `aud` from an Entra token | **NEVER** | — | `AKS_CUTOVER.md` §4. No JWKS URL option exists in the framework (`AuthorizationConfig.jwks_file` only); rotation means re-applying the file — a named Lane R framework item |
| `database.url` through PgBouncer, session mode | **yes** | kind, local infra chart | Flexible Server behind PgBouncer |
| `database.warehouseUrl` | created only | kind | own Flexible Server; nothing reads it until R4 |
| `neo4j.*` two databases in one instance | **NEVER** — two Community instances stand in | kind | Enterprise licence required |
| `storage.*` (ADLS) | **NEVER** — MinIO stands in, nothing reads it | kind | R4 / Lane C |
| `redis.host` | deployed, unused | kind | Azure Cache for Redis |

"Executed" means the four R2 scenarios ran against it and the result is in
`cookbook/94_retail/TEST_LOG.md`. Anything else is a hypothesis with a key reserved for it.

## Why some things are the way they are

- **Two charts.** The `migrate` Job is a `pre-install` hook, and Helm runs hooks before the
  release's own resources exist. A Postgres in this chart would not be running when the hook
  fires. Local infrastructure is therefore its own release, installed first — which also mirrors
  AKS, where managed Postgres pre-exists the application.
- **No consolidator replica count.** `replicas: 1` is hardcoded, `strategy: Recreate` (the default
  `RollingUpdate` runs two during every upgrade), and `_helpers.tpl` refuses to render if a
  `replicaCount` or `replicas` key appears. None of that stops `kubectl scale`; the entrypoint's
  Postgres advisory lock does — the second pod exits 3 naming the customer.
- **PgBouncer in session mode.** Transaction mode breaks psycopg3's prepared statements
  (intermittent `prepared statement "_pg3_N" already exists` under load) and would let the
  consolidator's session-scoped lock evaporate. `database.directUrl` bypasses the pooler for the
  lock regardless.
- **`terminationGracePeriodSeconds: 45`** on the consolidator, above the worker's 30s
  `stop_timeout`; otherwise Kubernetes kills a pass mid-way.
- **The migrate Job's env is inline, not `envFrom` the release ConfigMap.** Same hook rule as the
  two-chart split, one level down: on a clean install the release's own ConfigMap and Secret do
  not exist yet when the hook runs, and the pod sits in `CreateContainerConfigError`. Found on
  the first kind install; the Job needs only the database URL.
- **`cryptography` is a product dependency.** The framework declares bare `PyJWT`, which
  verifies HS256 only; with an RSA JWKS every key fails to parse and the validator starts with
  no keys. Any RS256 deployment of `egpt[os]` needs it — Entra on AKS included.
- **Roles are not in this chart.** G11's viewer / analyst / admin → scope mapping lives in the
  API contract (`x-egpt-roles`), and Entra app roles are configured against it. One source.
