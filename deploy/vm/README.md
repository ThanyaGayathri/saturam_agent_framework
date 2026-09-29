# deploy/vm/ -- the demo VM

The whole stack on one Azure VM (`egpt-vm`, `enterprisegpt.saturam.com`), for demos. This is
not the production target (that is `AZURE_CUTOVER.md`, D15); it is the local demo stack --
compose Postgres + Neo4j, the `egpt-retail` image, the Vite frontend -- put behind nginx so a
branch can be shown from a URL, and redeployed with one command.

```
deploy/vm/
├── compose.vm.yaml         postgres · neo4j · migrate · warehouse-migrate · api (+ poller) · consolidator
├── egpt.env.example        -> ~/egpt/egpt.env on the VM: model keys, tenant, CORS
├── frontend.env.example    -> ~/egpt/frontend.env on the VM: VITE_API_URL, /api/llm and login
├── site.env.example        -> ~/egpt/site.env on the VM: SITE hostname/IP, HTTP_ONLY (no cert yet)
├── egpt-frontend.service   systemd: node frontend/server.mjs on 127.0.0.1:8081
├── nginx.site              TLS: /backend/ -> api :7777 (SSE, no buffering) · / -> frontend :8081
├── nginx.http.site         the same on plain :80, default_server, for a VM without a certificate
│                           (both carry __SITE__, replaced with $SITE when installed)
├── setup.sh                one-time, on the VM: docker, nginx, node, env files, unit, nginx site
├── deploy.sh               every time, on the VM: checkout branch, build, up, frontend, nginx, health
└── sync-data.sh            from the laptop: copy the local demo databases + graph to the VM
```

Everything the browser reaches goes through nginx on 443; the API, frontend, Postgres and
Neo4j are bound to the loopback. The frontend is built with `VITE_API_URL=https://<site>/backend`,
so the browser and the API share an origin and CORS never enters into it.

## Redeploy a branch (the usual case)

```bash
ssh -i ~/AICoE/pem/egpt-azure.pem azureuser@20.64.179.94 \
    'bash ~/saturam-agent-framework/deploy/vm/deploy.sh demo/retail-prep'
```

`deploy.sh` fetches and hard-resets the checkout to `origin/<branch>` (the checkout carries
nothing else: secrets live in `~/egpt/`), rebuilds the image, brings the compose stack up
(migrations first, the API waits for them), runs `npm ci && npm run build`, restarts the
frontend service, makes sure nginx points at this stack, and checks `/` and `/backend/health`
through the public URL. Re-running it is the rollback too: deploy a different branch.
`SKIP_FRONTEND=1` and `SKIP_API=1` narrow it to one half.

Push the branch first -- the VM pulls from origin, it does not receive local commits.

## First-time setup of a VM

1. Get the repository onto the VM at `~/saturam-agent-framework`. The repository is private
   and the organisation has deploy keys disabled, so a VM either carries a personal GitHub
   token (`gh auth login`, the original VM) or -- preferred, no credential on the VM -- a bare
   repository the laptop pushes to over ssh (the second VM, `sat-egpt-vm`):

   ```bash
   ssh -i <key> azureuser@<vm> 'git init --bare ~/git/saturam-agent-framework.git'
   git remote add vm-sat ssh://azureuser@<vm>/home/azureuser/git/saturam-agent-framework.git
   git push vm-sat origin/main:refs/heads/main
   ssh -i <key> azureuser@<vm> 'git clone ~/git/saturam-agent-framework.git ~/saturam-agent-framework'
   ```

   With `GIT_SSH_COMMAND="ssh -i <key>"` on the push if the key is not in the ssh config.
   `deploy.sh <branch>` then fetches `origin/<branch>` from the bare repository, so "push the
   branch first" means pushing it to the VM rather than to GitHub.
2. Run `bash deploy/vm/setup.sh`. It installs Docker, nginx and Node 20 where missing,
   creates `~/egpt/{egpt,frontend}.env` from the examples, installs the systemd unit and the
   nginx site (available, not enabled). Open a new ssh session afterwards so the docker group
   membership applies.
3. Fill in `~/egpt/egpt.env` (at least `AZURE_OPENAI_API_KEY` and the endpoint) and
   `~/egpt/frontend.env` (`RETAILGPT_LLM_API_KEY`, the login password and secret). On a VM
   without a domain yet, also create `~/egpt/site.env` from `site.env.example` with the public
   IP as `SITE` and `HTTP_ONLY=1`; `EGPT_CORS_ORIGIN_LIST` and `VITE_API_URL` then use
   `http://<ip>`. Once the domain and its Certbot certificate exist, change `SITE`, drop
   `HTTP_ONLY`, fix the two URLs, and redeploy.
4. `deploy.sh <branch>`.
5. `deploy/vm/sync-data.sh` from the laptop, to carry the demo data over (below). Without it
   the stack is up but empty: no warehouse rows, no ontology, no KPIs, no use cases.
   `VM_HOST` and `VM_KEY` select the VM.

Both nginx templates carry `__SITE__` where the hostname goes, replaced with `$SITE` as they
are installed, so a second VM needs no edit -- only `~/egpt/site.env`. The TLS template also
names `$SITE` in its `ssl_certificate` paths, so that certificate must exist before a deploy
without `HTTP_ONLY`: `sudo certbot --nginx -d <site>` (install `certbot python3-certbot-nginx`
first on a VM that has never had one). `deploy.sh` stops with that instruction rather than
leaving nginx failing its config test. On the original VM the certificate was already there
from the prototype's deployment; `deploy.sh` disables the prototype's
`retailgpt.service` when it switches the site over, and leaves its config in
`sites-available` so `ln -sfn` back is the rollback to the prototype.

## Demo data

The demo state -- the Middleby warehouse (`egpt_warehouse`), the control plane (`ai`: KPI
registry, use cases, dashboards, recommendations, memory), and the Neo4j ontology and
semantic graph -- is built up over many steps (`cookbook/94_retail/TEST_LOG.md`, "The full
local sequence") and lives in the laptop's containers. `sync-data.sh` copies it as is:
`pg_dump` of both databases, a tar of Neo4j's `/data` (the local container is stopped for the
few seconds that takes), then on the VM drop + restore, replace `/data`, re-run the idempotent
migrations for the read-only role's grants, and restart the API. It replaces the VM's data
wholesale, so anything created on the VM since the last sync is lost -- sync before a demo,
not after.

Container names default to the laptop's (`smv1-pgvector-5532`, `egpt-retail-neo4j`); override
with `LOCAL_PG` / `LOCAL_NEO4J`.

To clone one VM's demo state onto another instead of the laptop's -- the two drift, because
each runs its own poller and each was synced from the laptop at a different time -- name the
source with `SOURCE_VM_HOST` / `SOURCE_VM_KEY`:

```bash
SOURCE_VM_HOST=azureuser@20.64.179.94 SOURCE_VM_KEY=~/AICoE/pem/egpt-azure.pem \
    VM_HOST=azureuser@20.169.82.164 VM_KEY=~/AICoE/pem/mcfadyen_egpt.pem \
    deploy/vm/sync-data.sh
```

The dumps stream through the laptop rather than landing on the source VM's disk, and the
source's Neo4j is stopped for the few seconds the archive takes (a remote `trap` starts it
again even if the ssh dies), so the *source* VM's own site is briefly without its graph.
Both VMs must carry the same `EGPT_RETAIL_NEO4J_PASSWORD`, for the reason below. `RESTORE_ONLY=1` skips the dump and upload and restores what
`~/egpt/sync` on the VM already holds (`restore-data.sh` is the same thing run from the VM).

**The Neo4j password travels with the data.** Neo4j 5 keeps its users in the `system`
database, which is inside `/data`, so after a sync the VM's graph accepts the *laptop's*
password, whatever `NEO4J_AUTH` said when the container was created. `EGPT_RETAIL_NEO4J_PASSWORD`
in `~/egpt/egpt.env` must therefore equal the local container's (`docker exec egpt-retail-neo4j
sh -c 'echo $NEO4J_AUTH'`), and the api container recreated after changing it
(`docker compose ... up -d --force-recreate neo4j api consolidator`). The symptom when it does
not: `/business-areas` 503 and `/kpis` 500 with `Neo.ClientError.Security.Unauthorized` in
the api log.

## Looking at it

```bash
ssh -i ~/AICoE/pem/egpt-azure.pem azureuser@20.64.179.94
export EGPT_VM_ENV_FILE=~/egpt/egpt.env
alias c='docker compose -f ~/saturam-agent-framework/deploy/vm/compose.vm.yaml --env-file ~/egpt/egpt.env'
c ps                          # the stack
c logs -f api                 # the API, including the poller and every use-case run
journalctl -u egpt-frontend -f
docker exec -it egpt-vm-postgres-1 psql -U ai -d ai
```

`egpt.env` is read by the compose file for interpolation *and* by the api/consolidator
containers as `env_file`, which is why `deploy.sh` exports `EGPT_VM_ENV_FILE` -- compose does
not expand `~` inside `env_file:` entries.
