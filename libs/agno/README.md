# egpt

The core EGPT framework: build agents, teams and workflows, and run them as
production services with tracing, scheduling, memory and RBAC.

## Install

`egpt` is published to a private channel as GitHub Release assets, not to public
PyPI. Do **not** run `pip install egpt` — the name `egpt` on public PyPI is an
unrelated package.

```bash
./scripts/install_from_github_release.sh egpt
```

This installs the full first-party closure (`egpt`, `egptctl`, `egpt-infra`) from
authenticated release assets, then resolves the remaining third-party
requirements normally. It needs `GITHUB_TOKEN` (or `GH_TOKEN`) with `contents:read`
on `Saturam-Inc/saturam-agent-framework`.

## Quickstart

```python
from egpt.agent import Agent
from egpt.models.openai import OpenAIResponses

agent = Agent(model=OpenAIResponses(id="gpt-5.5"))
agent.print_response("What can you do?")
```

Serve one or more agents as an EgptOS instance:

```python
from egpt.os import EgptOS

os_app = EgptOS(agents=[agent])
os_app.serve()
```

Then connect a coding agent to it with the `egpt` CLI (from the `egptctl`
distribution):

```bash
egpt connect
```

## Optional dependencies

Feature areas ship as extras — `egpt[os]`, `egpt[scheduler]`, `egpt[slack]`,
`egpt[telegram]`, `egpt[clickhouse]`, and one per model provider and tool. Because
the distribution is not on public PyPI, install the underlying third-party package
directly when you only need one:

```bash
pip install croniter pytz     # what egpt[scheduler] would pull in
```

## Documentation

Design documents and architecture decision records live in `docs/adr/` in the
repository. Runnable examples live in `cookbook/`.

## License

Apache-2.0. See `LICENSE`.
