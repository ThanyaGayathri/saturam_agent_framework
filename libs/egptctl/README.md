# egptctl

The CLI for [EgptOS](https://github.com/Saturam-Inc/saturam-agent-framework), built for humans and coding agents.

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

## Creating a project (`egpt create`)

`egpt create <name>` scaffolds a new EgptOS project by shallow-cloning a starter
template and stripping its git history:

```bash
egpt create my-os                 # default template (docker)
egpt create my-os -t railway      # pick a deployment target
egpt create my-os -u <git-url>    # any custom template repository
```

Templates live in the `Saturam-Inc` GitHub org (EGPT-0.11); they are rebranded,
EGPT-adapted forks of the upstream `agentos-*` starters:

| Key | Repository | Target |
|-----|------------|--------|
| `docker` (default) | [`egpt-os-docker`](https://github.com/Saturam-Inc/egpt-os-docker) | Self-hosted Docker Compose |
| `aws` | [`egpt-os-aws`](https://github.com/Saturam-Inc/egpt-os-aws) | AWS ECS |
| `fly` | [`egpt-os-fly`](https://github.com/Saturam-Inc/egpt-os-fly) | Fly.io |
| `gcp` | [`egpt-os-gcp`](https://github.com/Saturam-Inc/egpt-os-gcp) | Google Cloud Run |
| `railway` | [`egpt-os-railway`](https://github.com/Saturam-Inc/egpt-os-railway) | Railway |

Notes:

- The template repositories are **private**: `egpt create` runs a plain
  `git clone`, so git must be authenticated with GitHub access to
  `Saturam-Inc` (e.g. `gh auth login` or a credential helper).
- The scaffolded project installs the `egpt` framework from GitHub release
  wheels at image-build time (egpt is not on PyPI), so building or running it
  (`egpt up`) needs a `GITHUB_TOKEN` with read access to the framework
  releases exported in the environment. Each template's README documents this.
- After `egpt create`: `cd <name>`, `cp example.env .env` (fill in secrets),
  `egpt up` to start it, `egpt connect` to register it with your coding agents.

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

Telemetry is **off by default**. Set `EGPT_TELEMETRY=true` and `EGPT_API_URL` (your endpoint) to opt in to phone-home analytics. There is no upstream default endpoint. This does not affect model-provider or tool network calls.

Notes:

- CLI config lives on disk at `~/.config/egpt/config.json`. On first init, if that file is absent and `~/.config/ag/config.json` exists, it is copied once. There is no `EGPT_CLI_CONFIG_DIR` environment variable.
- `AGENTOS_URL` remains the URL override for egptctl discovery (no `EGPT_*` equivalent in this release).
- Infra writers dual-write `EGPT_*` and `AGNO_*` container/local env vars for one release so older packages inside images keep working.

### Wire-visible default identity

When no explicit `id` is set on an EgptOS instance, both the REST `GET /` response and the MCP `get_agentos_config` tool now default to `egpt-os` (via `egpt.brand.DEFAULT_OS_ID`). Clients that keyed sessions on the previous defaults (`agno-agentos` / `AgentOS`) should treat this as a new identity after upgrade, or set an explicit `id`.

### MCP server entry name

`egpt connect` and `egpt status` default `--server-name` to `egpt`. It was `agno` before EGPT-0.12.

This is a clean break, not a migration. An entry already written under the `agno` key is neither rewritten nor deleted, so after upgrading you will have both until you remove the old one:

```bash
egpt connect                    # writes the "egpt" entry
# then delete the stale "agno" entry from the client's config, e.g.
claude mcp remove --scope user agno
```

`egpt status` reports a leftover `agno` entry per client — as `legacy_entry` under `--json`, and as a removal hint in the human output — so you can find the stale ones without grepping each config by hand.

Pass `--server-name agno` to keep writing under the old key instead; the leftover check is skipped in that case, since the old key is then the one you asked for.

### Outbound identity

HTTP clients built from `egpt.api` send `egpt/<version>` in the `user-agent` header. Gemini clients send `egpt/<version>` in `x-goog-api-client`. Knowledge's GitHub loader sends `EGPT-Knowledge`, and MongoDB connections identify as `EGPT` in the driver handshake.

### Deprecation window

Legacy `AGNO_*` variables continue to work for one release. Update deployment manifests and local shell configuration to `EGPT_*` before the next major rebrand milestone.
