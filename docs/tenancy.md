# Tenant scoping (EGPT-2.4, runtime hydration EGPT-2.5, row-level enforcement EGPT-2.7)

Normative reference for how a request's tenant is resolved and what the component registry API
does with it. Companion to [capability-policy.md](capability-policy.md), which covers *which
capabilities* a tenant may reference; this document covers *which rows* a caller may reach.

Scoping is enforced at two layers, and section 6 is the second one. Sections 1–5 cover the
request-level gates: how the tenant is resolved from the credential and what the `/components` API
and runtime hydration do with it. Section 6 covers the data layer underneath, where every component
query requires a tenant regardless of which caller reached it.

---

## 1. Turning it on

Tenancy is opt-in. Set the JWT claim your issuer puts the tenant in:

```python
EgptOS(
    db=db,
    authorization=True,
    authorization_config=AuthorizationConfig(
        verification_keys=[...],
        tenant_id_claim="tid",       # or "org_id", whatever your issuer uses
    ),
)
```

With `tenant_id_claim` unset — the default — nothing in this document applies. No DB call gains
a tenant predicate, no request is refused for lacking a tenant, and behaviour is identical to
pre-EGPT-2.3.

---

## 2. Resolving the caller's tenant

`AuthMiddleware` writes `request.state.tenant_id`; `egpt.os.tenancy` is the only module that
reads it (`current_tenant_id`, and the `get_tenant_id` / `require_tenant_id` dependencies).

| Principal | Tenant | On `/components` when `tenant_id_claim` is set |
|---|---|---|
| JWT carrying the claim | the claim value | served, scoped to that tenant |
| JWT omitting the claim | `None` | **403** |
| JWT with a non-string or empty claim | — | **401** (the middleware refuses the token) |
| Service account | its `tenant_id` column | served if set, **403** if `NULL` |
| Scheduler (`__scheduler__`) | `None` | **403** — a platform identity, not a tenant's; it holds no `components:*` scope either |
| `OS_SECURITY_KEY` root | `None` | **403** — reachable, see below |

### Why an absent claim is a refusal

`None` means *unscoped*, and unscoped is the **widest** tenancy — it applies no `WHERE
tenant_id = ...` at all, so the caller reads and edits every tenant's rows. Serving it would
mean a token that simply omits the claim is more powerful than one that carries it. The gate is
`require_tenant_id`, attached at router level.

It is **components-only** on purpose. Making it global in the middleware would lock out every
existing untenanted caller on every other router; sequencing that is EGPT-2.7's.

### The security key is refused too

An earlier draft of this table called the security-key row unreachable, on the grounds that the
key path only runs when JWT is unconfigured and tenancy could not be on in that case. The first
half is right; the second is not. `tenant_id_claim` can be set *without* `authorization=True` —
that is how a PAT-only tenancy deployment is configured, and it is also where an operator who
forgot `authorization=True` lands. `tenancy_enabled` then reads the claim off `app.state` and
refuses the key with a 403 on `/components`.

That refusal is deliberate and stays: the key is an operator break-glass with no tenant, and
exempting it would hand its holder every tenant's rows — the exact "unscoped is the widest
tenancy" escalation this ticket exists to close. Turning a lockout into a leak is the wrong
trade. An operator who needs component access under tenancy should present a JWT carrying the
claim, or a PAT stamped with the tenant.

Because the 403 blames the *credential* for what is usually a configuration mistake, `EgptOS`
logs a warning at startup when `tenant_id_claim` is set with no JWT key configured.

---

## 3. What the components API enforces

Three router-level dependencies, resolved in this order:

1. `get_authentication_dependency(settings)` — authenticate.
2. `require_route_scopes("/components")` — the caller holds the route's mapped scope.
3. `require_tenant_id` — the caller has a tenant, if this deployment requires one.

### Scopes

The map in `egpt/os/scopes.py` is the single source of the vocabulary; the dependency derives
its table from `get_default_scope_mappings()` at wiring time, and a unit test asserts every
declared route resolves in it. Twelve routes, three scopes:

| Method | Scope |
|---|---|
| `GET /components…` | `components:read` |
| `POST` / `PATCH /components…` | `components:write` |
| `DELETE /components…` | `components:delete` |

`AuthMiddleware` and the service-account path already consult the same map. The router-level
gate is not redundant with them: both key their lookup on `request.url.path`, and Starlette
leaves `scope["path"]` intact when an app is mounted, so under
`parent.mount("/api", os_app)` the middleware looks up `"GET /api/components"`, finds no
mapping, and **an unmapped route defaults to allow**. The dependency resolves the scope from the
route *pattern*, which carries no mount prefix.

Like `require_resource_access`, it is inert unless `request.state.authorization_enabled` is set.
It closes a bypass in deployments that asked for authorization; it does not switch authorization
on for deployments that did not. A deployment running JWT for identity only (env-var JWT with
`authorization` falsy) is still ungated, which is what that flag means.

`/components` is deliberately outside the `agents|teams|workflows` family that
`get_resource_context_from_path` recognises, so the GET-listing filtered-access fallback never
fires: a caller without `components:read` gets a hard 403 rather than an empty list.

`POST /components/validate` (the EGPT-2.9 dry run) sits behind all three dependencies like every
other component route, and maps to `components:write`. It performs no component reads, so it is
not an existence oracle for another tenant's ids; the one thing the caller's tenant selects is
which capability policy applies.

**`GET /console` is the one path under this OS that is deliberately unauthenticated** — and it is
not a component route. It serves a static HTML asset with no tenant data in it, because a browser
navigating to the console carries no `Authorization` header and would otherwise be refused before
it could ask for a token. `EgptOS` adds it to `AuthMiddleware`'s excluded paths only when
`enable_console=True`, and every API call the page then makes is gated exactly as above. There is
deliberately no `scopes.py` entry for it.

### Status codes

| Situation | Code |
|---|---|
| Unauthenticated | 401 |
| Missing the mapped scope | 403 |
| No tenant, in a tenancy-enabled deployment | 403 |
| Component absent, soft-deleted, **or owned by another tenant** | 404 |
| `component_id` already taken | 409 |
| Config fails schema validation (2.2) or the capability allow-list (2.3) | 422 |
| Happy path | 201 create, 200 read/update, 204 delete |

**Cross-tenant reads 404 with the same body as a genuine miss.** The tenant-scoped existence
check now runs unconditionally on the config routes; it previously ran only when the request
carried a config blob, so a config-less write against a foreign component fell through to the
db layer and surfaced as a 400.

**An id collision answers 409 to everyone.** `component_id` is the global primary key
(EGPT-2.1 kept `tenant_id` out of it), so a collision is always observable as "not 201" — no
scheme hides it. Answering 409-for-mine and 404-for-theirs would not conceal the collision, only
*label its owner*, which is a cleaner ownership oracle than the undifferentiated 400 it
replaced. Both tenants get byte-identical responses.

### Client-supplied tenancy

No request model declares `tenant_id`, pydantic drops unknown keys, and every handler builds its
DB kwargs explicitly, so a tenant in the body or query string is ignored and the principal's
tenant is what reaches the database. No response model exposes `tenant_id` either. Both are
pinned by tests rather than left to hold by accident.

---

## 4. Tenanted service accounts

`ServiceAccount.tenant_id` is nullable and **immutable** — it is not in
`SERVICE_ACCOUNT_MUTABLE_COLUMNS`, for the same reason `scopes` and `user_id` are not:
re-tenanting a live token rebinds an already-issued credential. To move an account, revoke it
and mint a new one.

Minting mirrors the existing scope rule — a caller may not mint an account with more reach than
it has:

- No `tenant_id` in the body → the account inherits the caller's tenant.
- A tenanted caller naming a *different* tenant → **403**, admin included: an admin is an admin
  within its tenant, and minting elsewhere is more reach than it has.
- A caller with **no** tenant may name one only if it is genuinely an operator — the OS security
  key, or an explicit admin-scope holder. Without this there would be no way to create the first
  tenanted machine identity.
- Any other untenanted caller naming a tenant → **403**. Having no tenant is not evidence of
  privilege: a PAT minted without one is untenanted by accident, not by authority. Treating the
  two alike let an untenanted PAT holding `service_accounts:write` mint itself into any tenant
  and read its components in two requests, bypassing everything above (PR #55 review).

Listing and revocation are tenant-scoped: another tenant's account reads as absent, so it is
neither disclosed nor revocable.

Account **name** uniqueness stays global rather than becoming per-tenant. The `uq_active_name`
partial unique index is not cleanly reversible if rebuilt, and two tenants wanting the same
account name is a naming annoyance, not a security boundary.

### Upgrade note

A table created from now on already has the column. Migration `v2_7_0` adds it to a
`service_accounts` table that predates this change; migrations are not auto-run, so:

```python
asyncio.run(MigrationManager(db).up())
```

Reverting is `MigrationManager(db).down(target_version="2.6.0")`; on SQLite the column drop needs
3.35+. Rows that existed before the migration get `tenant_id = NULL`, which reads as an unscoped
platform account.

---

## 5. Runtime hydration (EGPT-2.5)

Hydration — turning a stored config row into a live Agent/Team/Workflow — is scoped to the
caller's tenant end to end:

- **Every hydration entry point takes a trailing `tenant_id`**: the component getters
  (`get_agent_by_id`, `get_team_by_id`, `get_workflow_by_id` and their `get_*s` listings),
  `Agent.load` / `Team.load` / `Workflow.load`, the OS-level getters and the
  `resolve_agent/team/workflow` helpers. The tenant is applied to the DB read *and* carried
  into the recursion — team members and workflow-step executors are read as the same tenant,
  so a scoped root cannot pull foreign children one level down. A cross-tenant id resolves
  exactly like a missing one.
- **`resolve_*` derive the tenant off the request** the same way the components router does
  (`egpt.os.tenancy`), and **fail closed**: when `tenant_id_claim` is configured and the
  principal resolves to no tenant, a lookup that would fall through to the DB is refused with
  the same 403 as `/components`. The in-memory list of code-defined agents is exempt — those
  are deployment-owned, not tenant rows. Router and MCP paths that reach the getters without
  `resolve_*` — the direct lookups and the listings — go through the same gate
  (`egpt.os.utils.tenant_for_direct_read`): a valid JWT that omits the configured tenant claim
  gets a 403, never an unscoped read.
- **A team member that cannot be resolved refuses the whole team.** Under a scoped read a
  stored member link can point at a child that is deleted, foreign, or has no resolvable
  version; both hydration paths (`get_team_by_id` and the `Team.load` graph path) raise
  `HydrationError` naming the parent and child rather than silently running a partial team.
  The write-validation dry run stays lenient so drafts can reference members authored later.
- **The capability policy is re-checked as the caller.** The hydration-side
  `enforce_policy_or_raise` now selects the policy by the caller's tenant (falling back to the
  row's tenant only for unscoped reads, which is exactly the pre-2.5 behaviour).
- **Config rows are cached** in a per-process TTL'd LRU (`egpt.registry.hydrate`), keyed by
  `(db, tenant, component_id, version selector)` and parked on the `Registry` by `EgptOS`.
  The components router evicts a component's entries on every successful mutation; other
  workers — and writers that bypass the API, like `Agent.save` or the studio toolkit —
  converge within `COMPONENT_CONFIG_CACHE_TTL_SECONDS` (default 30; `0` disables the cache).
  `ComponentConfigCache.invalidate` remains the seam an event-driven invalidation would call.
  EGPT-2.6 shipped the lifecycle *audit trail* (see
  [component-lifecycle.md](component-lifecycle.md)) and explicitly left cache mechanics alone,
  so router-side eviction plus the TTL is still how a mutation propagates.
- **`HYDRATE_PUBLISHED_ONLY=true`** makes hydration refuse any config whose stage is not
  `published` (including explicit-version reads) with a 409 — production should not run
  drafts. Default off.
- **Hydration failures are distinguishable from missing components**: a row that exists but
  cannot be built raises `HydrationError` (rendered as a 500 with the cause) instead of
  collapsing into a misleading 404.
- **`tools/studio.py` takes a constructor `tenant_id`** and scopes every component read and
  write to it. The default `None` keeps the old unscoped behaviour — acceptable only for
  single-tenant or operator-facing agents.
- **The write-validation dry run is scoped**: a team or workflow config naming another
  tenant's component as a member/step no longer hydrates it, closing the foreign-id
  existence oracle.

## 6. Row-level enforcement at the DB (EGPT-2.7)

Everything above is enforced *above* the data layer. This section is the layer underneath: the
component DB methods themselves refuse to run unscoped, so a missing or bypassed API check
cannot leak or cross-write another tenant's components.

Why both layers exist: the router gate only covers HTTP. `Agent.save()`, the studio toolkit, a
migration script and a cookbook all hold the same `BaseDb` and reach the same rows without
passing through a single FastAPI dependency. A gate with an unguarded path around it is not a
gate.

### The three states of `tenant_id`

`tenant_id` is **keyword-only with no default** on all fourteen component methods
(`get_component`, `upsert_component`, `delete_component`, `list_components`,
`create_component_with_config`, `get_config`, `upsert_config`, `delete_config`, `list_configs`,
`set_current_version`, `list_component_audit`, `get_links`, `get_dependents`,
`load_component_graph`):

| Call | Result |
|---|---|
| `get_component("a")` — omitted | `TypeError`, and a mypy `call-arg` error before you run it |
| `get_component("a", tenant_id=None)` — explicitly unscoped | No tenant predicate; sees every tenant's rows. Refused with `TenantScopeRequiredError` when the db has `require_tenant_id` |
| `get_component("a", tenant_id="acme")` | Restricted to that tenant |
| `get_component("a", tenant_id="")` | `TenantScopeRequiredError`, always — a blank tenant is never legitimate, and on a write it would stamp rows into a tenant nobody can name again |

Omission and explicit-`None` are deliberately different failures because they are different
mistakes. A forgotten argument is a bug in any deployment, so it fails everywhere and at check
time. An explicit `None` is *correct* for a single-tenant deployment and is the only way to reach
pre-multi-tenancy rows, so it fails only where the operator has said tenancy applies.

A required parameter rather than a sentinel default (`UNSCOPED`) was chosen so mypy reports the
mistake statically; `disable_error_code = ["override"]` in `libs/agno/pyproject.toml` means a
sentinel would have produced no type-checker signal at all. See
[adr/0004-component-tenant-isolation.md](adr/0004-component-tenant-isolation.md).

### Turning on strict mode

```python
db = PostgresDb(db_url=..., require_tenant_id=True)   # explicit
# or EGPT_REQUIRE_TENANT_ID=true|1|yes for dbs not built through EgptOS
```

**`EgptOS` sets it automatically on every registered db when `tenant_id_claim` is configured** —
opting into tenancy at the edge opts in at the bottom. The flag is only ever turned *on*: an
explicit `require_tenant_id=False` will not switch off what the environment enabled, because that
is the direction that loses isolation.

### NULL-tenant rows become unreachable

Rows written before multi-tenancy carry `tenant_id IS NULL` and are reachable only with
`tenant_id=None` — which strict mode refuses. That is intended, not an oversight: an unscoped row
in a tenanted deployment belongs to no tenant and is therefore visible to all of them. Assign them
before enabling strict mode:

```sql
UPDATE egpt_components        SET tenant_id = 'acme' WHERE tenant_id IS NULL;
UPDATE egpt_component_configs SET tenant_id = 'acme' WHERE tenant_id IS NULL;
UPDATE egpt_component_links   SET tenant_id = 'acme' WHERE tenant_id IS NULL;
UPDATE egpt_component_audit   SET tenant_id = 'acme' WHERE tenant_id IS NULL;
```

There is no backfill tool, because there is no tenants table to backfill against — which tenant
inherits pre-tenancy rows is a question only the operator can answer.

### `tenant_id` is a first-class column

On all four component tables `tenant_id` is a declared, indexed column — not a key inside the
config JSON, and not something the query layer interprets:

| Table | Tenant-leading composite index |
|---|---|
| `egpt_components` | `(tenant_id, component_id)` |
| `egpt_component_configs` | `(tenant_id, component_id)` |
| `egpt_component_links` | `(tenant_id, parent_component_id)`, `(tenant_id, child_component_id)` |
| `egpt_component_audit` | `(tenant_id, component_id)` |

`tenant_id` **leads** each index rather than merely appearing in one: a `(component_id, tenant_id)`
index cannot serve "everything in this tenant" and would turn a scoped list into a scan.

Note there is no `allowed_columns` entry for this. `db/filter_converter.py` is trace-only —
component queries take fixed typed parameters, not filter expressions, and the traces table has no
`tenant_id` column, so adding one there would emit SQL against a column that does not exist. The
DDL is the whitelist, pinned by `tests/unit/db/test_component_tenant_scoping.py`.

### Upgrade note

Any caller of a component DB method must now name a tenant. Passing `tenant_id=None` reproduces
the previous behaviour exactly:

```python
db.get_component("agent-1")                     # before
db.get_component("agent-1", tenant_id=None)     # after, unchanged behaviour
db.get_component("agent-1", tenant_id="acme")   # after, scoped
```

`Agent.save/delete`, `Team.save/delete` and `Workflow.save/delete` gained an optional
`tenant_id`, defaulting to `None`. On a team or workflow it is threaded into every member's and
step's own `save`, so a tenanted parent cannot end up linked to unscoped children.

### How it is enforced, and how it stays enforced

The guard (`egpt.db.component_scope.resolve_tenant_scope`) runs as the first statement of each
method, *before* the method's own `try` — inside it, a scope refusal would be logged as a database
error. The ~90 `WHERE tenant_id = ...` predicates per backend were deliberately left alone rather
than centralised; mutation testing had already established that the tenant-scoped *prechecks* are
the load-bearing layer.

Three tests keep it that way:

- `tests/unit/db/test_component_tenant_fail_closed.py` — asserts on every backend class that
  `tenant_id` is keyword-only with no default, and parses each backend's AST to assert the guard
  is the first statement of all fourteen methods. A backend added later without either cannot pass
  CI.
- `tests/unit/db/test_component_tenant_adversarial.py` — tenant B attempts all fourteen methods
  against tenant A's ids; tenant A's four tables are then compared row by row via raw SQL. A
  mutation refusing in its return value while having already written is caught here, as is any
  audit row written by a refused mutation.
- `tests/integration/os/tenancy_postgres/test_adversarial_tenant_isolation.py` — the same sweep on
  real Postgres, plus every component route driven with tenant B's real JWT. The route assertion
  is *indistinguishability* from a nonexistent component rather than a fixed status code: routes
  legitimately differ (`GET /components/{id}` 404s, `/configs` answers `200 []`), but no route may
  let B tell a foreign component apart from a missing one.

### Not covered by this layer

Sessions, memories and runs. Those tables have no `tenant_id` column, so scoping them is a schema
change with a migration rather than an enforcement change — tracked separately from EGPT-2.7.

## 7. Known limits

- **`component_id` is globally unique**, so two tenants cannot both own `support-agent`. The 404
  hides who holds it; the id is still unavailable. It also means `tools/studio.py`'s id-uniqueness
  probe is scoped to its own tenant and can therefore miss a foreign holder — the insert then
  fails on the primary key. That is the deliberate trade: an unscoped probe would answer correctly
  but is a cross-tenant existence oracle. Narrowing it properly means a per-tenant primary key.
- **Sessions, memories and runs are unscoped.** Hydration itself is scoped (section 5) and the
  agents/teams/workflows routers are tenant-gated, but the surrounding run/session data is not.
  Needs a `tenant_id` column and migration on those tables; follow-up work, not EGPT-2.7.
- **The non-HTTP writers default to unscoped.** `Agent.save/delete`, `Team.save/delete` and
  `Workflow.save/delete` take a `tenant_id` but default it to `None`. On a non-strict db an
  unscoped write can update a row owned by another tenant that shares the `component_id`, and the
  row keeps its original `tenant_id` — so the owner's correctly-scoped read then serves the foreign
  config with nothing to detect. `require_tenant_id` refuses it, and **is** the control for these
  three; the default is not. Requiring a tenant on `Agent.save()` was considered and rejected — see
  [adr/0004](adr/0004-component-tenant-isolation.md). Pinned by
  `TestNonHttpWritersInheritTheDbGuarantee`.
- **Two tenant resolvers exist and they disagree.** `egpt.os.tenancy.current_tenant_id`
  (middleware-written request state) and `egpt.os.utils.resolve_tenant_id` (factory-context claims,
  used by the workflow websocket path, which has no `Request`). The second **is** on a component
  read path — `workflows/router.py:125` feeds it into `get_workflow_by_id_async`, which reaches
  `db.get_config`. For a verified principal carrying no tenant claim, the HTTP path resolves `None`
  (refused under `require_tenant_id`) while the WS path resolves `tenant_id="<user_id>"`, using a
  user id as a tenant namespace. Reads stay inside that namespace, so it is not a cross-tenant leak,
  but it is a second isolation model reachable from one credential, and a component written there is
  invisible to the same user over HTTP. Tracked as
  [aicoe-tracker#174](https://github.com/Saturam-Inc/aicoe-tracker/issues/174).
- **Cache staleness across workers is bounded by the TTL**, not eliminated: only the worker
  that processes a mutation evicts immediately. Event-driven invalidation is still unbuilt —
  EGPT-2.6 delivered the audit trail, not this.
- **A hard-deleted component's audit history is unreachable over HTTP**, though the rows
  survive in the table. See [component-lifecycle.md](component-lifecycle.md) §3.
- **Seeding and importing definitions.** Rows are stamped with the *target* tenant and a
  component owned by another tenant is never written to. But `component_id` is the global
  primary key, so the same corpus cannot be seeded verbatim into two tenants — it needs
  `--id-prefix`. See [component-portability.md](component-portability.md) §3.
