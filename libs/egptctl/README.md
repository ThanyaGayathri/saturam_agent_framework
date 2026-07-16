# egptctl

The CLI for [EGPT EgptOS](https://docs.agno.com), built for humans and coding agents.

## EGPT-0.6 migration

See the [environment variable migration guide](#egpt-06-migration-product-strings-and-environment-variables) at the end of this file.

## EGPT-0.6 migration: product strings and environment variables

This release rebrands user-facing control-plane strings from AgentOS to **EGPT** / **EgptOS** and introduces an `EGPT_*` environment-variable prefix.

### Python API

- Preferred import: `from egpt.os import EgptOS`
- `AgentOS` remains available as a deprecated alias for one release and emits a `DeprecationWarning`.

### Environment variables

Read order for each setting:

1. `EGPT_<NAME>` (preferred)
2. `AGNO_<NAME>` (legacy fallback with deprecation warning)
3. Built-in default

| Legacy (`AGNO_*`) | Preferred (`EGPT_*`) |
|-------------------|----------------------|
| `AGNO_ADMIN_TOKEN` | `EGPT_ADMIN_TOKEN` |
| `AGNO_API_RUNTIME` | `EGPT_API_RUNTIME` |
| `AGNO_AUTH_HEADER` | `EGPT_AUTH_HEADER` |
| `AGNO_CLI_CONFIG_DIR` | `EGPT_CLI_CONFIG_DIR` |
| `AGNO_DEBUG` | `EGPT_DEBUG` |
| `AGNO_DEBUG_LEVEL` | `EGPT_DEBUG_LEVEL` |
| `AGNO_ENCRYPTION_KEY` | `EGPT_ENCRYPTION_KEY` |
| `AGNO_INFRA_DIR` | `EGPT_INFRA_DIR` |
| `AGNO_INFRA_NAME` | `EGPT_INFRA_NAME` |
| `AGNO_INFRA_ROOT` | `EGPT_INFRA_ROOT` |
| `AGNO_KEY` | `EGPT_KEY` |
| `AGNO_LOG_TRACEBACKS` | `EGPT_LOG_TRACEBACKS` |
| `AGNO_RUNTIME` | `EGPT_RUNTIME` |
| `AGNO_TELEMETRY` | `EGPT_TELEMETRY` |
| `AGNO_VERSION` | `EGPT_VERSION` |

### Outbound user-agent

HTTP clients built from `egpt.api` now send `egpt/<version>` in the `user-agent` header.

### Deprecation window

Legacy `AGNO_*` variables continue to work for one release. Update deployment manifests and local shell configuration to `EGPT_*` before the next major rebrand milestone.
