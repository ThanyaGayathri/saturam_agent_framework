# Portable component definitions (EGPT-2.8)

Normative reference for moving an agent, team or workflow definition between databases and
tenants, and for seeding a new tenant from templates. Companion to
[component-lifecycle.md](component-lifecycle.md), which covers how a definition moves between
stages; [tenancy.md](tenancy.md), which covers which rows a caller may reach; and
[component-config-schema.md](component-config-schema.md), which covers what a config may say.

---

## 1. The bundle

A definition travels as a **bundle**: one JSON document carrying a component, every component it
depends on, and the links between them.

```json
{
  "format": "egpt.component-bundle",
  "format_version": 1,
  "schema_version": 1,
  "exported_at": "2026-08-13T09:41:12Z",
  "source": {"component_id": "support-team", "tenant_id": "acme", "db_id": "os-db"},
  "root": "support-team",
  "components": [
    {
      "component_id": "faq-agent",
      "component_type": "agent",
      "name": "FAQ Agent",
      "description": "Answers frequently asked questions.",
      "metadata": null,
      "stage": "published",
      "label": null,
      "notes": null,
      "config": {
        "id": "faq-agent",
        "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
        "db": {"id": "os-db", "type": "postgres"},
        "schema_version": 1
      },
      "links": []
    },
    {
      "component_id": "support-team",
      "component_type": "team",
      "name": "Support Team",
      "stage": "published",
      "config": {
        "id": "support-team",
        "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
        "members": [{"type": "agent", "agent_id": "faq-agent"}],
        "schema_version": 1
      },
      "links": [
        {
          "link_kind": "member",
          "link_key": "member_0",
          "child_component_id": "faq-agent",
          "position": 0,
          "meta": {"type": "agent"}
        }
      ]
    }
  ],
  "redacted": ["components[0].config.db.db_url"]
}
```

**Always a bundle, even for one agent** (`components` is then a one-element list). A team whose
members are missing does not become a smaller team — `Team._hydrate_from_graph` refuses to build
a partial one. Carrying the dependency closure is what makes an imported definition actually
hydratable rather than hydratable-if-the-target-happens-to-have-the-members.

**Children come first.** A link row references a `child_version` that must already exist, and
Postgres declares the foreign key.

**Exactly one config version per component.** Export walks
`load_component_graph` from a resolved version and emits that one config each.

### `format_version` compatibility

Import accepts `format_version <= 1` and refuses anything newer with a message naming both
numbers. The `schema_version` *inside* each config is a different axis and is handled by
`registry.schema.migrate()` before validation, so an old bundle upgrades exactly the way an old
stored row does.

### What is not portable

| Not carried | Why |
|---|---|
| Version history | The target renumbers from 1. A bundle that carried v7 would be lying about the target. |
| `current_version` | Derived from what the import publishes. |
| The audit trail | The target's own record of what happened *there*. It is append-only and never imported. |
| Labels, as a set | A label is unique per component and there is no API to move one. See §5. |
| `tenant_id` | A definition takes the tenant it is imported into. `source.tenant_id` is provenance, never an instruction. |

---

## 2. Secrets

`PostgresDb.to_dict()` emits `db_url`, and `Agent.to_dict()` embeds the db dict — so a stored
config can hold `postgresql+psycopg://ai:hunter2@host/ai`. **Export redacts by default.**

Two layers:

| Layer | Applies to | Action |
|---|---|---|
| Structural | `db_url`, `db_file`, `connection_string`, `uri`, `host`, `port`, `user`, `username`, `password` in the `db` block | **Dropped.** A sentinel would be handed to a driver and produce a confusing connection error; an absent field produces a clean "unresolvable db". |
| Generic scan | Keys matching `api_key(s)`/`secret(s)`/`password(s)`/`token(s)`/`credential(s)`/`private_key(s)`/`access_key(s)`/`authorization`, and any string value containing a URL with userinfo | **Replaced** with `__redacted__`. The key is one we did not anticipate, so its presence is information. |

**Both layers run over the whole bundle entry, not just the config.** An entry carries nine
fields, and `description` and `notes` are free text an operator writes — exactly where a
connection string gets pasted while debugging. Scanning only the config left five fields
carrying whatever was in them while `redacted` reported `[]`, which is worse than reporting
nothing: it certifies the file as clean.

**What survives** is exactly what lets the config still resolve: `id`, `type`, `db_schema`,
`table_prefix` and the per-table overrides — the fields `resolve_db_from_config` needs to find a
registered instance by id.

Every removal is listed in `document["redacted"]`, so over-redaction is visible rather than
silent. Two details make the pattern hold where a simpler one does not:

- **Plurals count.** `api_keys` is the natural shape for a dict of per-provider credentials, and
  its inner keys are provider names no pattern would catch — so the outer key is the only chance.
- **What keeps `max_tokens` safe is the value, not the key.** A credential is never an integer,
  so numbers and booleans are never redacted whatever they are called. Trying to express "not
  `max_tokens`" in the pattern is what excluded the plurals in the first place.

### Sentinels do not import

`import` strips any `__redacted__` value before writing, drops the key, and logs which fields
were dropped. An absent `api_key` falls through to the provider SDK's environment lookup, which
is usually right in the target; a component whose credential is the literal string
`__redacted__` looks configured and fails on first call. It also stops the sentinel compounding
— re-exporting an imported bundle would otherwise re-detect it as a fresh secret.

### Making a redacted config land

| Option | Effect |
|---|---|
| `db_id="target-os-db"` | Rewrites `config["db"]["id"]` on every component that has a db block. The normal cross-environment move. |
| `drop_db=True` | Removes the block; hydration then attaches the caller's database. |
| neither | Keeps the source's `db.id`, which the target must know. |

In all three the reference is resolved **before** validation, so a config naming a database that
does not exist in the target is rejected with a field-level `db.id / unresolvable_ref` — rather
than stored and then quietly losing its database at hydration time.

`include_secrets=True` exists for the library and the seed CLI, sets `"contains_secrets": true`
on the envelope so a git hook can refuse the file, and warns on stderr. **It is not exposed over
HTTP:** `components:read` is a read scope, not a licence to extract the database password out of
a stored config. An operator running the CLI already holds the DSN.

---

## 3. Component ids are global

`component_id` is the **primary key** of `egpt_components`; EGPT-2.1 deliberately kept
`tenant_id` out of it. Two tenants can therefore never hold the same id, and the existence check
inside `create_component_with_config` is intentionally not tenant-scoped.

So "seed and import are tenant-scoped" means:

- rows are stamped with the **target** tenant, never the source's;
- reads, ownership checks and conflict probes are tenant-aware;
- a component belonging to another tenant is never written to, under any policy.

It does **not** mean the same bundle seeds into two tenants unchanged. That needs `id_prefix`.

### Conflict options

Applied in order: `id_map` → `id_prefix` → `new_component_id` → `on_conflict`.

| Option | Effect |
|---|---|
| `id_map` | Explicit old-id to new-id renames. |
| `id_prefix` | Prefix every id in the bundle. The usual answer for a second tenant. |
| `new_component_id` | Rename a single-component bundle. Refused for a multi-component one, where it has no single meaning. |
| `on_conflict="error"` (default) | Raise. The probe is unscoped, because that is what predicts the database's behaviour. |
| `on_conflict="skip"` | No write. |
| `on_conflict="new_version"` | Append a version — **only** to a component this tenant owns. A foreign-tenant holder degrades to `error`, never to a write. |

**The collision message is identical whoever holds the id.** The collision is observable either
way, so naming the owner would not conceal anything — it would only hand a prober a cleaner
ownership oracle. This is the same doctrine the components router applies to its 409s.

### What a rename rewrites

Ids live in five places, and hydration reads more than one of them:

1. the entry's `component_id`
2. `config["id"]` — the self-id
3. `links[*].child_component_id`
4. a team config's `members[*].agent_id` / `team_id`
5. a workflow config's step `agent_id` / `team_id` / `workflow_id`, recursed through `steps`,
   `else_steps` (Condition) and `choices` (Router)

An id **not in the bundle** is an external reference and is never rewritten — a bundle may
legitimately link to something the target already holds. `config["db"]["id"]` is a *database* id
and is never touched by a rename; it has `db_id`.

---

## 4. Seeding

`scripts/seed_agents.py` loads every `*.json` bundle in a directory into one tenant. The packaged
starter templates live in `egpt/registry/templates/` and ship in the wheel.

```bash
./scripts/seed_agents.py --db-file ./egpt.db --tenant-id acme
./scripts/seed_agents.py --db-url postgresql+psycopg://ai:ai@localhost:5532/ai --tenant-id acme
./scripts/seed_agents.py --db-file ./egpt.db --tenant-id globex --id-prefix globex-
```

Exit codes: `0` everything created/versioned/already current, `1` one or more components failed,
`2` usage error or a missing import. `--dry-run` resolves and validates without writing;
`--json` prints the results as JSON and nothing else.

### The state machine

Per component:

| State found | Outcome |
|---|---|
| Absent | `created` at version 1 |
| Present, config differs | `new_version` — appended, never overwriting a published version |
| Present, config identical but still a draft, `--stage published` | `published` — publishes the version already there, in place |
| Present, config identical, metadata differs | `metadata_updated` |
| Present, identical | `unchanged` — **no row written and no audit record** |
| Present in another tenant | Refused |
| Held by a soft-deleted row | Refused (`--on-deleted skip` to pass over it) |

"Unchanged writes nothing" is checkable: EGPT-2.6 guarantees a successful mutation cannot commit
without an audit row, so an unchanged audit count proves nothing happened.

### The comparison rule

The stored config is compared against **what would actually be written** — after db resolution
and after validation — not against the raw template file. A template says `db: {"id": "os-db"}`
while the stored row holds that block with the connection details merged in; comparing the file
would report a difference on every run and append a version forever. Both sides are normalized
through `migrate` + `stamp_schema_version` and serialized with sorted keys, which is also what
keeps seeding idempotent on Postgres, where `jsonb` does not preserve key order.

### Two limitations worth knowing

- **Labels apply on create only.** A label is unique per component and there is no API to move
  one off its current holder, so carrying a template's label onto a new version would raise
  `Label '...' already exists`. Moving a label needs a `set_label` on `BaseDb` (follow-up).
- **Soft deletes block the id.** `get_component` and `list_configs` both filter
  `deleted_at IS NULL`, but the row still owns the primary key, and there is no undelete in
  `BaseDb`. The error names the fix: `delete_component(..., hard_delete=True)`, or a different id.
  This is the one case detected during the write rather than in the pre-pass, because no read API
  can see such a row.

---

## 5. Over HTTP

| Route | Scope | Notes |
|---|---|---|
| `GET /components/{id}/export` | `components:read` | Query: `version`, `label`, `include_dependencies`. Always redacted. Sets `Content-Disposition`. |
| `POST /components/import` | `components:write` | Body: `document` plus the options in §3. |

Both have **explicit entries in `os/scopes.py`, placed above the `GET /components/*` wildcard**.
Scope lookup takes the first pattern that fnmatches in insertion order, so the wildcard would
otherwise authorize the export route by accident — and reordering that dict would silently change
who can call it.

### Atomicity

Import **validates, id-rewrites and conflict-probes the entire bundle before writing anything**.
A bundle that fails validation on its third component leaves nothing behind: the response is a
422 with field-level errors and zero rows.

Phase two then writes **children before parents**, whatever order the document lists them in, so
a link can never point at a component this run has not created yet. A cyclic bundle is refused
with the cycle named.

Two failures phase one cannot cover: a soft-deleted id (no read API can see one) and a
database-level fault. Writes are one transaction per component — `BaseDb` has no cross-component
transaction — so either can leave a partial bundle. When it happens, the components already
written are attached to the raised `ComponentIOError` as `.results` and logged, and the HTTP
response carries the per-component `results` array. The alternative to reporting a partial write
is not preventing it, it is hiding it. Re-running with `on_conflict="new_version"` converges.

| Failure | Status |
|---|---|
| Config not a legal definition | 422, `CONFIG_VALIDATION_ERROR`, no writes |
| Id already taken (any tenant) | 409, owner-neutral message, no writes |
| Id held by a soft-deleted row | 409 |
| Malformed bundle, dangling link, cycle, missing `name`, bad option | 400 |
| Component absent or in another tenant (export) | 404 |

---

## 6. Where the code lives

| Concern | Module |
|---|---|
| Bundle format, redaction, id rewriting, reads and writes | `egpt/registry/io.py` (core) |
| Binding the EGPT-2.2 validator; `import_component`, `seed_from_directory` | `egpt/components/io.py` (orchestration) |
| The `ComponentSpec` factory and `validate_component_config` | `egpt/components/spec.py` |
| The routes | `egpt/os/routers/components/components.py` |
| The CLI | `scripts/seed_agents.py` |
| Starter templates | `egpt/registry/templates/*.json` |

`registry/io.py` is core and therefore names no orchestration module: the validator arrives as an
injected `ConfigValidator`, the same way `ComponentSpec` closes the same gap for EGPT-2.2. That
is enforced by `tests/unit/registry/test_schema_layering.py`, which runs the module in a clean
interpreter. `egpt/components/` exists rather than putting the binder under `egpt/os/` because
importing anything there runs `from egpt.os.app import EgptOS`, which would make FastAPI a
dependency of seeding a database.
