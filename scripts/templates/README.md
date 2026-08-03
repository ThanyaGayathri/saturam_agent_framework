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

## Allowed residual strings

The `check` subcommand greps for `agno`/`agentos` and fails on any hit except:

- `openinference-instrumentation-agno` — a real third-party PyPI package
  (pulled in by `egpt[os]`), not a brand reference.
- `AGENTOS_URL` and `AGENTOS_MCP_SIGNING_KEY` — env var names the framework
  and egptctl still read (`egptctl/discovery.py` hardcodes `AGENTOS_URL`).
- `NOTICE` — the Apache-2.0 attribution to the upstream template.

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
