# Component Config Schema — normative reference

The schema and validation rules for the config blob that defines an agent, team or workflow as
data. This is the reference the components API is written against; for a runnable tour see
[`cookbook/93_components/`](../cookbook/93_components/README.md).

The one-line rule:

> **A config that would not hydrate must not be stored.**
> Validation runs on write, so an unrunnable definition is rejected at authoring time instead of
> failing in the middle of someone's run.

Implemented in [`libs/agno/egpt/registry/schema.py`](../libs/agno/egpt/registry/schema.py) (the
schema and every check answerable from core) and
[`libs/agno/egpt/os/routers/components/validation.py`](../libs/agno/egpt/os/routers/components/validation.py)
(the api-layer half that supplies the component contract). The split exists because
`egpt.registry` is core and cannot import orchestration — see [ADR 0001](adr/0001-layered-architecture.md).

---

## 1. What a config is

A config is the dict produced by `Agent.to_dict()` / `Team.to_dict()` / `Workflow.to_dict()` and
consumed by the matching `from_dict()`. That serialization pair is the source of truth; this
schema describes it rather than restating it.

Configs are persisted in the `component_configs.config` column (JSONB), one row per
`(component_id, version)`. The stored bytes are exactly what the caller submitted, plus a
`schema_version` key — **the validator never rewrites a config**. Pydantic's coerced output is
deliberately discarded so the write → read → `from_dict` round trip is byte-stable.

---

## 2. Versioning

`schema_version` is the version of *this schema*, not of the stored config row (that is
`component_configs.version`, an unrelated counter).

| Rule | Behaviour |
|---|---|
| Current version | `SCHEMA_VERSION = 1` |
| Who stamps it | All three `to_dict()`s, **and** the validator on every accepted write |
| Absent | Read as pre-versioning. v1 describes what `to_dict` already emitted before the key existed, so an unversioned config is structurally a v1 and validates as one |
| Not an integer | Rejected — `invalid_type`. (`schema_version: true` is a mistake, not version 1; `bool` is an `int` subclass, so it is excluded explicitly) |
| Higher than `SCHEMA_VERSION` | Rejected — `unsupported_schema_version`. An older node refuses a newer config rather than silently downgrading it |
| On read | `from_dict()` ignores the key, so `to_dict(from_dict(config)) == config` still holds |

Because `to_dict()` stamps it too, configs written by `save()` — which bypasses the API entirely —
are self-describing as well.

**Forward compatibility.** `migrate(config, component_type)` brings a config forward before
validation by applying `_MIGRATIONS[from_version]` in ascending order. The table is empty at v1;
it is the seam where a v1 → v2 normalizer goes so old rows keep validating after a bump.

---

## 3. Where validation runs

Semantic validation runs on **every write path that carries a config**:

| Endpoint | Validated |
|---|---|
| `POST /components` | yes — when `config` is non-empty |
| `POST /components/{id}/configs` | yes |
| `PATCH /components/{id}/configs/{version}` | yes |
| `PATCH /components/{id}` | no — metadata only, it carries no config |
| all `GET` endpoints | no |

Two deliberate non-behaviours:

- **An empty config is accepted.** A shell component whose members or steps are added later is a
  legitimate flow, so there is nothing to validate yet.
- **Stored configs are not retro-validated.** This is a write-path gate. Rows written before the
  gate existed, or by a `save()` that bypasses the API, are not re-checked on read.

Validation runs *after* the `db` reference is resolved, so it sees exactly the bytes that get
persisted.

---

## 4. The schema

### 4.1 Fields common to all three component types

| Field | Type | Constraint |
|---|---|---|
| `schema_version` | int | see §2 |
| `id` | str | optional — the component row already carries `component_id` |
| `name` | str | optional |
| `description` | str | optional |
| `instructions` | str or list[str] | optional |
| `metadata` | dict | optional |
| `session_state` | dict | optional |
| `dependencies` | dict | optional |
| `debug_level` | int | one of `1`, `2` |
| `num_history_runs` | int | `>= 0` |
| `num_history_messages` | int | `>= 0` |
| `retries` | int | `>= 0` |

### 4.2 Agent

`model` is **the only required field** in the whole schema. It is what the definition is hydrated
around; everything else has a working default.

| Field | Type | Constraint |
|---|---|---|
| `model` | [ModelRef](#45-reference-shapes) or `"<provider>:<model_id>"` string | **required** |
| `tools` | list[[ToolRef](#45-reference-shapes)] | |
| `db` | [DbRef](#45-reference-shapes) | |
| `knowledge` | [KnowledgeRef](#45-reference-shapes) | |
| `knowledge_filters` | dict | |
| `input_schema` | registered schema name (str) or inline JSON Schema (dict) | non-empty if inline |
| `output_schema` | registered schema name (str) or inline JSON Schema (dict) | non-empty if inline |
| `learning` | bool or serialized learning machine (dict) | |
| `max_tool_calls_from_history` | int | `>= 0` |
| `tool_call_limit` | int | `>= 1` |
| `reasoning_min_steps` | int | `>= 1` |
| `reasoning_max_steps` | int | `>= 1` |

### 4.3 Team

| Field | Type | Constraint |
|---|---|---|
| `members` | list[[MemberRef](#45-reference-shapes)] | validated structurally; see [known limits](#10-known-limits) |
| `mode` | str | |

A team config with no members is **not** an error — creating a team and adding its members
afterwards is a legitimate flow. It logs a warning.

### 4.4 Workflow

| Field | Type | Constraint |
|---|---|---|
| `steps` | list[dict] | each `type` must be one of `Step`, `Steps`, `Loop`, `Parallel`, `Condition`, `Router`; defaults to `Step` when absent |

### 4.5 Reference shapes

Non-serializable objects — tools, knowledge bases, schema classes, live db handles — are stored
**by reference** and supplied at hydration time by the `Registry`.

```jsonc
// ModelRef — as emitted by Model.to_dict()
{"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}   // id required
// or the shorthand string form:
"openai:gpt-5.5"

// ToolRef — as emitted by Function.to_dict()
{"name": "web_search"}   // name required; parameters/description/strict/... optional

// DbRef — an id naming a registered db, a type to construct one, or both
{"id": "os-db"}                                    // reference a registered db
{"type": "postgres", "db_url": "postgresql://..."} // construct one
// Left open: connection and table-name fields differ per backend.

// KnowledgeRef — knowledge is referenced by name; the instance holds the live connections
{"name": "product-docs"}

// MemberRef — a team member
{"type": "agent", "agent_id": "researcher"}
{"type": "team",  "team_id": "research-pod"}
```

### 4.6 Why these tables are not exhaustive, on purpose

The tables above carry the required fields, the reference shapes, and the fields with a real value
domain. They are **not** a restatement of every serializable setting — that would duplicate
`Agent.__init__` and drift from it within a release.

The scalar long tail passes through (`extra="allow"`), and the allowed key set is computed at
runtime rather than listed: the constructor's parameters, from `inspect.signature`, union a small
set of keys the constructor does not take but a serialized component still carries —
`schema_version`, plus ownership and session-history fields that vary by component type. A new
setting is therefore valid the moment it exists, with no schema edit.

The two numbers differ, so it is worth being exact about which is which:

<!-- key-counts: asserted against the live values by TestDocumentedKeyCounts -->

| Component | Constructor parameters | Config keys accepted |
|---|---|---|
| `agent` | 115 | 119 |
| `team` | 120 | 122 |
| `workflow` | 27 | 28 |

Those counts are pinned by test, not maintained by hand — if they no longer match the live
signatures, the suite fails rather than the document quietly going stale.

What this buys you is the other half: a key that is *not* a constructor parameter is rejected.
`from_dict` builds its kwargs from an explicit list, so an unknown key is not merely ignored, it is
*silently* ignored — which is how `instruction:` for `instructions:` survives all the way to a run
that quietly has no instructions. The validator catches it with a spelling suggestion.

---

## 5. Reference resolution

### 5.1 `model`

The string form must be `"<provider>:<model_id>"`; anything else is `invalid_format`. The object
form must carry an `id`.

The provider is then resolved in two steps, **importing nothing**: a live instance on the
`Registry` settles it outright; otherwise `discovery.get("model", key)` answers from the
declaration table, which returns a registration without calling `load()`. 59 providers are
registered today.

A config naming a provider whose SDK is not installed therefore **passes** this check — that
condition surfaces in the dry run as `provider_not_installed`, which is a different problem with a
different fix.

### 5.2 `tools`

Each entry needs a `name`. `parameters`, when present, must be a JSON Schema object.

The name is then checked against `registry.has_entrypoint(name)`. A tool is stored by name only,
so if nothing in the registry can supply its entrypoint the agent would load holding a tool it
cannot call.

### 5.3 `db`

A `DbRef` needs an `id` (to reference a registered db) or a `type` (to construct one); neither is
`missing_required_field`. A `type` is checked against the registered backends, which are whatever
`discovery.names("db")` reports — today:

<!-- db-types: asserted against discovery.names("db") by TestDocumentedDbTypes -->

```text
clickhouse, dynamo, firestore, gcs_json, in_memory, json, mongo, mysql, postgres, redis,
singlestore, sqlite, surrealdb
```

That list is pinned by test too: registering a fourteenth backend fails the suite until it is
listed here.

An `id` is resolved by the router's `_resolve_db_in_config` before validation — against the OS
database, then the `Registry`. If it resolves, the referenced db's connection fields (`type`,
`db_url`, `db_file`, `db_schema`, `id`) always win, and only `table_prefix` and the per-table name
keys may be overridden by the caller, so a config cannot redirect a referenced db to another
backend. If it does not resolve, the id comes back to the validator and is reported at `db.id`.

### 5.4 `knowledge`, `input_schema`, `output_schema`, `learning`

- `knowledge` must be `{"name": ...}`, and the name must be registered on the `Registry`.
- `input_schema` / `output_schema` are either the name of a registered schema class, or an inline
  JSON Schema object (which must be non-empty). Anything else is `invalid_type`.
- `learning` must be a bool or a serialized learning machine.

### 5.5 When reference checks are skipped

**Checks that consult the `Registry` — tools, knowledge, schemas, and the registry half of the
model check — do not run when there is no `Registry`.**

This is intentional and worth knowing before you write a test that expects a rejection. With
nothing registered, "this name is wrong" and "this process never wired a registry" are
indistinguishable, and a validator that cannot tell the difference must not reject: doing so would
break every caller that never wired one.

Checks answered from the discovery table (model providers, db types) and every structural check
run regardless.

---

## 6. Dry-run hydration

Once the checks above pass, the config is handed to the component's real `from_dict` and the
result thrown away. This is the catch-all for everything the declarative checks cannot model.

It runs only when the earlier checks produced no errors — dry-running a config already known to be
broken produces noise, not information.

**It is side-effect free by construction, not by luck:**

- `db` is withheld from the copy handed to the hydrator. Otherwise `from_dict` →
  `resolve_db_from_config` → `db_from_dict` constructs a real backend, engine and connection pool
  included. The `db` block is fully checked separately, so dropping it here costs nothing.
- The payload is deep-copied, because `from_dict` copies the dict *shallowly* before deleting keys
  and handing the nested dicts to the constructor.

No tool is executed and no model call is made: model classes build their client lazily,
rehydration only assigns entrypoints, and the constructors are plain attribute assignment.

Failures are mapped back to a field where possible:

| Raised | Reported as | Path |
|---|---|---|
| `ImportError` | `provider_not_installed` | `model.provider` |
| `ValidationError` | `invalid_type` | the pydantic location |
| `ValueError` / `TypeError` | `hydration_failed` | best-effort from the message (`model`, `model.id`, `model.provider`, `steps`), else the config as a whole |
| any other `Exception` | `hydration_failed` | the config as a whole — a hydration bug must never become a 500 |

---

## 7. Error codes

A **closed vocabulary**. Clients are expected to branch on these codes, not on message text —
messages are written for humans and may be reworded.

| Code | Fires when | Example path |
|---|---|---|
| `missing_required_field` | A required field is absent | `model`, `tools[0].name`, `db` |
| `invalid_type` | A field has the wrong JSON type | `knowledge`, `tools[1].parameters` |
| `invalid_value` | Right type, disallowed value | `num_history_runs`, `members[0].type` |
| `invalid_format` | Right type, malformed content | `model` (bad `provider:id` string) |
| `unknown_field` | A key the component cannot consume, with a spelling suggestion | `instruction` |
| `unresolvable_ref` | A referenced model, tool, db, knowledge base or schema is not registered | `model.provider`, `tools[0].name`, `db.id` |
| `cyclic_reference` | A component references itself | `members[0].team_id` |
| `unsupported_schema_version` | `schema_version` exceeds `SCHEMA_VERSION` | `schema_version` |
| `hydration_failed` | The dry run raised | varies; `""` when unattributable |
| `provider_not_installed` | The provider is registered but its SDK is absent here | `model.provider` |

Adding a code is a schema change: extend the vocabulary here and in `registry/schema.py` together.
A test asserts the two agree.

---

## 8. Error paths and accumulation

**Paths.** Core reports config-relative paths (`model.provider`, `tools[1].name`, `""` for the
config as a whole). The API layer prefixes each with `config.`, so what a client receives is
body-relative and reads the same way as FastAPI's own `loc` entries.

**Accumulation.** Validation never short-circuits: a caller fixing a config sees every problem in
one response rather than peeling them off one request at a time. At most one error is reported per
path, so a pydantic union failure cannot triple-report the same field.

**Status code.** A failure is a **422** with the payload below. Note that `ConfigValidationError`
subclasses `ValueError` so a handler that only knows the older contract still fails closed — which
means any new handler must catch it *above* its `except ValueError` arm, or the field-level errors
flatten into a plain 400 string.

---

## 9. Examples

### A valid definition

```http
POST /components
```

```json
{
  "name": "Research Agent",
  "component_id": "research-agent",
  "component_type": "agent",
  "config": {
    "model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"},
    "name": "Research Agent",
    "description": "Searches the web and summarizes what it finds.",
    "instructions": ["Cite every source.", "Say so when you are unsure."],
    "markdown": true
  }
}
```

`201`, and the row stored in `component_configs.config` is the submitted config plus the stamp —
nothing reordered, nothing coerced, nothing dropped:

```json
{
  "name": "Research Agent",
  "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
  "markdown": true,
  "description": "Searches the web and summarizes what it finds.",
  "instructions": ["Cite every source.", "Say so when you are unsure."],
  "schema_version": 1
}
```

### The rejection payload

Every failure has this shape:

```json
{
  "detail": "Invalid agent configuration: 1 error.",
  "error_code": "CONFIG_VALIDATION_ERROR",
  "schema_version": 1,
  "errors": [
    {
      "path": "config.model",
      "message": "Field 'model' is required for an agent definition.",
      "code": "missing_required_field"
    }
  ]
}
```

### Rejected definitions

The `errors` entry each one produces:

**A typo in a field name** — `{..., "instruction": "singular"}`

```json
{"path": "config.instruction",
 "message": "Unknown field 'instruction'. Did you mean 'instructions'?",
 "code": "unknown_field"}
```

**A tool nothing can supply** — `{..., "tools": [{"name": "web_serch"}]}`

```json
{"path": "config.tools[0].name",
 "message": "Tool 'web_serch' is not registered; no entrypoint was found. Register the tool or its toolkit on the Registry (for MCP toolkits, make sure they are connected).",
 "code": "unresolvable_ref"}
```

**A db id that resolves to nothing** — `{..., "db": {"id": "no-such-db"}}`

```json
{"path": "config.db.id",
 "message": "Database 'no-such-db' could not be resolved. It matches neither the OS database nor any db registered on the Registry.",
 "code": "unresolvable_ref"}
```

**A malformed model string** — `{..., "model": "gpt-5.5"}`

```json
{"path": "config.model",
 "message": "Invalid model string 'gpt-5.5'. Expected '<provider>:<model_id>', e.g. 'openai:gpt-5.5'.",
 "code": "invalid_format"}
```

**A config from the future** — `{..., "schema_version": 99}`

```json
{"path": "config.schema_version",
 "message": "Config declares schema version 99, but this framework understands up to 1. Upgrade before writing this definition.",
 "code": "unsupported_schema_version"}
```

**Several problems at once** — all three come back together, so the config is fixed in one pass:

```json
[
  {"path": "config.num_history_runs",
   "message": "Input should be greater than or equal to 0", "code": "invalid_value"},
  {"path": "config.instruction",
   "message": "Unknown field 'instruction'. Did you mean 'instructions'?", "code": "unknown_field"},
  {"path": "config.model.provider",
   "message": "Model provider 'acme' is not registered. Registered providers: aimlapi, anthropic, aws-bedrock, ... (47 more).",
   "code": "unresolvable_ref"}
]
```

Run [`cookbook/93_components/validate_config.py`](../cookbook/93_components/validate_config.py) to
reproduce these offline, without a database or a server.

---

## 10. Known limits

- **Team and workflow get envelope validation only.** Shape, schema version, unknown keys, and the
  structure of `members` / `steps` are checked. Deep resolution is not: a `MemberRef` naming a
  component that does not exist is accepted, and cycle detection catches only direct
  self-reference, not `a -> b -> a`. The dry run is gated to agent definitions. EGPT-2.2 was
  scoped to the agent definition; the rest is a follow-up.
- **Stored configs are not retro-validated** (§3).
- **`PATCH /components/{id}` can change `component_type`** without revalidating the configs already
  stored under it, which would leave them checked against the wrong schema.
- **`to_dict(from_dict(config))` is not byte-identical when the config has tools.** Rehydration
  reattaches the live entrypoint and re-derives its JSON Schema, so the second `to_dict` is a
  *fuller* version of the first. Pre-existing serialization behaviour, not something validation
  introduces. The `stored == submitted + schema_version` guarantee is unaffected and holds always.

---

## 11. Programmatic access

```python
from egpt.registry.schema import (
    SCHEMA_VERSION,          # 1
    json_schema,             # JSON Schema for a component type, for docs and OpenAPI
    validate_config,         # core validator; needs a ComponentSpec for the dry run
    ConfigValidationError,   # carries .errors: list[ConfigError(path, message, code)]
)

json_schema("agent")["required"]   # ['model']
```

From the api layer, where orchestration may be imported, use
`egpt.os.routers.components.validation.validate_component_config` — it builds the `ComponentSpec`
for you, so the dry-run check is included:

```python
from egpt.os.routers.components.validation import validate_component_config
from egpt.registry.schema import ConfigValidationError

try:
    stored = validate_component_config(config, "agent", registry=registry)
except ConfigValidationError as error:
    for item in error.errors:
        print(item.path, item.code, item.message)
```
