# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## The `agno` / `egpt` split — read this first

This repo is a rebranded fork of Agno. **Directory names still say `agno`; the Python
namespace is `egpt`.**

| Directory | Distribution | Import as |
|-----------|--------------|-----------|
| `libs/agno/egpt/` | `egpt` | `from egpt.agent import Agent` |
| `libs/agno_infra/egpt/` | `egpt-infra` | `from egpt.infra...` |
| `libs/egptctl/egptctl/` | `egptctl` (CLI binary: `egpt`) | `from egptctl...` |

Never write `from agno...` in code, cookbooks, or examples — it will not import.
`scripts/rebrand_namespace.py` is the tool that performed (and re-performs) the
`agno` → `egpt` rewrite when merging upstream.

User-facing product strings live in `libs/agno/egpt/brand.py` (`PRODUCT_NAME = "EGPT"`,
`EgptOS`, env prefix `EGPT_` with `AGNO_` as legacy fallback). Database table prefixes are
deliberately **not** there — see `egpt/db/table_names.py`, since changing them re-points
deployments at empty tables.

> `.cursorrules` still contains upstream `from agno...` examples. Treat its *patterns* as
> authoritative and its *import lines* as stale.

---

## Commands

### Setup

```bash
./scripts/dev_setup.sh    # creates .venv  — egptctl[dev] + agno[dev], for tests/lint
./scripts/demo_setup.sh   # creates .venvs/demo — egptctl + agno[demo], for cookbooks
```

Both wipe and recreate the venv with `uv` + Python 3.12. `.venvs/demo` is not present in a
fresh clone; create it before running cookbooks.

### Format and validate (required before every PR)

```bash
source .venv/bin/activate
./scripts/format.sh      # ruff format + import sort: agno, egptctl, agno_infra, cookbook
./scripts/validate.sh    # ruff check + mypy (agno, egptctl) + ruff + pattern check (cookbook)
```

`validate.sh` deliberately skips `agno_infra` (not in the dev venv) — it has its own CI job
and its own script:

```bash
./libs/agno_infra/scripts/validate.sh
```

Both must exit clean. CI runs the same scripts on Python 3.10 and 3.12.

### Tests

The PR gate is the **unit suite only**:

```bash
./libs/agno/scripts/test.sh                      # pytest libs/agno/tests/unit with coverage
pytest libs/agno/tests/unit                      # same, no coverage
pytest libs/agno/tests/unit/agent/test_x.py      # single file
pytest libs/agno/tests/unit/agent/test_x.py::test_name -v   # single test
pytest libs/agno/tests/unit -n auto              # parallel (pytest-xdist is installed)
```

`asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`.

Three suites, escalating in cost:

| Suite | Path | Needs | Runs |
|-------|------|-------|------|
| unit | `libs/agno/tests/unit` | nothing | every PR |
| integration | `libs/agno/tests/integration` | API keys, live services | nightly / `workflow_dispatch` |
| system | `libs/agno/tests/system` | docker-compose (gateway + remote + adk containers) | manual — `libs/agno/tests/system/run_tests.sh` |

`scripts/test.sh` at the repo root runs the unit gate plus combined coverage.

### Cookbooks

```bash
.venvs/demo/bin/python cookbook/<folder>/<file>.py
./cookbook/scripts/run_pgvector.sh    # and run_redis.sh, run_mongodb.sh, ... as needed
```

---

## Architecture

### Three packages

- **`libs/agno` (`egpt`)** — the framework: agents, teams, workflows, models, tools,
  knowledge, memory, db, and the EgptOS server.
- **`libs/egptctl`** — the `egpt` CLI (`create`, `connect`, `status`, `lifecycle`,
  `tokens`). `egpt create` scaffolds a project by shallow-cloning a starter template from
  the `Saturam-Inc` org.
- **`libs/agno_infra` (`egpt-infra`)** — deployment/infra resources (AWS, Docker).
  Independent dependency set; validated and tested in its own CI job.

### The facade pattern (important when editing)

`Agent`, `Team`, and `Workflow` are large public surfaces implemented as **thin facades over
private sibling modules**. `egpt/agent/agent.py` is a `@dataclass(init=False)` holding
configuration; nearly every method one-lines into `egpt/agent/_*.py`:

| Module | Owns |
|--------|------|
| `_run.py` | the run loop — model calls, tool execution, streaming, cancellation, pause/resume |
| `_messages.py` | system message construction, context assembly |
| `_session.py` / `_storage.py` | session lifecycle and persistence |
| `_tools.py` / `_default_tools.py` | tool resolution and built-in tools |
| `_response.py` | response/event shaping |
| `_managers.py`, `_init.py`, `_hooks.py`, `_telemetry.py`, `_cli.py` | supporting concerns |

`team/` mirrors this exactly. `workflow/` concentrates logic in `workflow.py` plus
`step.py`, `condition.py`, `loop.py`, `router.py`.

**Consequence:** a behavior change almost never belongs in `agent.py` — find the `_*.py`
module. A signature change usually has to be made in both places, and mirrored in `team/`.

### Run types and events

`egpt/run/` defines the wire contract shared by every execution surface: `RunContext`,
`RunStatus`, `RunOutput`, `RunOutputEvent` (per-kind in `agent.py`, `team.py`,
`workflow.py`), plus approval, cancellation, and requirement types. Streaming yields
`RunOutputEvent`s; non-streaming returns a `RunOutput`.

### EgptOS

`egpt/os/app.py` builds a FastAPI app from registered agents/teams/workflows. It
auto-discovers databases and knowledge instances, populates a `Registry`, mounts routers
(`os/routers/`), interfaces (`os/interfaces/` — Slack, AG-UI, A2A, …), MCP
(`os/mcp.py`), auth/RBAC middleware, and tracing.

Registration is **protocol-based**, not subclass-based: anything satisfying
`egpt/agent/protocol.py::AgentProtocol` (`id`, `name`, `arun`) can be registered — native
`Agent`, `RemoteAgent` (`agent/remote.py`), and third-party framework adapters alike. When
adding an agent-like type, satisfy the protocol rather than subclassing `Agent`.

### Pluggable backends

Each of these is a `base.py` contract plus one subpackage per provider — add a provider by
adding a subpackage, never by branching in the base:

- `models/` — `models/base.py::Model` + ~40 providers; `fallback.py` implements
  `call_model_with_fallback` / `acall_model_with_fallback`.
- `db/` — `db/base.py::BaseDb` and `AsyncBaseDb` + postgres, sqlite, mongo, redis, dynamo,
  clickhouse, firestore, surrealdb, json, in-memory, …
- `vectordb/`, `knowledge/` (embedders, readers, chunking, rerankers), `tools/` (140+
  toolkits), `context/` (live context providers: Slack, Drive, Gmail, wiki, fs, MCP, web).

### Distinctive subsystems

`learn/` (self-improvement loop: `machine.py`, `curate.py`, stores), `culture/`
(shared cross-agent knowledge), `skills/` (loadable skill packages), `compression/`
(context compression), `guardrails/`, `approval/` (human-in-the-loop pauses),
`scheduler/`, `eval/`, `tracing/`.

---

## Coding conventions

From `.cursorrules` (patterns valid; imports stale — use `egpt.*`):

- **Never create agents in loops** — construct once, reuse across calls.
- **Every public method needs both variants** — `foo()` and `afoo()`.
- **`output_schema`** for structured responses; `search_knowledge=True` when a `Knowledge`
  is attached.
- **PostgreSQL in production**, SQLite for dev only.
- **Start with a single agent** — reach for `Team` (LLM-coordinated) or `Workflow`
  (programmatic control flow) only when the task actually needs it.
- Ruff line length 120; mypy targets py39 syntax compatibility (`requires-python >=3.9`).

For cookbooks, `cookbook/STYLE_GUIDE.md` is enforced by
`cookbook/scripts/check_cookbook_pattern.py` (run inside `validate.sh`): module docstring,
banner-comment sections, a `Create ...` section before a `Run ...` section, an
`if __name__ == "__main__":` gate, and no emoji. `cookbook/08_learning/` is the golden
standard; each cookbook folder carries a `README.md` and a `TEST_LOG.md`.

---

## Workflow expectations

### Implementing a feature

1. Check `specs/` for a design doc — if one exists, follow it. (`specs/` and `docs/` are
   private symlinks: `ln -s ~/code/specs specs && ln -s ~/code/docs docs`.)
2. Find similar existing code and match its conventions.
3. Add a cookbook example for the pattern.
4. Mark progress in the spec's `implementation.md`.

`.context/` is gitignored — use it for scratch notes and agent-to-agent handoffs.

### Testing cookbooks

Maintaining `cookbook/` is a first-class task, not an afterthought. After running one,
append to that folder's `TEST_LOG.md`:

```markdown
### filename.py

**Status:** PASS/FAIL

**Description:** What the test does and what was observed.

**Result:** Summary of success/failure.

---
```

### Pull requests

Title must match `type: description`, `[type] description`, or `type-kebab-case`, with type
one of `feat`, `fix`, `cookbook`, `test`, `refactor`, `chore`, `style`, `revert`, `release`
(enforced by `.github/workflows/pr-lint.yml`). Body follows
`.github/pull_request_template.md`.

`gh pr edit` can fail with GraphQL errors about classic projects; patch via the API instead:

```bash
gh api repos/Saturam-Inc/saturam-agent-framework/pulls/<PR_NUMBER> -X PATCH \
  -f body="$(cat /path/to/body.md)"
```

### CI code review

Every non-draft PR gets an automated Opus review (`code-review` + `pr-review-toolkit`
plugins) posted as a sticky comment. When running inside GitHub Actions, end the response
with a plain-text summary — never a tool call. If nothing surfaced, say
"No high-confidence findings." Always verify: sync **and** async variants for new public
methods, no agent construction in loops, no f-strings without interpolation.

---

## Don't

- Don't write `from agno...` — the namespace is `egpt`.
- Don't put logic in `agent.py` / `team.py` that belongs in a `_*.py` sibling.
- Don't add a model/db/vectordb provider by branching inside `base.py`.
- Don't implement a feature without checking `specs/` first.
- Don't use f-strings for print lines with nothing to interpolate.
- Don't use emojis in examples or print lines.
- Don't skip async variants of public methods.
- Don't push without `./scripts/format.sh` and `./scripts/validate.sh` passing.
- Don't use `OpenAIChat` in cookbooks or examples — use `OpenAIResponses`.
- Don't use `gpt-4o` or `gpt-4o-mini` in cookbooks or examples — use `gpt-5.5`.

---

## Keep in sync

`AGENTS.md` is a near-copy of this file for non-Claude agents. When you change guidance
here that isn't Claude-specific, mirror it there.
