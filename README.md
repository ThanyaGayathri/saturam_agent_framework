<div align="center" id="top">
  <h1>EGPT</h1>
</div>

<p align="center">
  Build, run, and manage agent platforms.<br/>
</p>

## Introduction

EGPT is Saturam's SDK for building agent platforms.

- Build agents using any agent framework.
- Run them as production services with tracing, scheduling, and RBAC.
- Manage using a single control plane.

EGPT allows you to own your agent stack. Maintain control of your data, context,
tools, permissions, memory and human-review loops, and run your platform in your
own cloud.

EGPT is maintained by Saturam as a private fork of the upstream Agno framework.
See [PROVENANCE.md](PROVENANCE.md) for the source project, version and licence.

## Get started

`egpt` is not published to public PyPI — it is installed from this repository's
sources or from private release assets. Do **not** run `pip install egpt`; that
name on public PyPI is an unrelated package.

```bash
# Development environment (tests, formatting, validation)
./scripts/dev_setup.sh

# Cookbook environment, with all example dependencies
./scripts/demo_setup.sh
.venvs/demo/bin/python cookbook/00_quickstart/agent_with_tools.py
```

- Start with the guided quickstart: [cookbook/00_quickstart](cookbook/00_quickstart/README.md)
- Package-level install and usage notes: [libs/agno/README.md](libs/agno/README.md)
- Browse the examples: [cookbook/](cookbook/README.md)

## Features

- **Production API.** 50+ endpoints with SSE and websockets to build a product on top.
- **Storage.** Store sessions, memory, knowledge, and traces in your own database.
- **100+ integrations.** Integrate with 100+ tools using pre-built toolkits.
- **Context Providers.** Access live data from Slack, Drive, wikis, MCP, and custom sources.
- **Human approval.** Pause runs for user confirmation. Block tools that require admin approval.
- **Observability.** Get monitoring via OpenTelemetry tracing, run history, and audit logs out of the box.
- **Security.** Get JWT-based RBAC and multi-user, multi-tenant isolation out of the box.
- **Interfaces.** Expose your agents via Slack, Telegram, WhatsApp, Discord, AG-UI, A2A.
- **Scheduling.** Cron-based scheduling and background jobs with no external infrastructure.
- **Deploy anywhere.** Run on any cloud platform that runs containers. Docker, Railway, AWS, GCP.

## Use EGPT with your coding agent

Run an EgptOS server and connect a coding agent to it with `egpt connect` (shipped
by the `egptctl` distribution). The server exposes interactive API docs at
`http://localhost:7777/docs`.

## Contributing

See the [contributing guide](CONTRIBUTING.md).

## Telemetry

Telemetry (phone-home of run/OS analytics events) is **off by default**. No telemetry
events are sent to any external service unless you explicitly opt in. This does not
disable model-provider API calls or other tools you configure.

```bash
export EGPT_TELEMETRY=true
export EGPT_API_URL=https://your-telemetry-endpoint.example.com
```

Legacy `AGNO_TELEMETRY` / `AGNO_API_URL` are still accepted with a deprecation warning.

<p align="right"><a href="#top">↑ Back to top</a></p>
