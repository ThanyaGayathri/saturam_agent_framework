# Tenant Capability Policy — normative reference

**Status:** EGPT-2.3. Companion to [component-config-schema.md](./component-config-schema.md),
which covers whether a definition is *valid*. This document covers whether its tenant is
*permitted* to compose it.

The two are deliberately separate concerns, and both are separate again from the RBAC scopes in
`os/scopes.py`:

| Layer | Question it answers | Lives in |
|---|---|---|
| RBAC scopes | May this caller invoke this endpoint? | `egpt/os/scopes.py` |
| Config schema | Is this definition well-formed and hydratable? | `egpt/registry/schema.py` |
| **Capability policy** | **May this tenant reference these capabilities?** | **`egpt/registry/policy.py`** |

---

## 1. Why this exists

A DB-authored agent definition is a document a tenant writes and the platform executes. Without a
capability gate, a tenant can put `ShellTools` in that document and obtain arbitrary command
execution inside the platform — `SQLTools` for arbitrary SQL, `FileTools` for the filesystem,
`DockerTools` for the host daemon. Schema validation does not help: such a definition is
perfectly well-formed.

---

## 2. Activation — the policy is opt-in

**With no policy configured, nothing changes.** No capability check runs, no extra database read
happens, and no request can fail in a way it could not before. This is the deliberate reading of
"privileged tools are blocked by default": default-deny applies *within* the feature, once an
operator turns it on. Enabling the denylist unconditionally would reject definitions that
legitimately wire `PythonTools` today.

A policy is in force when either of these is true, checked in this order:

1. `EgptOS(capability_policy=...)` was passed a `CapabilityPolicy` (or `Registry.capability_policy`
   was set directly).
2. `EGPT_CAPABILITY_POLICY_FILE` (falling back to `AGNO_CAPABILITY_POLICY_FILE`) points at a
   document.

An explicitly passed policy always wins, including over one a previous `EgptOS` parked on a shared
registry.

**A configured path that does not exist raises at `EgptOS` construction.** It does *not* degrade
to "no policy" — a typo silently switching the security control off is the failure this whole
feature exists to prevent.

---

## 3. The document

JSON. `EGPT_CAPABILITY_POLICY_FILE=/etc/egpt/capability-policy.json`

```json
{
  "version": 1,
  "mode": "enforce",
  "unknown_tenant": "defaults",

  "defaults": {
    "models": ["openai:*", "anthropic:*"],
    "tools": ["get_weather", "search_docs"],
    "mcp_servers": [],
    "granted_privileged": []
  },

  "tenants": {
    "acme": {
      "models": ["openai:gpt-5.5"],
      "tools": ["*"],
      "mcp_servers": ["mcp_docs_internal"],
      "granted_privileged": ["sql"]
    },
    "beta-labs": {
      "models": ["openai:*"]
    },
    "locked-down": {
      "models": [],
      "tools": [],
      "mcp_servers": []
    }
  }
}
```

### Semantics

| Rule | Behaviour |
|---|---|
| Key **omitted** in a tenant block | Inherits `defaults`' value. `beta-labs` above inherits `defaults.tools`, `mcp_servers` and `granted_privileged`. |
| Key **present** in a tenant block | **Replaces** `defaults`' value outright. There is no merge syntax — a union is written out, so a tenant block shows that tenant's whole permitted surface without also holding `defaults` in your head. |
| `null` or omitted at the top level | **Unrestricted** for that capability class. |
| `[]` | **Nothing permitted** of that class. |
| `"*"` in `models` / `tools` / `mcp_servers` | Allows everything of that class. |
| `"*"` in `tools` | **Does not unlock any privileged toolkit.** That is the entire point of keeping the grant on its own key: `acme` above has `tools: ["*"]` and may still use only `sql` from the denylist. |
| `"*"` in `granted_privileged` | **Rejected at load.** An operator who wants every privileged toolkit lists all eleven, so the grant shows up in the diff. |
| `version` greater than the framework's | Rejected at load. Fail closed on a document this build cannot read. |
| Any unknown top-level or per-tenant key | Rejected at load. A typo'd `"priviledged_tools"` must not silently mean "no grants". |
| `unknown_tenant` | `"defaults"` (default) — an unlisted tenant id gets `defaults`. `"deny"` — it gets the empty policy. |
| `mode` | `"enforce"` (default) rejects. `"warn"` logs each violation and allows — see §7. |

### `models` entry grammar

- `"openai"` — any model from that provider
- `"openai:gpt-5.5"` — that exact model
- `"openai:gpt-5*"` — glob on the model id (case-insensitive)
- `"openai:*"` — same as the bare provider slug
- `"*"` — any model

The provider half is normalized through `resolve_provider_key` — the same function the config
validator uses — so `"OpenAI"`, `"openai"` and the registered aliases all resolve to one key, and
the policy cannot disagree with the validator about what a provider is.

### `mcp_servers` entry grammar

The MCP **toolkit's name**: the `mcp_<slug>` that `get_default_toolkit_name` derives from the
server URL or command, or whatever was passed as `MCPTools(name=...)`. Globs are allowed
(`"mcp_https_*_acme_com_*"`). An MCP server is one capability regardless of how many remote tools
it exposes, and its tools are matched against `mcp_servers` — never against `tools`.

---

## 4. The privileged inventory

These are denied to tenant authors unless the tenant's `granted_privileged` names them. The left
column is the grant slug.

<!-- privileged-toolkits: pinned against PRIVILEGED_TOOLKITS by tests/unit/registry/test_capability_policy.py -->

| Grant slug | Package | Class | Why it is privileged |
|---|---|---|---|
| `shell` | `egpt.integrations.tools.shell` | `ShellTools` | arbitrary command execution on the host |
| `python` | `egpt.integrations.tools.python` | `PythonTools` | arbitrary code execution and package installation |
| `docker` | `egpt.integrations.tools.docker` | `DockerTools` | container and image control on the host daemon |
| `e2b` | `egpt.integrations.tools.e2b` | `E2BTools` | remote sandbox execution and filesystem access |
| `daytona` | `egpt.integrations.tools.daytona` | `DaytonaTools` | remote sandbox execution and filesystem access |
| `sql` | `egpt.integrations.tools.sql` | `SQLTools` | arbitrary SQL against a configured database |
| `postgres` | `egpt.integrations.tools.postgres` | `PostgresTools` | arbitrary SQL against a configured database |
| `duckdb` | `egpt.integrations.tools.duckdb` | `DuckDbTools` | arbitrary SQL plus local and S3 file access |
| `redshift` | `egpt.integrations.tools.redshift` | `RedshiftTools` | arbitrary SQL against a configured data warehouse |
| `bigquery` | `egpt.integrations.tools.google.bigquery` | `GoogleBigQueryTools` | arbitrary SQL against a configured data warehouse |
| `api` | `egpt.integrations.tools.api` | `CustomApiTools` | arbitrary outbound HTTP from the platform's network position |
| `aws_lambda` | `egpt.integrations.tools.aws_lambda` | `AWSLambdaTools` | invocation of arbitrary cloud functions |
| `aws_ses` | `egpt.integrations.tools.aws_ses` | `AWSSESTool` | outbound email from the platform's identity |
| `file` | `egpt.integrations.tools.file` | `FileTools` | local filesystem read and write |
| `local_file_system` | `egpt.integrations.tools.local_file_system` | `LocalFileSystemTools` | local filesystem read and write |
| `file_generation` | `egpt.integrations.tools.file_generation` | `FileGenerationTools` | writing generated files to the local filesystem |

`bigquery` is listed at *module* rather than package granularity, because
`tools/google/` also holds calendar, drive, gmail, maps, sheets and slides — matching is by
module prefix, so naming the package would sweep all of them in.

**The inventory is name-based, and a toolkit not in this table is unprivileged by default.**
That is the important thing to understand before relying on it: this is a denylist, not an
exhaustive account of every privileged capability in the tree. A toolkit added tomorrow is an
ordinary tool until someone adds a row. To stop that being purely a matter of remembering,
`test_no_toolkit_in_the_tree_exposes_a_sensitive_verb_without_being_listed` walks every toolkit
module's AST for a set of verbs that are privileged wherever they appear (`run_query`,
`make_request`, `execute`, `eval`, `run_sql_query`, `run_shell_command`) and fails on any that is
neither listed here nor explicitly recorded as reviewed-and-benign with a reason. Widen that verb
set as new capability shapes appear.

`egpt/registry/policy.py` is **core**, and core must not import integrations. These package paths
are therefore compared as strings and never imported; a layering guard
(`tests/unit/registry/test_schema_layering.py`) keeps it that way — and the inventory guard above
parses the toolkit tree rather than importing it, for the same reason.

---

## 5. How a tool name is classified

This is the sharp part of the design, and the reason for the rules that follow.

**A stored config does not record which toolkit a tool came from.** `parse_tools` flattens every
`Toolkit` into its individual `Function`s before serialization, and `Function.to_dict` emits seven
keys — `name`, `description`, `parameters`, `strict`, `requires_confirmation`,
`external_execution`, `approval_type` — none of which is a toolkit, module or qualname. So a
config says `read_file`, and `read_file` is a function of `FileTools`, `PythonTools`, `E2BTools`
*and* `DaytonaTools`.

Classification recovers what it can, in this order:

1. **Live registry evidence wins.** Every `Toolkit` in `Registry.tools` that exposes the name is
   collected. Each is classified by walking its **MRO** and matching `__module__` against the
   package prefixes in §4 — the MRO walk, not just the concrete class, so a user's
   `class MyShell(ShellTools)` in their own package is still `shell`. MCP toolkits are detected by
   MRO class name (`MCPTools` / `MultiMCPTools`) and classified under their server name.
2. **A name the registry resolves but no toolkit owns** is a bare `Function` or callable — a
   tenant's own tool. It is **not** run through the static table: a tenant's `def read_file` is
   theirs, and blocking it on a name collision with `FileTools` would make the policy unusable.
3. **Only when the registry cannot answer at all** (no registry wired, or the name is unknown to
   it) does the static function-name table apply. That is precisely the case where nothing else
   can see a `run_shell_command` for what it is.

### Ambiguity fails closed

If a name maps to more than one privileged toolkit, **every** candidate must be granted. So
`run_shell_command` with no registry wired requires both `shell` and `daytona`.

This is load-bearing rather than cautious. `Registry`'s entrypoint lookup is keyed by function
name and is **last-wins**, warning on a collision rather than refusing it — so with `ShellTools`
and `DaytonaTools` both registered, which `run_shell_command` actually executes depends on
registration order. A classifier that picked one would be guessing which sandbox the agent escapes
into.

**To narrow it:** register the specific toolkit on the `Registry`. With a real `ShellTools`
registered, `run_shell_command` resolves to `shell` alone and granting `shell` is enough.

---

## 6. Where enforcement happens

Both write and hydration, from one walker — a second implementation would drift, and the whole
point of checking twice is that the two checks agree.

### On write (422)

`POST /components`, `POST /components/{id}/configs`, `PATCH /components/{id}/configs/{version}`.
Violations arrive as ordinary field-level errors in the existing
`ConfigValidationErrorResponse` shape, alongside any schema errors — they accumulate rather than
short-circuiting, so one response shows everything wrong:

```json
{
  "detail": "Invalid agent configuration: 2 errors.",
  "error_code": "CONFIG_VALIDATION_ERROR",
  "schema_version": 1,
  "errors": [
    {"path": "config.model", "message": "Model 'anthropic:claude-sonnet-5' is not permitted for this tenant. Permitted models: openai.", "code": "capability_not_permitted"},
    {"path": "config.tools[0].name", "message": "Tool 'run_shell_command' belongs to the privileged toolkit 'shell' (arbitrary command execution on the host), which is blocked for tenant-authored definitions. Add it to this tenant's 'granted_privileged' to allow it.", "code": "privileged_tool_blocked"}
  ]
}
```

A policy-rejected config is never dry-run: the capability check runs before hydration is attempted.

### At hydration (403)

Every path that turns a stored config into a live component:

| Path | Where the gate is |
|---|---|
| `get_agent_by_id` / `get_agents` | `agent/agent.py` |
| `get_team_by_id` / `get_teams` | `team/team.py` |
| `get_workflow_by_id` / `get_workflows` | `workflow/workflow.py` |
| `Agent.load` | `agent/_storage.py::load` |
| `Workflow.load` | `workflow/workflow.py::load` |
| `Team.load`, nested teams, **and member agents** | `team/_storage.py::_hydrate_from_graph` |
| `StudioTools` loaders (all three) | `tools/studio.py::_load_config_from_db` |

The `load()` methods matter as much as the `get_*` helpers: `load` is the natural entry point for
anything programmatic — a migration script, a CLI, a test fixture — and those are exactly the
callers that will not have a policy in mind.

A team's **member agents** are gated where the graph is hydrated, so wrapping a disallowed agent
in a team is not a way to launder it past the check.

- **A single fetch raises**, and the api layer maps it to **403**. Not 404: the component exists
  and hydrates fine; the caller is just not permitted it.
- **A listing omits the offender with a warning** rather than failing. One poisoned row must not
  empty a whole page, and returning it would be the leak.
- Only the *policy* is re-checked — not shape, not references, not a dry run. Re-running the full
  validator at read time would reject every stored config predating a schema tweak and would cost
  a hydration per request.
- The tenant comes off the config row already fetched (`get_config` returns the `tenant_id`
  column), so this costs no extra query.

---

## 7. Rolling it out

Turning a policy on against an existing corpus can strand definitions that were written before it.
`"mode": "warn"` is the lever: the walker still runs and every violation is logged with its tenant
and capability, but nothing is rejected at either layer.

```json
{ "mode": "warn", "defaults": { "models": ["openai:*"] } }
```

Audit the logs (`[capability-policy:warn]`), fix or grant what turns up, then switch to
`"enforce"`.

---

## 8. Tenant resolution

Minimal by design in this release. Broader tenant scoping is EGPT-2.4.

```python
EgptOS(
    db=db,
    authorization=True,
    authorization_config=AuthorizationConfig(tenant_id_claim="tid"),
    capability_policy=CapabilityPolicy.from_file("/etc/egpt/capability-policy.json"),
)
```

The chain: JWT claim named by `tenant_id_claim` → `request.state.tenant_id` → read by
`egpt.os.tenancy.get_tenant_id` → passed to the components router's validation *and* to every
component DB call it makes.

- **`tenant_id_claim` unset** (the default) means no tenant is ever resolved. `tenant_id` is
  `None` everywhere, which applies no DB tenant predicate and selects the policy's `defaults`
  block — identical to pre-EGPT-2.3 behaviour.
- **A claim that is present but not a non-empty string is a 401**, not a fallback to `None`.
  `None` is the *widest* tenancy, so degrading to it would turn a malformed claim into an
  escalation.
- **`None` selects `defaults`**, not deny-all (which would break every deployment the moment it
  wrote its first policy file) and not allow-all (which would make the denylist bypassable by
  dropping the claim). To refuse unscoped traffic, write an empty `defaults`, or set
  `unknown_tenant: "deny"` and enumerate your tenants.

### Delivered by EGPT-2.4

- **Service-account tokens carry a tenant.** `ServiceAccount.tenant_id` is stamped at mint time
  and attached to `request.state.tenant_id` on every authenticated request. See
  [tenancy.md](tenancy.md).
- **Unscoped principals are refused on the component routes** once `tenant_id_claim` is
  configured. The third bullet above still describes what `None` *means*; what changed is that
  the components router no longer serves a caller who resolves to it. Other routers are
  unchanged.

The scheduler principal stays `None` deliberately — it is a platform identity, not a tenant's,
and `INTERNAL_SERVICE_SCOPES` grants it no `components:*` scope in any case.

### Delivered by EGPT-2.5

- **Runtime hydration is tenant-scoped.** `get_agent_by_id`, `Agent.load`, the team/workflow
  equivalents and the studio toolkit read stored configs with the caller's tenant predicate,
  and the hydration-side policy check selects the policy by the **caller's** tenant rather
  than the row's self-attested one (the row's tenant remains the fallback for unscoped
  reads). See [tenancy.md](tenancy.md) section 5.
- The write-validation dry run threads the tenant into member/step hydration, so validation
  is no longer an existence oracle for foreign component ids.

### Delivered by EGPT-2.7

- Row-level scoping enforcement in the data layer: every component DB method requires a
  `tenant_id` and fails closed without one. See [tenancy.md](tenancy.md) section 6.

### Deferred beyond EGPT-2.7

- Tenant scoping of session, memory and run data — those tables have no `tenant_id` column, so it
  is a schema change with a migration rather than an enforcement change.
- Tenant-aware RBAC scopes and cross-tenant read enforcement on run/session data.
- Any tenants table, tenant CRUD, or a policy stored in the database.
- Backfill *tooling* for rows carrying a NULL `tenant_id`. There is no tenants table to backfill
  against, so which tenant inherits pre-tenancy rows is a question only the operator can answer;
  [tenancy.md](tenancy.md) section 6 carries the SQL instead.

---

## 9. Error codes

Both belong to the closed vocabulary in
[component-config-schema.md §7](./component-config-schema.md#7-error-codes).

| Code | Fires when |
|---|---|
| `capability_not_permitted` | A model, tool or MCP server outside this tenant's allow-list |
| `privileged_tool_blocked` | A tool belonging to a privileged toolkit this tenant has not been granted |

---

## 10. Known limits

- **Top-level `model` and `tools` only.** A workflow step's own definition is checked when *that*
  component is hydrated, not transitively from its parent. (Team **members** are the exception —
  they are gated with the parent, because `Team.load` builds them straight from the component
  graph rather than going through `get_agent_by_id`.)
- **Classification depends on whether a `Registry` is present, so the write and hydration paths
  can in principle disagree.** The write path always has one; hydration usually does (`EgptOS`
  populates `os.registry` and parks the policy on it), but a bare programmatic caller may not.
  Where they differ, the registry-less backstop is normally the *stricter* answer — an
  unregistered `run_shell_command` needs both `shell` and `daytona` granted, where a registered
  one needs only `shell`. The one permissive direction is a function that a privileged toolkit
  exposes but that is not in the static name table — a subclass adding a method, say: the
  registry would catch it and the backstop would not. Register privileged toolkits on the
  `Registry` if you want the two paths to agree exactly.
- **An unconnected MCP toolkit escapes `mcp_servers`.** Its `Function`s do not exist until
  `build_tools()` runs, so at classification time the name resolves to nothing and falls through
  as an ordinary tool. Mitigated in the served path (`EgptOS` connects MCP toolkits at startup)
  and on the write path (`StudioTools` refuses to persist a component with an unconnected MCP
  toolkit), but the residual hole is real. It exists because `Function.to_dict` drops the owning
  toolkit; carrying toolkit provenance through serialization is a schema change and a follow-up.
- **A privileged toolkit registered only as loose `Function`s** — `Registry(tools=[
  shell_tools.functions["run_shell_command"]])` — has no owning `Toolkit`, so rule 2 of §5
  classifies it as a benign user function. This is the deliberate cost of not blocking a tenant's
  own `def read_file`.
- **Hydration reads the config row's `tenant_id`.** Writes stamp both it and the component row so
  they agree, but a manual SQL edit could diverge them.

---

## 11. Programmatic access

```python
from egpt.registry.policy import (
    CapabilityPolicy,           # .from_dict / .from_file / .disabled / .for_tenant
    PolicyViolationError,       # raised at hydration; carries .violations
    PRIVILEGED_TOOLKITS,        # slug -> PrivilegedToolkit
    check_capabilities,         # the walker: -> list[PolicyViolation]
    classify_tools,             # name -> ToolOrigin
    enforce_policy_or_raise,    # the hydration-side entry point
    resolve_policy,             # explicit -> registry -> environment
)

policy = CapabilityPolicy.from_file("/etc/egpt/capability-policy.json")
for violation in check_capabilities(config, "agent", policy=policy, tenant_id="acme", registry=registry):
    print(violation.code, violation.path, violation.message)
```

Run [`cookbook/93_components/capability_policy.py`](../cookbook/93_components/capability_policy.py)
to see all of this offline, without a server.
