# Starter template rebrand tooling

`egpt create` scaffolds new projects by cloning the starter templates under
`Saturam-Inc/egpt-os-{docker,aws,fly,gcp,railway}`. Those repositories are
rebranded copies of the upstream `agno-agi/agentos-*` templates (Apache-2.0).
This directory holds the tooling that produces them, so the initial creation
and any future refresh from upstream are reproducible.

## Contents

| File | Purpose |
|------|---------|
| `rebrand_template.sh` | Clone an upstream template, strip history, apply the codemod, report residual brand leakage. Also provides `check <dir>`. |
| `rebrand_codemod.py` | The mechanical rename (agno -> egpt, AgentOS -> EgptOS, AGNO_* -> EGPT_*) plus structural patches (Dockerfile, compose, CI, scripts) so the template installs egpt from GitHub release wheels. |
| `files/install_egpt.sh` | Canonical installer copied into each template as `scripts/install_egpt.sh`. Downloads the egpt wheel from a framework GitHub release and installs it with `--no-deps` (egpt is not on PyPI; the public `egpt==0.0.1` project is unrelated). |

## Producing a template repo

```bash
# 1. Build the rebranded tree (writes ./egpt-os-docker)
./scripts/templates/rebrand_template.sh docker

# 2. Do the manual pass printed at the end of the run
#    (README rewrite, example.env comments, skills prose)

# 3. Regenerate requirements.txt against the pinned egpt release
export GITHUB_TOKEN=...   # token with read access to framework releases
(cd egpt-os-docker && ./scripts/generate_requirements.sh)

# 4. Validate
(cd egpt-os-docker && ./scripts/venv_setup.sh && source .venv/bin/activate && ./scripts/validate.sh)
docker build --secret id=github_token,env=GITHUB_TOKEN egpt-os-docker

# 5. Leak check must pass before publishing
./scripts/templates/rebrand_template.sh check egpt-os-docker

# 6. Publish as a private repo under Saturam-Inc
cd egpt-os-docker
git init -b main && git add -A && git commit -m "feat: initial rebranded template"
gh repo create Saturam-Inc/egpt-os-docker --private --source . --push

# 7. Add the CI secret so validate.yml can download the egpt wheels
gh secret set EGPT_RELEASES_TOKEN --repo Saturam-Inc/egpt-os-docker --body "<token>"
```

## GITHUB_TOKEN at deploy time

`egpt` is installed from private GitHub release wheels, so the image build needs a token.
Each cloud flavor deploys through its own `scripts/<flavor>/up.sh` and `redeploy.sh` rather
than through `scripts/build_image.sh`, so the codemod patches those scripts directly: each
one fails fast when `GITHUB_TOKEN` is unset, then forwards it to the build.

| Flavor | How the token reaches the build |
|--------|--------------------------------|
| docker | `docker compose` build secret, or `docker build --secret id=github_token,env=GITHUB_TOKEN` |
| aws, gcp | `docker build --secret id=github_token,env=GITHUB_TOKEN` |
| fly | `fly deploy --build-secret "github_token=$GITHUB_TOKEN"` |
| railway | `railway variables --set GITHUB_TOKEN=…`, consumed as a Dockerfile `ARG` |

Export `GITHUB_TOKEN` before running any of them:

```bash
export GITHUB_TOKEN=...
(cd egpt-os-aws && ./scripts/aws/up.sh)
```

**Railway caveat.** Every other flavor mounts the token as a BuildKit secret, which keeps
it out of the image *and* the build cache. Railway's builder does not support secret
mounts, so the Railway template falls back to a build `ARG` — the token is recoverable
from Railway's build cache. That is a platform limitation rather than a choice; use a
read-only, narrowly-scoped token for Railway deployments and rotate it independently.

## Allowed residual strings

The `check` subcommand greps for `agno`/`agentos` and fails on any hit except:

- `openinference-instrumentation-agno` — a real third-party PyPI package
  (pulled in by `egpt[os]`), not a brand reference.
- `AGENTOS_URL` and `AGENTOS_MCP_SIGNING_KEY` — env var names the framework
  and egptctl still read (`egptctl/discovery.py` hardcodes `AGENTOS_URL`).
- `agno_pat_` — the service-account token prefix the framework still mints
  (`libs/agno/egpt/os/service_accounts.py` `TOKEN_PREFIX`).
- `NOTICE` — the Apache-2.0 attribution to the upstream template.
- `LICENSE` — retains the upstream copyright notice, as Apache-2.0 requires.

## Version pinning

Templates pin the framework release via `egpt[...]==<version>` in
`pyproject.toml` and default `EGPT_VERSION` in `scripts/install_egpt.sh`
(overridable via env). When a new framework release ships, bump both, rerun
`./scripts/generate_requirements.sh`, and push to the template repos.

## Refreshing from upstream

Upstream templates evolve independently. To pick up upstream changes, rerun
`rebrand_template.sh <flavor>` in a scratch directory and diff the result
against the published `egpt-os-<flavor>` repo. The codemod warns whenever an
expected upstream pattern is missing — those warnings are the signal that a
structural patch needs updating by hand.
