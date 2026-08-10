# Tenant scoping (EGPT-2.4)

Normative reference for how a request's tenant is resolved and what the component registry API
does with it. Companion to [capability-policy.md](capability-policy.md), which covers *which
capabilities* a tenant may reference; this document covers *which rows* a caller may reach.

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
| `OS_SECURITY_KEY` root | `None` | unreachable in practice: the key path only runs when JWT is not configured, and `tenancy_enabled` is false there |

### Why an absent claim is a refusal

`None` means *unscoped*, and unscoped is the **widest** tenancy — it applies no `WHERE
tenant_id = ...` at all, so the caller reads and edits every tenant's rows. Serving it would
mean a token that simply omits the claim is more powerful than one that carries it. The gate is
`require_tenant_id`, attached at router level.

It is **components-only** on purpose. Making it global in the middleware would lock out every
existing untenanted caller on every other router; sequencing that is EGPT-2.7's.

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
- A tenanted caller naming a *different* tenant → **403**.
- An unscoped (operator) caller may name any tenant. Without this there would be no way to
  create the first tenanted machine identity.

Listing and revocation are tenant-scoped: another tenant's account reads as absent, so it is
neither disclosed nor revocable.

Account **name** uniqueness stays global rather than becoming per-tenant. The `uq_active_name`
partial unique index is not cleanly reversible if rebuilt, and two tenants wanting the same
account name is a naming annoyance, not a security boundary.

### ⚠️ Upgrade note

Migration `v2_7_0` adds the column. Migrations are **not** auto-run, and `_get_table` raises
when a live table is missing a column the schema declares — which `ServiceAccountVerifier` turns
into `UNAVAILABLE`. So a deployment that ships this code before running the migration will fail
**every** service-account token with a 503 until it does:

```python
asyncio.run(MigrationManager(db).up())
```

Run it first, or in the same maintenance step. Reverting is
`MigrationManager(db).down(target_version="2.6.0")`; on SQLite the column drop needs 3.35+.

Service accounts minted before this change have `tenant_id = NULL`. In a tenancy-enabled
deployment they are refused on the component routes and must be re-minted.

---

## 5. Known limits

- **Runtime hydration is not tenant-scoped.** `get_agent_by_id`, `Agent.load` and the team and
  workflow equivalents read a stored config with no tenant predicate, so `POST /agents/{id}/runs`
  can still hydrate another tenant's component even though `GET /components/{id}` would 404. The
  capability policy is the only control there today. **EGPT-2.5.**
- **`tools/studio.py` runs unscoped** — the in-process toolkit over the same tables has no HTTP
  principal to derive a tenant from. Do not expose it to a tenant-facing agent until 2.5.
- **The dry-run hydration in write validation is unscoped**, so a team config naming another
  tenant's component as a member hydrates successfully. Whether the write is accepted is
  therefore a weak existence oracle for foreign component ids. **EGPT-2.5.**
- **`component_id` is globally unique**, so two tenants cannot both own `support-agent`. The 404
  hides who holds it; the id is still unavailable.
- **Every router other than `/components` is unscoped** — sessions, memories, runs. **EGPT-2.7.**
- **The DB layer does not require a tenant.** `tenant_id=None` still means "no predicate" at
  every `BaseDb` method; making it mandatory is **EGPT-2.7**.
