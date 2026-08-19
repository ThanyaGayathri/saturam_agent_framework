# Component lifecycle and audit trail (EGPT-2.6)

Normative reference for how a stored agent definition moves between stages, what is immutable,
and what gets recorded about who changed it. Companion to [tenancy.md](tenancy.md), which covers
*which rows* a caller may reach, and
[component-config-schema.md](component-config-schema.md), which covers *what a config may say*.

---

## 1. Stages

A component has many config versions. Each version carries a `stage`:

| Stage | Meaning |
|---|---|
| `draft` | Freely editable and deletable. Not served to production unless it is `current_version`. |
| `published` | Immutable. Publishing also points the component's `current_version` at it. |

There is no `publish` method and no publish route. Publishing is
`upsert_config(..., stage="published")` — `PATCH /components/{id}/configs/{version}` with
`{"stage": "published"}` — which moves `current_version` inside the same transaction.

```
                create                update                publish
   [*] ──────────────────► draft ◄──────────────┐ ────────────────────► published
                             │                  │                          │
                             │  delete draft    │                          │ rollback
                             ▼                  └──────────────────────────┘ (set-current)
                            [*]                    published stays immutable
```

Rollback is `POST /components/{id}/configs/{version}/set-current`, which repoints
`current_version` at an **already published** version. Nothing is deleted or rewritten.

### What is immutable

| Attempt | Result |
|---|---|
| Edit a published version's config, label, stage or notes | 400 — `Cannot update published config` |
| Delete the current version | 400 — `Cannot delete current config` |
| Delete a published, non-current version | 400 — `Cannot delete published config` |
| Delete a non-current draft | 204 |
| Make a draft current via `set-current` | 400 — `Cannot set draft config … as current` |

> **Changed in EGPT-2.6.** The third row used to succeed. `delete_config` read the version's
> stage and then never checked it, so only the *current* version was protected and a published
> version that had been rolled away from could be destroyed — contradicting the method
> docstring, the route description and the ticket, with no test pinning it. The check now runs
> *after* the current-version check, so a version that is both published and current still
> answers `Cannot delete current config` exactly as before; the fix adds a refusal where there
> was none rather than relabelling one that already worked.

`upsert_component(current_version=N)` (`PATCH /components/{id}`) also moves the pointer, but it
does **not** check that the target is published. Prefer `set-current` for rollback.

---

## 2. The audit trail

Every lifecycle mutation appends one row to `egpt_component_audit`. This is what makes
runtime-editable agents accountable: versioning, stages and rollback already worked, but nothing
recorded *who* changed *what*, *when*.

### Guarantees

- **No mutation commits unaudited.** The audit insert runs on the mutation's own transaction, so
  the change and its record commit or roll back together.
- **No refusal is audited.** A published-config edit, a cross-tenant delete or a draft made
  current leaves the trail exactly as it was.
- **Append-only.** There is no update or delete for this table anywhere in the DB API. That
  absence *is* the enforcement — no trigger, no grant. Do not add one.
- **It outlives what it describes.** The table carries no foreign key to `components`, so
  `delete_component(hard_delete=True)` — which removes the component row and its configs —
  leaves the history intact.
- **No entry describes something that did not happen.** Both pointer-moving paths — the publish
  inside `upsert_config` and `set_current_version` — record which version they replaced, and both
  establish that value with a compare-and-swap rather than an earlier read, so concurrent writers
  cannot produce two rows claiming to have replaced the same version. A move that would be a
  no-op (setting the version that is already current) records nothing at all.
- **A row carries the tenant of the component it describes**, not the tenant filter its caller
  happened to pass. An unattributed write (`Agent.save()`) to a tenanted component is therefore
  still visible to the tenant that owns it.

### Columns

| Column | Notes |
|---|---|
| `audit_id` | Autoincrement. **The ordering key.** |
| `component_id` | Not a foreign key, deliberately. |
| `tenant_id` | Nullable; `NULL` means an unscoped write. |
| `version` | `NULL` for component-level actions. |
| `action` | See the vocabulary below. |
| `actor` | The authenticated principal, or `NULL`. |
| `note` | Free text; defaults to the mutation's own `notes`. |
| `details` | JSON — the facts that make an entry readable on its own. |
| `created_at` | Epoch seconds. **Display only, not the sort key.** |

`tenant_id` carries the same visibility rule as component rows (`db/base.py`): rows written
before multi-tenancy was enabled have `tenant_id = NULL`, and a tenant-scoped read applies a
strict predicate that does not match them. Enabling tenancy therefore makes the pre-tenancy
audit history disappear from every scoped view — no error, just an empty page. It stays
reachable through an unscoped read.

`created_at` has second resolution, so an update and the publish that follows it share a
timestamp. Ordering on it would return the two in an arbitrary order, which is why `audit_id`
exists and why the read endpoint exposes it.

`audit_id` is `Integer` on **both** backends, and that is load-bearing rather than stylistic:
Postgres turns an `Integer` primary key into `SERIAL`, and SQLite only aliases its implicit
`rowid` — and so only autoincrements — for a column declared exactly `INTEGER`. A `BigInteger`
primary key raises `IntegrityError: NOT NULL constraint failed` on SQLite. The SQLite schema
deviates from its `BigInteger` house style here on purpose.

### Action vocabulary

| Action | Emitted when | `details` |
|---|---|---|
| `component.create` | A component is created | `component_type` |
| `component.reactivate` | A soft-deleted component is revived | `component_type` |
| `component.update` | Component metadata changes | changed `fields`; `current_version` + `previous_current_version` if the pointer moved |
| `component.delete` | Soft or hard delete | `{"hard": bool}` |
| `config.create` | A new version is created | `stage`, `label` |
| `config.update` | A draft is edited and stays a draft | `stage`, `label` |
| `config.publish` | A version becomes published | `from_stage`, `previous_current_version` |
| `config.delete` | A draft version is deleted | `stage` |
| `config.rollback` | `set_current_version` moves the pointer | `from_version`, `to_version` |

`config.rollback` is emitted only when the pointer actually moves: `set_current_version` on the
version that is already current returns `True` and records nothing, because `from_version ==
to_version` would describe a transition that never happened.

Two of these are derived from what the transaction actually did rather than from what the caller
asked for, because the method names do not distinguish them:

- **`config.publish`** comes from the stage transition inside `upsert_config`, not from the
  presence of `stage: "published"` in the request. A draft edited straight to published records
  the publish *alone*, not an update followed by a publish — one call did one thing, and
  splitting it would invent an intermediate state that never existed.
- **`config.rollback`** is reserved for `set_current_version`, the only path that validates the
  target is published. A pointer move through `upsert_component` records `component.update`
  instead, so the trail never claims a validation that did not run.

`POST /components` writes two rows (`component.create` and `config.create`, plus
`config.publish` when created published), so each action means the same thing wherever it came
from.

### The actor

`actor` is the authenticated principal, taken from the verified token — never from the request
body. `egpt.os.actor` is the only place it is read.

| Value | Principal |
|---|---|
| a JWT `sub` | a human, via the configured `user_id_claim` |
| `sa:<name>` | a service account |
| `__scheduler__` | the platform scheduler |
| `NULL` | no principal on this write |

The first two namespaces are trustworthy because `is_reserved_principal` already rejects a JWT
whose subject claims one of them.

**`NULL` is not a gap in the trail.** The change is still recorded; it simply had no principal.
That is the honest answer for an open instance with no authentication, and for the writers that
bypass HTTP entirely — `Agent.save()`, `Team.save()`, `Workflow.save()` and the studio toolkit.
Those are covered *because* the audit write lives in the DB layer rather than in the router.

Unlike tenancy, there is no `require_actor` gate. `None` there means *unscoped*, which is the
widest possible access, so it has to be refused; an actor of `None` grants nothing.

---

## 3. Reading the history

```
GET /components/{component_id}/audit
```

Scope `components:read` by default — the same scope as `GET /components` and
`GET /components/*/configs/*`. This was deliberate reuse, not an oversight: `actor` is already
exposed under read scopes elsewhere in this API (sessions, runs), so anyone who can already read
a component's config can also see who has been changing it. Newest first, paginated like
`GET /components`.

The audit trail is the one surface whose purpose is attribution, and in most governance models
"can read config" and "can see who touched it" are separable grants. A deployment that needs the
split can set `components_audit_scope_enabled=True` on `EgptServingSettings` (or the
`COMPONENTS_AUDIT_SCOPE_ENABLED` env var, aicoe-tracker#170): this route then requires a
dedicated `components:audit` scope instead, and a `components:read`-only token is refused. The
setting defaults to `False`, so existing `components:read`-only tokens keep working until a
deployment opts in.

Rolling back is not symmetric: disabling the flag after `components:audit`-only tokens have been
minted locks those tokens out of the audit route (403, since scope checks are AND-only and there
is no fallback to `components:read`). Coordinate a rollback with token holders, or re-mint their
tokens with `components:read` first.

| Parameter | Notes |
|---|---|
| `page`, `limit` | `limit` capped at 100 |
| `action` | Must be one of the vocabulary above |
| `version` | Filter to one config version |
| `include_deleted` | See history for a soft- or hard-deleted component too (default `false`) |

| Situation | Code |
|---|---|
| Component absent, **or owned by another tenant** | 404, byte-identical |
| Component soft- or hard-deleted, `include_deleted` unset | 404, byte-identical |
| Component soft- or hard-deleted, `include_deleted=true`, history exists | 200, full history |
| Unknown `action` filter | 422 |
| Missing `components:read` (or `components:audit` when the split is enabled) | 403 |

An unknown `action` is rejected rather than answered with an empty page, so a typo'd filter is
never mistaken for a component that nothing ever happened to.

The response never includes `tenant_id`, matching every other component response model.

### Reading past a delete

`include_deleted=true` opts into seeing a deleted component's history, under the same scope as
the rest of this route — `components:read` by default, or `components:audit` when
`components_audit_scope_enabled` is on (see above).

Soft and hard delete need different proof of existence, because they leave different things
behind. A soft delete leaves the `components` row in place (`deleted_at` set), so
`include_deleted` uncovers it the same way the db layer's `list_components(include_deleted=True)`
already does. A hard delete removes the row outright — the audit trail carries no foreign key to
`components` specifically so this can happen without taking the history with it — so there is no
row for `include_deleted` to uncover. In that case existence is instead proven by the audit trail
itself: if any row exists for the component in-tenant, that is enough. A `component_id` with no
row and no history either way (never existed, or belongs to another tenant) still 404s regardless
of `include_deleted`.

---

## 4. Operating it

The table is created on demand, so no migration is needed for an existing deployment. Two
consequences worth knowing:

- `_create_all_tables` failures are logged and swallowed at startup, so a database role without
  `CREATE` boots fine and then fails on the **first component mutation** — because the audit
  write shares its transaction. That is the intended fail-closed behaviour, not a bug, but it
  surfaces late. Operators who manage schema externally should create the table up front.
- Every component write appends a row, including each `Agent.save()`. There is no retention
  policy; pruning old rows is an operator decision. Prune by `created_at`, never by rewriting
  rows.

---

## 5. The admin console (EGPT-2.9)

`EgptOS(enable_console=True)` serves a minimal authoring UI at `GET /console`. It is a single
self-contained HTML page shipped as package data — no build step, no CDN, no node toolchain —
and it drives nothing but the `/components` API, so it can do exactly what a token can do and
no more.

Every lifecycle rule above shows up in it as an enabled or disabled control, each carrying a
`title` that says why:

| Row state | Edit | Publish | Make current | Delete |
|---|---|---|---|---|
| draft, not current | yes | yes | no — target must be published | yes |
| draft, is current | yes | yes | no | **no** — current cannot be deleted |
| published, is current | **no** → *Clone to draft* | no | no — already current | **no** |
| published, not current | **no** → *Clone to draft* | no | **yes — this is rollback** | **no** |

*Clone to draft* is `POST /components/{id}/configs` with the published body and `stage: "draft"`:
the escape hatch from immutability, so an operator does not have to know the rule to work around
it. The server remains the authority — each greyed-out action is still refused with a 400 if it
is reached another way.

Publish and rollback map to the two calls in §1: publishing is `PATCH .../configs/{version}`
with `{"stage": "published"}`, and rollback is `POST .../configs/{version}/set-current`. The
console re-reads the component after both, because `current_version` lives on the component row
and not on the config it points at.

Two things worth knowing before enabling it:

- **The page is served without a credential**, because a browser navigating to it has none to
  send. It is inert — no tenant data is baked into it — and every call it makes carries a bearer
  token the operator pastes in, so scopes and tenancy are enforced normally. `EgptOS` adds
  `/console` to the auth middleware's excluded paths only when the flag is on. It is off by
  default so that exposing it stays a deployment decision.
- **The token lives in `sessionStorage`**, which is why the page loads nothing external and is
  served under a `Content-Security-Policy` that forbids it. Adding a CDN would put a third-party
  script in a position to read an admin token, and would break the page.

Cookbook: `cookbook/93_components/admin_console.py`.

---

## 6. Scope

Not in this ticket:

- **Event-driven cache invalidation.** The components router still evicts the hydration cache
  itself on each mutation, and other workers converge within `COMPONENT_CONFIG_CACHE_TTL_SECONDS`
  (see [tenancy.md](tenancy.md) §5). `ComponentConfigCache.invalidate` remains the seam.
- **Tenant-mandatory data access.** `tenant_id=None` still means "no predicate" at the DB layer
  — **EGPT-2.7**.
- **Async backends.** They raise `NotImplementedError` for the whole component family, audit
  included, as they did before.
- **Moving a definition between databases or tenants.** Export/import and template seeding are
  **EGPT-2.8** — see [component-portability.md](component-portability.md). Note that neither
  version history nor the audit trail travels with a definition: both describe what happened in
  one database, and an import is a new beginning in the target.
