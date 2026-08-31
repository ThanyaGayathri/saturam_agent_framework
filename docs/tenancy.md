# Tenant scoping (EGPT-2.4, runtime hydration EGPT-2.5, row-level enforcement EGPT-2.7, sessions/runs aicoe-tracker#197, memory aicoe-tracker#175)

Normative reference for how a request's tenant is resolved and what the component registry API
does with it. Companion to [capability-policy.md](capability-policy.md), which covers *which
capabilities* a tenant may reference; this document covers *which rows* a caller may reach.

Scoping is enforced at two layers, and sections 6–9 are the second one. Sections 1–5 cover the
request-level gates: how the tenant is resolved from the credential and what the `/components` API
and runtime hydration do with it. Section 6 covers the data layer underneath, where every component
query requires a tenant regardless of which caller reached it. Sections 8 and 9 cover the identical
enforcement for sessions/runs and for memory respectively, each with its own departures from
section 6's pattern that made a direct copy unsafe.

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
  write to it, with one exception: the id-uniqueness probe behind `create_*` is the same
  deliberately unscoped `component_id_is_taken` described in section 7, since only an
  unscoped check predicts what the write will do. The default `None` keeps the old unscoped
  behaviour — acceptable only for single-tenant or operator-facing agents.
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

There is a fifteenth component method, `component_id_is_taken`, and its `tenant_id` story is
different from every method above: it carries no `tenant_id` parameter at all, not even a
keyword-only one, and it is the one deliberate exception to the guard this section describes.
See section 7 for why.

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
- `tests/unit/db/test_component_tenant_adversarial.py` — tenant B attempts all fifteen methods
  against tenant A's ids; tenant A's four tables are then compared row by row via raw SQL. A
  mutation refusing in its return value while having already written is caught here, as is any
  audit row written by a refused mutation. The fifteenth, `component_id_is_taken`, is expected
  to *succeed* and see tenant A's id -- it is the deliberate exception -- so its assertion is
  that tenant A's state is untouched, not that the call is refused.
- `tests/integration/os/tenancy_postgres/test_adversarial_tenant_isolation.py` — the same sweep on
  real Postgres, plus every component route driven with tenant B's real JWT. The route assertion
  is *indistinguishability* from a nonexistent component rather than a fixed status code: routes
  legitimately differ (`GET /components/{id}` 404s, `/configs` answers `200 []`), but no route may
  let B tell a foreign component apart from a missing one.

### Not covered by this layer

Memories. The `memories` table gained a nullable, indexed `tenant_id` column (SM-C3 #91), but
nothing filters on it yet — `MemoryManager` still partitions by `user_id` alone; the read/write
path is aicoe-tracker#175. Sessions and runs are covered below (section 8) as of aicoe-tracker#197.

## 7. Known limits

- **`component_id` is globally unique**, so two tenants cannot both own `support-agent`. The 404
  hides who holds it; the id is still unavailable. Every "is this id taken?" question — the
  import/seed planner's collision pre-pass and `tools/studio.py`'s id-uniqueness probe — therefore
  goes through one deliberately **unscoped** probe, `BaseDb.component_id_is_taken`
  ([aicoe-tracker#176](https://github.com/Saturam-Inc/aicoe-tracker/issues/176)): it sees every
  tenant's live rows, takes no `tenant_id` parameter at all, and is permitted even under
  `require_tenant_id`. That is a one-bit cross-tenant existence oracle, accepted deliberately.
  What keeps it tolerable: the probe returns no row data, and the conflict it produces
  (`ComponentIdConflictError`, HTTP 409) reads identically whether the holder is the caller's
  tenant or a foreign one, so it hands a prober nothing the collision itself does not already
  reveal. The alternative — a probe scoped to the caller's tenant — answers wrongly: the collision
  then surfaces as a raw primary-key error, and on the import path was misreported as a
  soft-deleted holder. The exemption is declared in
  `egpt/db/component_scope.py::UNSCOPED_EXISTENCE_PROBES` and pinned by
  `tests/unit/db/test_component_tenant_fail_closed.py`, which asserts the probe can never grow a
  tenant parameter. Removing the oracle entirely still means a per-tenant primary key, which
  remains the correct end state if that migration ever gets appetite.

  The one-bit and "reads identically" claims above hold only for a **live** holder. The probe
  does not count soft-deleted rows (matching `get_component`'s visibility), so a *soft-deleted
  foreign* holder is invisible to it — and to `upsert_component`'s tenant-scoped existing-row
  lookup, which studio's insert relies on. One asymmetry follows from that gap: `tools/studio.py`'s
  `create_*` still ends in a raw `IntegrityError` for a soft-deleted foreign holder (the probe
  only steps over a foreign-held *live* id). The two write routes no longer disagree on this,
  though: both `POST /components` and the import route now map a live *or* soft-deleted
  collision to the same fixed, owner-blind "already exists" body — the import route's handler
  discards `ComponentSoftDeletedError`'s distinguishing detail rather than echoing it, so a
  prober can no longer use it as a tell. Degrading studio's write-time `IntegrityError` to
  `ComponentIdConflictError` when the holder is foreign remains tracked as follow-up work under
  aicoe-tracker#176.
- **Memories are unscoped.** Hydration itself is scoped (section 5) and the agents/teams/workflows
  routers are tenant-gated, but `MemoryManager` still partitions by `user_id` alone
  (aicoe-tracker#175). Sessions and runs were the identical gap until aicoe-tracker#197 (section 8)
  — that column and migration exist now, but see section 8 for exactly how much of the gap it
  closes: the DB layer and `Workflow`'s own session read/write are complete; `Agent`/`Team` and the
  session *write* path in general remain a documented follow-up.
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

## 8. Row-level enforcement for sessions and runs (aicoe-tracker#197)

Section 6 covers the four component tables. This section covers the `sessions` table, which
gained the identical `tenant_id` column and enforcement primitive **on the backends listed in the
support matrix below** — not universally; see that subsection before enabling
`require_session_tenant_id` on a non-SQL backend — with two deliberate departures from the
component pattern that this section exists to explain.

There is no separate "runs" table to scope. A `RunOutput`/`TeamRunOutput`/`WorkflowRunOutput`
lives inside the `runs` JSON column of the session row it belongs to (`AgentSession.upsert_run`
and siblings), so scoping the session read closes the run-read gap the ADR named:
`Workflow.aget_run_output` → `aget_session` → `_aread_session` all bottom out in
`BaseDb.get_session`.

### `tenant_id` is optional, not required

The fourteen component methods made `tenant_id` keyword-only with **no default**, so a forgotten
argument is a `TypeError` at check time. The seven session methods (`get_session`, `get_sessions`,
`upsert_session`, `upsert_sessions`, `rename_session`, `delete_session`, `delete_sessions`) instead
default it to `None` (unscoped) — the same posture `Agent.save`/`Team.save`/`Workflow.save` already
take for components (section 7, "The non-HTTP writers default to unscoped"). The component surface
was narrow enough that every caller could be updated in the same PR; the session surface is not —
`get_session`/`upsert_session` and friends are called from roughly a hundred sites deep inside the
agent/team/workflow run loop (`agent/_run.py`, `team/_run.py`, `workflow.py`), and a required
parameter there risks silently breaking a call site this change cannot enumerate.

### `require_session_tenant_id` is a separate flag from `require_tenant_id`

This is the more consequential departure, and skipping it is the mistake to avoid if this pattern
is ever extended to another table. `EgptOS` turns `require_tenant_id` on for every registered db
automatically, the moment an operator configures `AuthorizationConfig(tenant_id_claim=...)`. That
is safe for components, because the write paths that stamp a component's tenant (the
`/components` router, `Agent.save`) were updated in the same PR that added the guard.

It would not be safe for sessions. As of this section, the *read* chain the ADR named is fully
scoped (below), but session *writes* — `read_or_create_session`, `save_session`/`asave_session`,
and the ~100 internal call sites that invoke them — are **not** threaded with a tenant in general
(the one exception is `Workflow`, see below). If session enforcement shared `require_tenant_id`,
turning on tenancy for components would also make every agent/team/workflow *run* start raising
`TenantScopeRequiredError` the moment it tried to save its own session with the implicit
`tenant_id=None` every untouched call site still passes — not just the read endpoints this issue
is about.

So `require_session_tenant_id` is its own flag (`BaseDb`/`AsyncBaseDb` constructor argument,
`EGPT_REQUIRE_SESSION_TENANT_ID` env var), off by default, and **not** auto-enabled by `EgptOS`.
An operator opts in only once they have verified their deployment's session writes are
tenant-safe. Both flags reuse the same underlying primitive (`resolve_tenant_scope`) via separate
guard methods, `BaseDb._require_tenant_scope` (component) and `BaseDb._require_session_tenant_scope`
(session) — mirrored on `AsyncBaseDb`, which has no component methods at all and so only ever
needed the session guard.

### `Workflow` closes the read/write consistency gap for itself

Scoping *reads* only is not enough by itself: if a scoped read filters `WHERE tenant_id = 'acme'`
but the write that created the row left `tenant_id` `NULL` (the default, since writes are not
threaded), the read finds nothing — a caller can no longer see the run it just started. That is a
functional regression, not a fix.

`Workflow` closes this without threading `tenant_id` through its ~100 internal session call sites.
It carries `tenant_id` as a declared dataclass field (`self.tenant_id`, default `None`), stamped
by `get_workflow_by_id`/`Workflow.from_dict` from the caller's resolved tenant at hydration. Both
the read path (`_aread_session`/`_read_session`) and the write path
(`_aupsert_session`/`_upsert_session`) fall back to `self.tenant_id` when a call does not
explicitly override it — so a session this workflow instance creates and a later scoped read
against the same instance agree on the tenant, with no per-call-site changes needed anywhere else
in the run loop. A declared field rather than a plain attribute, specifically so it survives
`deep_copy()` — `os/utils.py` deep-copies a workflow once per request for isolation, and
`egpt/agent/_utils.py::deep_copy`'s handling of `model_provider` documents what a plain attribute
loses there. Verified for every non-factory hydration path reaching `workflows/router.py`
(`resolve_workflow`, the `os/utils.py` `get_workflow_by_id`/`_async` wrapper, and the module-level
`get_workflows`): none of them re-copy or reconstruct the instance after hydration, so
`.tenant_id` reaches every downstream `.aget_session`/`.aget_run_output`/`.arun`/`.acancel_run`/
`.acontinue_run` call correctly, whether or not that call passes `tenant_id` explicitly.

**`Agent` and `Team` do not have the equivalent instance-level default yet.** Their
`aget_run_output`/`aget_session`/`get_run_output`/`get_session` gained the same optional
`tenant_id` parameter as `Workflow`'s (so the DB-layer capability is there, and an explicit
override always works), but nothing stamps a default onto the instance the way
`get_workflow_by_id` does for `Workflow`, because `Agent.__init__`/`Team.__init__` are large,
heavily-used, hand-written constructors and adding a field there carries materially more
regression risk than the same change on `Workflow`. Wiring `Agent`/`Team` WS/HTTP routes to pass
a real tenant into these reads without that instance-level default would hit the identical
read/write mismatch `Workflow` avoids — a request with tenancy configured would find its own
just-created session invisible. Tracked as follow-up work, not done in this PR.

**Factory-produced workflows are a related, pre-existing gap `Workflow.tenant_id` does not close.**
`BaseFactory._post_resolve`/`WorkflowFactory._post_resolve` never copy `RequestContext.tenant_id`
onto the produced instance, so a factory-produced `Workflow`'s `.tenant_id` stays `None`
regardless of the caller's identity — session/run reads for it fall back to unscoped. Not
introduced or worsened by this change; flagged here because it shares the same instance-level
mechanism and would need the identical fix (the factory pipeline stamping `.tenant_id` from
`ctx.tenant_id` in `_post_resolve`).

**Enabling `require_session_tenant_id` today silently disables session persistence for `Agent`
and `Team` runs, rather than raising.** `Agent.save_session`/`Team.save_session` call
`upsert_session` with no `tenant_id` (see above), so under the flag `_require_session_tenant_scope`
raises `TenantScopeRequiredError` on every save — but the storage helpers that call it
(`agent/_storage.py::upsert_session`/`aupsert_session`, and the `team` equivalents) catch that
exception, log a warning, and return `None`. The run itself completes normally and reports success;
nothing surfaces to the caller, and the session record, chat history and resumption state for
that run are simply never written. This is a direct, expected consequence of `Agent`/`Team` not
yet having the instance-level `tenant_id` default `Workflow` has (previous point) — it is not a bug
in the flag, but an operator turning it on today should know the actual behavior is "goes quiet",
not "raises," until that follow-up lands.

### Backend support matrix

`require_session_tenant_id` is read by every `BaseDb`/`AsyncBaseDb` backend (it is a constructor
argument on the abstract base), but only some backends have a `_require_session_tenant_scope` call
wired into their session methods that actually filters or stamps by it. The rest construct-time
refuse `require_session_tenant_id=True` instead (`egpt.db.session_scope.
require_unsupported_session_tenant_scope`) — the flag can never silently report protection it
does not provide.

| Backend | Session tenant enforcement |
|---|---|
| `SqliteDb` / `AsyncSqliteDb` | Enforced (singular + bulk upsert both tenant-guarded) |
| `PostgresDb` / `AsyncPostgresDb` | Enforced (singular + bulk upsert both tenant-guarded) |
| `MySQLDb` / `AsyncMySQLDb` | Enforced (bulk upsert pre-checks ownership per-row; `ON DUPLICATE KEY UPDATE` has no `WHERE`, so it can't be gated in the statement itself the way SQL/Postgres are) |
| `SingleStoreDb` | Enforced, same pre-check approach as MySQL |
| `InMemoryDb` | Enforced |
| `JsonDb`, `GcsJsonDb`, `DynamoDb`, `FirestoreDb`, `MongoDb`, `AsyncMongoDb`, `RedisDb`, `SurrealDb` | **Not implemented.** Constructing one with `require_session_tenant_id=True` (explicitly or via `EGPT_REQUIRE_SESSION_TENANT_ID`) raises `NotImplementedError` at `__init__`. |
| `ClickHouseDb` | N/A — trace-only backend, session methods are unimplemented by design |

The **HTTP session router** (`os/routers/sessions/session.py`) has zero tenant awareness across
its seven `db.get_session`/`db.upsert_session`/… call sites — it does not pass `tenant_id` at all.
That is outside this PR's stated scope (the ticket names the workflow read chain:
`Workflow.aget_run_output` → `aget_session` → `_aread_session`), but it bounds how much of the
product this section's enforcement actually covers today, and belongs on the follow-up ticket
rather than being inferred as done.

### Where the tenant is threaded on the read side

`Workflow.aget_run_output`/`get_run_output`, `aget_session`/`get_session`, and
`_aread_session`/`_read_session` all take the same optional `tenant_id`, forwarded to
`BaseDb.get_session(..., tenant_id=...)`. The WS `reconnect` and `continue-workflow` handlers
(`egpt/os/routers/workflows/router.py`) pass the same `hydration_tenant_id` used to scope the
workflow's own hydration (`tenant_for_ws_hydration`) into the `aget_run_output` call that follows
it — redundant with the `self.tenant_id` fallback above (both resolve to the same value) but kept
explicit at those two call sites for auditability, since they are the exact chain named in the
ADR's "remaining limits" note. Every other route (`get_workflow_run`, `list_workflow_runs`,
`continue_workflow_run`, `resume_workflow_run_stream`, `cancel_workflow_run`) relies on the
`self.tenant_id` fallback alone and needed no changes, because `resolve_workflow`/`get_workflow_by_id`
already compute a tenant for the component-hydration call that precedes every one of them.

### Migration and enforcement

`db/migrations/versions/v2_9_0.py` adds a nullable, indexed `tenant_id` column to `sessions` —
same contract as the component tables and `memories`: unbackfilled, and a row written before this
column existed reads as unscoped. The index is built with a plain `CREATE INDEX` inside the
migration's transaction, same convention as `v2_7_0.py`/`v2_8_0.py` — on Postgres this holds a lock
for the build's duration and blocks writes to `sessions` while it runs. `sessions` is the hot-path
table this PR's own description calls out (~100 write call sites in the run loop), so schedule this
migration's rollout on a large existing table accordingly, same caveat as the two prior migrations.
Session read predicates use strict equality
(`WHERE tenant_id = :tenant`, not `tenant_id IS NULL OR tenant_id = :tenant`), matching the
component pattern: under `require_session_tenant_id`, a NULL-tenant row is invisible to a
tenant-scoped read, the same "unscoped is the widest tenancy" reasoning section 6 gives for
components. Full test coverage: `tests/unit/db/test_session_tenant_fail_closed.py` (the guard is
wired in, the two flags are independent, the optional-parameter behaviour), `tests/unit/db/
test_session_tenant_adversarial.py` (tenant B cannot read, list, rename, delete, or overwrite
tenant A's session), and `tests/unit/workflow/test_workflow_tenant_scoping.py` (the
`self.tenant_id` read/write consistency and `deep_copy()` propagation).

## 9. Row-level enforcement for memory (aicoe-tracker#175)

Sections 6 and 8 cover components and sessions. This section is the same pattern's third
application: the `memories` table, whose nullable `tenant_id` column SM-C3 (#91) added but which
nothing read or wrote until now. `MemoryManager`'s CRUD (`get_user_memories`, `add_user_memory`,
`search_user_memories`, `delete_user_memory`, `clear`, and their async twins) threads an optional
`tenant_id` to `BaseDb`/`AsyncBaseDb`'s memory methods, the same "optional, not required" shape
section 8 uses for sessions and for the identical reason: this surface is reachable from the
agentic memory-creation loop and from any downstream code that reads or writes memories directly,
most of which has no tenant to name in an ordinary single-tenant deployment.

### `require_memory_tenant_id` is a third, independent flag

Not shared with `require_tenant_id` or `require_session_tenant_id` — see `egpt/db/memory_scope.py`
for the full argument, which mirrors section 8's for sessions: nothing threads a tenant through
the agentic memory-creation call sites (`MemoryManager.create_user_memories`/`update_memory_task`
and their async twins), so sharing either existing flag would start refusing those call sites'
unscoped writes the instant an operator turned on tenancy elsewhere. `require_memory_tenant_id` is
therefore its own constructor argument / `EGPT_REQUIRE_MEMORY_TENANT_ID` env var, off by default
and not auto-enabled by `EgptOS`, backed by its own `BaseDb._require_memory_tenant_scope` /
`AsyncBaseDb._require_memory_tenant_scope` guard method.

### Backend support matrix

Narrower than the session matrix: the tenant column itself only exists on Postgres and SQLite
(`db/migrations/versions/v2_8_0.py` — MySQL, SingleStore and DynamoDB "define a memories table but
get neither the column nor the tier tables"). `InMemoryDb` needs no migration, since it carries no
schema, so it joins the two SQL backends as the third backend that can actually filter.

| Backend | Memory tenant enforcement |
|---|---|
| `SqliteDb` / `AsyncSqliteDb` | Enforced (singular + bulk upsert both tenant-guarded; `tenant_id` is stamped only on insert, never changed by an update) |
| `PostgresDb` / `AsyncPostgresDb` | Enforced, same shape |
| `InMemoryDb` | Enforced |
| `MySQLDb`, `AsyncMySQLDb`, `SingleStoreDb`, `DynamoDb`, `FirestoreDb`, `MongoDb`, `AsyncMongoDb`, `RedisDb`, `SurrealDb`, `JsonDb`, `GcsJsonDb` | **Not implemented.** Every memory method accepts `tenant_id` for signature parity with `BaseDb`, but nothing filters or stamps by it. Constructing one with `require_memory_tenant_id=True` (explicitly or via `EGPT_REQUIRE_MEMORY_TENANT_ID`) raises `NotImplementedError` at `__init__` (`egpt.db.memory_scope.require_unsupported_memory_tenant_scope`) — the flag can never silently report protection it does not provide. |
| `ClickhouseDb` | N/A — trace-only backend; its memory methods are stubs regardless of tenancy |

### `tenant_id` on `upsert_user_memory` falls back to the row, unlike sessions

`upsert_session`'s `tenant_id` is the sole source of truth for what gets stamped — the session
object's own state is not consulted. `UserMemory` already carries its own `tenant_id` field
(unlike `Session`, whose subclasses' `tenant_id` fields are not read by the upsert path either), so
`upsert_user_memory`/`upsert_memories` fall back to `memory.tenant_id` when the keyword is not
given, rather than always stamping `NULL`. The keyword still wins when both are present, but the
enforcement check (`_require_memory_tenant_scope`) validates the *effective* tenant — the keyword
when given, otherwise the fallback to `memory.tenant_id` — not the keyword alone: a deployment
with `require_memory_tenant_id=True` and a caller relying purely on `UserMemory.tenant_id` (e.g. a
pinned single-tenant seed bridge) is not refused, as long as the memory object itself carries a
real tenant.

### `SeedMemoryProvider`'s single-tenant refusal is narrowed, not removed

`egpt/memory/adapters/seed.py`'s `SeedMemoryProvider` used to refuse to construct at all unless
the deployment declared `options={"single_tenant": True, "tenant_id": ...}` — the seed had no way
to honour `Scope.tenant_id` at all. That refusal is now conditional
(`SeedMemoryProvider._tenant_scoping_gaps`): it still fires when a `LearningStore` is bound to any
tier (no store accepts a `tenant_id` — that is a separate, unstarted piece of work) or when the
bound `MemoryManager`'s db is not in the table above, but a manager-only deployment backed by
Postgres, SQLite or `InMemoryDb` needs no `single_tenant` declaration at all: `Scope.tenant_id` is
threaded straight through to a backend that filters on it for real. A deployment may still choose
to pin explicitly even when it would not have to — `single_tenant` remains available as a
narrower, opt-in posture.

### Migration

No new migration: `db/migrations/versions/v2_8_0.py` already added the column and its index for
Postgres and SQLite. This ticket is the consumer half named in that migration's own docstring and
in SM-C3's SPEC section 11.3 row.

Full test coverage: `tests/unit/db/test_memory_tenant_scoping.py` (cross-tenant isolation on real
`SqliteDb`/`AsyncSqliteDb`, the fail-closed flag, and the unsupported-backend refusal) and
`tests/unit/memory/test_provider_seed_bridge.py` (the narrowed `SeedMemoryProvider` refusal, both
the still-refused and the newly-allowed shapes).
