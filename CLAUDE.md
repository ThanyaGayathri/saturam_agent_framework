# CLAUDE.md — EGPT

Instructions for Claude Code when working on this codebase.

---

## Repository Structure

```
.
├── libs/agno/egpt/          # The framework. Ships as `pip install egpt`. No retail nouns.
├── product/                 # The Enterprise GPT retail product. Never in the egpt wheel.
├── frontend/                # The React interface. Full UI wiring is phase five; infra
│                            # plumbing (API base URL, fetch client) lands ahead of it (#222)
├── deploy/                  # Dockerfile, compose, Azure cutover (current, D15); Helm charts + kind config are AKS-era reference
├── cookbook/                # Examples, patterns and test cases (organized by topic)
├── scripts/                 # Development and build scripts
├── specs/                   # Design documents (symlinked, private)
├── docs/                    # Documentation (symlinked, private; docs/plan/ is tracked)
└── .cursorrules             # Coding patterns and conventions
```

### Framework or product

One repository, one visible line. `libs/agno/egpt/` is the published wheel and the root
import-linter governs; `product/egpt_retail/` is the retail product, imports `egpt` freely, and
is where retail nouns belong. The packaging boundary enforces it: the wheel is built from
`libs/agno/` with `packages.find include = ["egpt*"]`, so a root-level `product/` or `frontend/`
cannot reach it, and CI's wheel-contents check fails if that ever changes.

Five packages are genuine framework fixes and go in `libs/agno/egpt/` because every deployment
inherits them: **K1** memory scope and wiring, **K10** unified retrieval, **R6** the SQL guard,
**R7** the provenance record, **R8** escalation into the run record (`ModelResolveResult`
detail, `resolve_run_model_detailed`/`aresolve_run_model_detailed`, `_stamp_escalation` --
every `EscalatingModelProvider` deployment benefits, not only retail's tiers). Everything else
retail — ontology, prose, enrichment prompts,
semantic layer, KPI content, silver and gold, workflows, skills, dashboards, alerts, monitoring —
is `product/`. When in doubt: would a non-retail deployment want it? If not, it is product.

`product/` is its own distribution (`egpt-retail`, `product/pyproject.toml`), installed
editable by `dev_setup.sh` and `demo_setup.sh`, formatted at 88 columns, and covered by
`format.sh` and `validate.sh` (ruff, mypy with `MYPYPATH=libs/agno`, the API contract check).
See `docs/plan/delivery-plan.md`, "Where the code lives".

---

## Conductor Notes

When working in Conductor, you can use the `.context/` directory for scratch notes or agent-to-agent handoff artifacts. This directory is gitignored.

---

## Setting Up Symlinks

The `specs/` and `docs/` directories are symlinked from external locations. For a fresh clone or new workspace, create these symlinks:

```bash
ln -s ~/code/specs specs
ln -s ~/code/docs docs
```

These contain private design documents and documentation that are not checked into the repository.

---

## Virtual Environments

This project uses two virtual environments:

| Environment | Purpose | Setup |
|-------------|---------|-------|
| `.venv/` | Development: tests, formatting, validation | `./scripts/dev_setup.sh` |
| `.venvs/demo/` | Cookbooks: has all demo dependencies | `./scripts/demo_setup.sh` |

**Use `.venv`** for development tasks (`pytest`, `./scripts/format.sh`, `./scripts/validate.sh`).

**Use `.venvs/demo`** for running cookbook examples.

---

## Testing Cookbooks

Apart from implementing features, your most important task will be to test and maintain the cookbooks in `cookbook/` directory.

> See `cookbook/08_learning/` for the golden standard.

### Quick Reference

**Test Environment:**

```bash
# Virtual environment with all dependencies
.venvs/demo/bin/python

# Setup (if needed)
./scripts/demo_setup.sh

# Database (if needed)
./cookbook/scripts/run_pgvector.sh
```

**Run a cookbook:**
```bash
.venvs/demo/bin/python cookbook/<folder>/<file>.py
```

### Expected Cookbook Structure

Each cookbook folder should have the following files:
- `README.md` — The README for the cookbook.
- `TEST_LOG.md` — Test results log.

### Testing Workflow

**1. Before Testing**
- Ensure the virtual environment exists (run `./scripts/demo_setup.sh` if needed)
- Start any required services (e.g., `./cookbook/scripts/run_pgvector.sh`)

**2. Running Tests**
```bash
# Run individual cookbook
.venvs/demo/bin/python cookbook/<folder>/<file>.py

# Tail output for long tests
.venvs/demo/bin/python cookbook/<folder>/<file>.py 2>&1 | tail -100
```

**3. Updating TEST_LOG.md**

After each test, update the cookbook's `TEST_LOG.md` with:
- Test name and path
- Status: PASS or FAIL
- Brief description of what was tested
- Any notable observations or issues

Format:
```markdown
### filename.py

**Status:** PASS/FAIL

**Description:** What the test does and what was observed.

**Result:** Summary of success/failure.

---
```

---

## Code Locations

| What | Where |
|------|-------|
| Core agent code | `libs/agno/egpt/agent/` |
| Teams | `libs/agno/egpt/team/` |
| Workflows | `libs/agno/egpt/workflow/` |
| Tool interface | `libs/agno/egpt/tools/` (`Function`, `Toolkit`, `@tool`, registry) |
| Toolkits + MCP | `libs/agno/egpt/integrations/tools/` |
| Model interface | `libs/agno/egpt/models/` (`Model` base, message, metrics) |
| Model providers | `libs/agno/egpt/integrations/models/` |
| Knowledge/RAG | `libs/agno/egpt/knowledge/` (pipeline, `Reader`/`Embedder` interfaces, `ReaderFactory`, chunkers) |
| Knowledge integrations | `libs/agno/egpt/integrations/knowledge/` (readers, embedders, rerankers, loaders, remote content) |
| Memory | `libs/agno/egpt/memory/` |
| Learning | `libs/agno/egpt/learn/` |
| DB interface | `libs/agno/egpt/db/` (`BaseDb`/`AsyncBaseDb`, schemas, migrations) |
| DB backends | `libs/agno/egpt/integrations/db/` |
| Vector DB interface | `libs/agno/egpt/vectordb/` (`VectorDb` + distance/score/search) |
| Vector DB backends | `libs/agno/egpt/integrations/vectordb/` |
| Context provider contract + local providers | `libs/agno/egpt/context/` (`ContextProvider`/`ContextBackend`, `fs/`, `workspace/`, `wiki/provider.py`) |
| Context connectors | `libs/agno/egpt/integrations/context/` (calendar, database, gdrive, gmail, google, mcp, slack, web, and wiki's git/Notion backends) |
| Tests | `libs/agno/tests/` |
| Retail product | `product/egpt_retail/` (`app/` assembly, `api/` contract + stub, `contracts/`, `executors/`, `memory/`, `ontology/`, `skills/`, `entrypoints/`) |
| Retail product tests | `product/tests/` — contract tests and DB-free entrypoint tests |
| Retail cookbooks + evidence | `cookbook/94_retail/` — one folder, one `TEST_LOG.md`, entries tagged by package (K*, R*) |
| Deployment | `deploy/` — `Dockerfile`, `compose.yaml`, `AZURE_CUTOVER.md` (current: Functions + Container Apps, D15). `helm/egpt-local-infra/`, `helm/egpt/`, `kind/`, `AKS_CUTOVER.md` are AKS-era reference, superseded |
| Frontend | `frontend/` — full UI arrives in phase five; API plumbing (`src/lib/api.js`) lands incrementally ahead of it, see `frontend/README.md` |

---

## Coding Patterns

See `.cursorrules` for detailed patterns. Key rules:

- **Never create agents in loops** — reuse them for performance
- **Use output_schema** for structured responses
- **PostgreSQL in production**, SQLite for dev only
- **Start with single agent**, scale up only when needed
- **Both sync and async** — all public methods need both variants. Exempt: pure-construction
  methods that build and return an object with nothing to await (e.g.
  `BaseDb.clone_with_table_overrides`). The rule targets I/O-bound API; an `async def` that
  never awaits is dead weight. Define such a method on both the sync and async ABC so
  neither path degrades silently.

---

## Running Code

**Running cookbooks:**
```bash
.venvs/demo/bin/python cookbook/<folder>/<file>.py
```

**Running tests:**
```bash
source .venv/bin/activate
pytest libs/agno/tests/

# Run a specific test file
pytest libs/agno/tests/unit/test_agent.py
```

**Optional dependencies:** a unit test that imports an optional dependency (db driver,
vector DB client, channel SDK) must gate on it with `pytest.importorskip("<module>",
reason="...")` at module scope, or a `pytest.mark.skipif` on the individual tests. The dev
environment installs every driver, so a missed gate passes locally and in the main CI job and
only breaks on a partial install — the `unit-drivers-absent` CI job exists to catch it. See
CONTRIBUTING.md for the pattern.

---

## When Implementing Features

1. **Check for design doc** in `specs/` — if it exists, follow it
2. **Look at existing patterns** — find similar code and follow conventions
3. **Create a cookbook** — every pattern should have an example
4. **Update implementation.md** — mark what's done

---

## Before Submitting Code

**Always run these scripts before pushing code or creating a PR:**

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Format all code (ruff format)
./scripts/format.sh

# Validate all code (ruff check, mypy)
./scripts/validate.sh
```

Both scripts must pass with no errors before code review. Both cover `product/` as well as
the framework; `validate.sh`'s product target also runs the API contract check
(`scripts/check_api_contract.py`), so a contract edit without `./scripts/gen_api_models.sh`
fails here rather than in review.

**Commit and PR attribution:**

No AI attribution in git history. Commit messages carry no `Co-Authored-By: Claude ...`
trailer and no `Claude-Session:` link; PR descriptions carry no "Generated with Claude Code"
footer or session link. `.claude/settings.json` turns these off at the harness level
(`attribution.commit`/`attribution.pr` empty, `attribution.sessionUrl` false); this rule is the
instruction that takes precedence when a session's own defaults say otherwise. The PR template's
"AI-Generated PR Check" checkbox is where AI involvement is disclosed, not the commit trailer.

**PR Title Format:**

PR titles must follow one of these formats:
- `type: description` — e.g., `feat: add workflow serialization`
- `[type] description` — e.g., `[feat] add workflow serialization`
- `type-kebab-case` — e.g., `feat-workflow-serialization`

Valid types: `feat`, `fix`, `cookbook`, `test`, `refactor`, `chore`, `style`, `revert`, `release`

**PR Description:**

Always follow the PR template in `.github/pull_request_template.md`. Include:
- Summary of changes
- Type of change (bug fix, new feature, etc.)
- Completed checklist items
- Any additional context

---

## GitHub Operations

**Updating PR descriptions:**

The `gh pr edit` command may fail with GraphQL errors related to classic projects. Use the API directly instead:

```bash
# Update PR body
gh api repos/Saturam-Inc/saturam-agent-framework/pulls/<PR_NUMBER> -X PATCH -f body="<PR_BODY>"

# Or with a file
gh api repos/Saturam-Inc/saturam-agent-framework/pulls/<PR_NUMBER> -X PATCH -f body="$(cat /path/to/body.md)"
```

---

## Don't

- Don't implement features without checking for a design doc first
- Don't use f-strings for print lines where there are no variables
- Don't use emojis in examples and print lines
- Don't skip async variants of public methods
- Don't push code without running `./scripts/format.sh` and `./scripts/validate.sh`
- Don't submit a PR without a detailed PR description. Always follow the PR template provided in `.github/pull_request_template.md`.
- Don't use `OpenAIChat` in cookbooks or examples — use `OpenAIResponses` instead
- Don't use `gpt-4o` or `gpt-4o-mini` in cookbooks or examples — use `gpt-5.5` instead

---

## CI: Automated Code Review

Every non-draft PR automatically receives a review from Opus using both `code-review` and `pr-review-toolkit` official plugins (10 specialized agents total). No manual trigger needed — the review posts as a sticky comment on the PR.

When running in GitHub Actions (CI), always end your response with a plain-text summary of findings. Never let the final action be a tool call. If there are no issues, say "No high-confidence findings."

EGPT-specific checks to always verify:
- Both sync and async variants exist for all new public methods
- No agent creation inside loops (agents should be reused)
- CLAUDE.md coding patterns are followed
- No f-strings for print lines where there are no variables
