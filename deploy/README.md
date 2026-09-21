# deploy/

Deployment artifacts for Enterprise GPT. Phase zero (R1) adds the image and the local compose
stack; R2 adds the Helm charts and the local cluster.

```
deploy/
├── Dockerfile                 one image, four entrypoints: api · pipelines · consolidator · migrate
├── compose.yaml               the local stack R1 verifies against (Postgres only by default)
├── postgres/init/             first-boot SQL: creates the warehouse database (D5)
├── kind/cluster.yaml          the local verification cluster (R2) -- AKS-era, see below
├── helm/egpt-local-infra/     Postgres+pgvector, PgBouncer, Neo4j x2, Redis, MinIO -- AKS-era local stand-in
├── helm/egpt/                 the AKS-era application chart -- superseded by D15, kept as reference
├── AKS_CUTOVER.md             superseded -- points to AZURE_CUTOVER.md
├── AZURE_CUTOVER.md           the current checklist: Functions (api) + Container Apps (everything else) (D15)
└── vm/                        the single-VM demo stack (enterprisegpt.saturam.com): compose + nginx + one-command redeploy, see vm/README.md
```

**D15 moved the standing compute decision from AKS to Azure Functions Flex Consumption + Azure
Container Apps** — see `docs/plan/build-plan.md`'s D15 and `docs/plan/azure-migration-plan.md`
for the gap analysis and phasing. The Helm chart, `kind/` cluster, and `egpt-local-infra` chart
above are kept as reference/history rather than deleted, but are not the current target; new
deploy work follows `AZURE_CUTOVER.md`.

## Build and run locally

```bash
docker build -f deploy/Dockerfile -t egpt-retail:dev .     # from the repo root
egpt up -f deploy/compose.yaml                             # or docker compose -f deploy/compose.yaml up --build
```

`egpt up` autodetects a compose file only in `./` and `./infra`; the `-f` flag is the whole
accommodation, by decision — no change to `egptctl`.

## The four entrypoints

| `egpt-retail …` | What | Replicas |
|---|---|---|
| `api` | Serves EgptOS. No scheduler poller. | Scale on HTTP concurrency. |
| `pipelines` | The same app, with the scheduler poller. Scheduled workflow runs execute here. | Safe to scale (`FOR UPDATE SKIP LOCKED`), but every replica needs the same `EGPT_RETAIL_INTERNAL_SERVICE_TOKEN`. |
| `consolidator` | Drains memory scopes for `EGPT_RETAIL_CUSTOMER_SLUG`. `--once` for one tick. | **One per customer.** It holds a Postgres advisory lock; a second instance exits 3. |
| `migrate` | Control-plane schema migrations to latest, then exit. | A Job (Helm `pre-install,pre-upgrade` hook in R2). Never an initContainer. |

Why `api` and `pipelines` are split when the poller could run on every API pod: contention, not
correctness. Nightly gold materialisation (R5) should never compete with interactive traffic.

## Settings

Every setting is an `EGPT_RETAIL_*` variable; see `product/egpt_retail/app/settings.py`. Two are
contracts with other lanes: `EGPT_RETAIL_CUSTOMER_SLUG` (the tenant id K1 will stamp on every
run context) and `EGPT_RETAIL_INTERNAL_SERVICE_TOKEN` (required for `pipelines`).

Model credentials are the framework's own: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` for
chat; `AZURE_EMBEDDER_OPENAI_API_KEY`, `AZURE_EMBEDDER_OPENAI_ENDPOINT`, `AZURE_EMBEDDER_DEPLOYMENT`
for the memory embedder. None is needed for `/config` or for a consolidator tick with nothing to
do; all are needed the moment a model is called or a memory row is written.

## Three things that are not obvious

- The consolidator's advisory lock is session-scoped, taken on a **dedicated connection direct to
  Postgres**. Through a transaction-mode pooler (PgBouncer) the session ends with the first
  transaction and the lock evaporates. When R2 adds PgBouncer, it uses session pooling — and
  psycopg3's prepared statements need `prepare_threshold=None` under transaction mode anyway.
- `terminationGracePeriodSeconds` for the consolidator must exceed the worker's `stop_timeout`
  (30s), or Kubernetes kills a pass mid-way.

- CORS: the API's allow-list defaults to `http://localhost:3000` plus legacy `agno.com` hosts
  (`libs/agno/egpt/os/utils.py:resolve_origins`, kept in sync with `egpt/os/settings.py`). A
  frontend served from anywhere else - a different local port, or the deployed static-hosting
  origin - needs the bare `CORS_ORIGIN_LIST` env var set on the API process, as a JSON array
  (e.g. `CORS_ORIGIN_LIST=["https://app.example.com"]`) - it's read through the same
  branded-settings machinery as every other list-typed field, so a plain comma-separated string
  fails validation. There is no `EGPT_RETAIL_*`-prefixed alias for it.

- Authorization is on exactly when `EGPT_RETAIL_AUTH_JWKS_FILE` is set. Locally that file comes
  from `python -m egpt_retail.app.devauth keygen <dir>` (an offline RS256 key pair; `mint` issues
  tokens) mounted through a ConfigMap. On Azure it is Entra's JWKS, reaching the app through Key
  Vault / app settings rather than a ConfigMap (**D15** — Functions and Container Apps, not AKS);
  it stays a file until AZ-D (PR #214, open and unmerged) adds the URL mode. RS256 needs
  `cryptography`, which `egpt[os]` does not bring — the product declares it.

Do not name a file here `*.config.*` — the repository `.gitignore` carries a blanket `*.config*`
pattern and the file is silently untracked.
