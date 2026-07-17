# egptctl

The CLI for [EgptOS](https://docs.agno.com), built for humans and coding agents.

Install the `egptctl` distribution from the workspace or your configured package
index, then run the branded `egpt` command:

```bash
pip install -e libs/egptctl
egpt --help
```

Once `egptctl` is available from the configured package index, name the
distribution explicitly for an ephemeral invocation:

```bash
uvx --from egptctl egpt --help
```

Do not use `uvx egpt`: the public `egpt` distribution is unrelated to this CLI.

## EGPT-0.6 migration: product strings and environment variables

This release rebrands user-facing control-plane strings from AgentOS to **EGPT** / **EgptOS** and introduces an `EGPT_*` environment-variable prefix.

### Python API

- Preferred import: `from egpt.os import EgptOS`
- `AgentOS` remains available as a deprecated alias for one release and emits a `FutureWarning`.
- Preferred client/config: `EgptOSClient`, `EgptOSConfig` (with deprecated `AgentOS*` aliases).

### Environment variables

Read order for each setting that participates in the branded shim:

1. `EGPT_<NAME>` (preferred)
2. `AGNO_<NAME>` (legacy fallback with deprecation warning)
3. Built-in default

Boolean settings other than `LOG_TRACEBACKS` accept only the literal `true` (case-insensitive), matching the pre-EGPT-0.6 grammar. `EGPT_LOG_TRACEBACKS` / `AGNO_LOG_TRACEBACKS` additionally accept `1` and `yes`.

| Legacy (`AGNO_*`) | Preferred (`EGPT_*`) |
|-------------------|----------------------|
| `AGNO_ADMIN_TOKEN` | `EGPT_ADMIN_TOKEN` |
| `AGNO_API_RUNTIME` | `EGPT_API_RUNTIME` |
| `AGNO_AUTH_HEADER` | `EGPT_AUTH_HEADER` |
| `AGNO_DEBUG` | `EGPT_DEBUG` |
| `AGNO_DEBUG_LEVEL` | `EGPT_DEBUG_LEVEL` |
| `AGNO_ENCRYPTION_KEY` | `EGPT_ENCRYPTION_KEY` |
| `AGNO_INFRA_DIR` | `EGPT_INFRA_DIR` |
| `AGNO_INFRA_NAME` | `EGPT_INFRA_NAME` |
| `AGNO_INFRA_ROOT` | `EGPT_INFRA_ROOT` |
| `AGNO_LOG_TRACEBACKS` | `EGPT_LOG_TRACEBACKS` |
| `AGNO_RUNTIME` | `EGPT_RUNTIME` |
| `AGNO_TELEMETRY` | `EGPT_TELEMETRY` |

Telemetry is **off by default**. Set `EGPT_TELEMETRY=true` and `EGPT_API_URL` (your endpoint) to opt in. There is no upstream default endpoint.

Notes:

- CLI config lives on disk at `~/.config/egpt/config.json`. On first init, if that file is absent and `~/.config/ag/config.json` exists, it is copied once. There is no `EGPT_CLI_CONFIG_DIR` environment variable.
- `AGENTOS_URL` remains the URL override for egptctl discovery (no `EGPT_*` equivalent in this release).
- Infra writers dual-write `EGPT_*` and `AGNO_*` container/local env vars for one release so older packages inside images keep working.

### Wire-visible default identity

When no explicit `id` is set on an EgptOS instance, both the REST `GET /` response and the MCP `get_agentos_config` tool now default to `egpt-os` (via `egpt.brand.DEFAULT_OS_ID`). Clients that keyed sessions on the previous defaults (`agno-agentos` / `AgentOS`) should treat this as a new identity after upgrade, or set an explicit `id`.

### Outbound identity

HTTP clients built from `egpt.api` send `egpt/<version>` in the `user-agent` header. Gemini clients send `egpt/<version>` in `x-goog-api-client`.

### Deprecation window

Legacy `AGNO_*` variables continue to work for one release. Update deployment manifests and local shell configuration to `EGPT_*` before the next major rebrand milestone.
