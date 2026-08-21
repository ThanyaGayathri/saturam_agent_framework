# ADR 0004: Row-Level Tenant Isolation for Component Data Access

- **Status:** Proposed
- **Date:** 2026-08-12
- **Backlog:** EGPT-2.7 ([aicoe-tracker#59](https://github.com/Saturam-Inc/aicoe-tracker/issues/59))
- **Epic:** 2 – DB-defined agent registry
- **Deciders:** Architecture owners (framework), security

## Context

Tenant isolation for DB-defined components was enforced entirely *above* the data layer. EGPT-2.1
added a nullable `tenant_id` column to the four component tables and a `tenant_id` parameter to all
fourteen component methods on `BaseDb`; EGPT-2.4 gated the `/components` router on a tenant and
threaded it into every DB call; EGPT-2.5 scoped runtime hydration.

The data layer itself remained fail-open. Every one of the fourteen methods defaulted
`tenant_id=None`, and the documented contract was that `None` applies no tenant predicate. Two
consequences followed:

1. **A forgotten argument was a silent cross-tenant call.** `db.get_component("agent-1")` returned
   any tenant's row and looked like working code. Nothing — not a type checker, not a test, not a
   log line — distinguished it from a deliberate unscoped read.
2. **Non-HTTP writers had an unguarded path around the router gate.** `Agent.save()`, the studio
   toolkit, migration scripts and cookbooks hold the same `BaseDb` and reach the same rows without
   passing through a FastAPI dependency. A gate with a path around it is not a gate.

`docs/tenancy.md` recorded both as EGPT-2.7's to resolve. The requirement-owner directive was
strict tenant isolation: enforced at the lowest layer, fail-closed, and proven adversarially.

The constraint that shapes everything below: `tenant_id=None` is not simply a mistake. It is
correct for a single-tenant deployment (there is no tenant to pass) and it is the only way to reach
the NULL-`tenant_id` rows written before multi-tenancy. A change that merely forbids `None` breaks
every existing deployment.

## Decision

### 1. Two independent guards, because there are two different mistakes

`tenant_id` becomes **keyword-only with no default** on all fourteen methods, *and* `BaseDb` gains
a `require_tenant_id` flag that refuses an explicit `None`.

| Mistake | Guard | Failure | Active |
|---|---|---|---|
| Argument forgotten | required keyword-only parameter | `TypeError` at the call, `call-arg` under mypy | Always, every deployment |
| Deliberately unscoped where tenancy applies | `require_tenant_id` | `TenantScopeRequiredError` | Only when the operator opted in |

Neither subsumes the other. The flag alone leaves a forgotten `tenant_id` silent in every
single-tenant deployment — the original hole. The required parameter alone leaves `None` legal,
which is the widest possible scope. `EgptOS` sets the flag on every registered db when
`AuthorizationConfig(tenant_id_claim=...)` is configured, so the same operator opt-in that enables
the router gate enables the data-layer one.

### 2. A required parameter, not a sentinel default

The alternative was `tenant_id: Union[str, None, Literal[UNSCOPED]] = UNSCOPED`, raising at runtime
on the sentinel. Both have **identical blast radius** — a call already passing `tenant_id=X`
satisfies either, and only calls that omit it need changing. Once that ties, the required parameter
wins on every remaining axis:

- **It fails at check time, not run time.** mypy reports a `call-arg` error at every omitting call
  site, here and in downstream typed code. The sentinel produces *no* type-checker signal, because
  `disable_error_code = ["override"]` is set in `libs/agno/pyproject.toml` — Liskov checks are off,
  so a tri-state base signature is not compared against the narrower backend ones, and call sites
  type-check cleanly against the union.
- **No tri-state type** threaded through fourteen base signatures plus their backend overrides,
  and no narrowing call in each body.
- **No new public constant.** `egpt.db.UNSCOPED` would itself become a compatibility obligation.

`*` is placed immediately before `tenant_id`, so every preceding parameter keeps its
positional-ness and existing calls such as `db.delete_config("agent-1", 2, tenant_id=A)` are
unaffected.

### 3. The guard sits at the method entry, not in the predicates

`resolve_tenant_scope` is called as the first statement of each method — before the method's own
`try`, because inside it a scope refusal would be caught by the shared
`except Exception: log_error("Error getting component: ...")` wrapper and reported as a database
error, which is a misleading diagnostic for a configuration fault.

The roughly ninety `if tenant_id is not None: stmt = stmt.where(...)` predicates per backend were
**deliberately left alone**. They are already uniform; `list_components` and
`list_component_audit` already share one `where_clauses` list between their count and page queries
so the two cannot drift; and mutation testing recorded in
`tests/unit/db/test_component_tenant_scoping.py` had already established that the tenant-scoped
*prechecks* are the load-bearing layer while the inner predicates are defence in depth. Rewriting
~180 working predicates through a helper is regression risk for no enforcement gain.

What makes "mandatory" hold going forward is a test, not a convention: the fail-closed suite parses
each backend's AST and asserts the guard is the first statement of all fourteen methods, and
asserts on every backend class that `tenant_id` is keyword-only with no default.

### 4. `TenantScopeRequiredError` does not inherit from `ValueError`

The components router maps a `ValueError` out of the DB layer to a 400. An unscoped-access refusal
is not a client error — the client did nothing wrong and cannot fix it by sending something else.
It is a server-side configuration or programming fault and must not be reported as the caller's.

### 5. Scope boundary: component data access only

Session, memory and run data stays unscoped. Those tables have no `tenant_id` column, so scoping
them is a schema change with a migration, not an enforcement change. `docs/tenancy.md` previously
attributed that work to EGPT-2.7; the entry has been corrected and re-attributed.

## Consequences

**Breaking, deliberately and loudly.** Every caller of a component DB method must name a tenant.
`tenant_id=None` reproduces the previous behaviour exactly, so the migration is mechanical, and
mypy enumerates the sites rather than leaving them to be discovered in production.

**NULL-tenant rows become unreachable under strict mode.** Intended: an unscoped row in a tenanted
deployment belongs to no tenant and is therefore visible to all of them. `docs/tenancy.md` section
6 carries the assignment SQL. No backfill tool ships, because there is no tenants table to backfill
against — which tenant inherits pre-tenancy rows is a question only the operator can answer.

**A residual cross-tenant existence oracle remains, narrowed.** `component_id` is a global primary
key, so the studio toolkit's id-uniqueness probe is now scoped to its own tenant and can miss a
foreign holder; the insert then fails on the primary key. The alternative — an unscoped probe —
answers correctly but lets a caller enumerate other tenants' component names by minting ids until
one collides. A clear `IntegrityError` on a rare collision is the better failure. Closing it fully
requires a per-tenant primary key.

**Async backends and ClickHouse gained the signature but not the behaviour.** The async component
methods still raise `NotImplementedError` (maximally fail-closed: no data is reachable) and
ClickHouse's component reads still return empty. Their signatures were updated so a future
implementer inherits the contract rather than reintroducing the fail-open default.

**Two tenant resolvers existed, and they disagreed — resolved by
[aicoe-tracker#174](https://github.com/Saturam-Inc/aicoe-tracker/issues/174).**
`egpt.os.utils.resolve_tenant_id` serves the factory/websocket path, which has no `Request` to
read `request.state.tenant_id` from.

An earlier draft of this ADR claimed it "is not on the component data-access path". **That was
wrong**, and the correction matters because it changes what the limit actually is.
`workflows/router.py:125` feeds its result into `get_workflow_by_id_async(..., tenant_id=...)`,
which reaches hydration and `db.get_config` — so it *is* a component read path, and this ADR's
enforcement applies to it.

The underlying problem was that the two resolvers produced different answers for the same
principal. `resolve_tenant_id` falls back to `user_id` (when that id is the middleware-verified
one) where `egpt.os.tenancy.current_tenant_id` returns `None`:

| Principal | HTTP path | WS / factory path (before the fix) |
|---|---|---|
| JWT with the tenant claim | that claim | that claim |
| Verified JWT, **no** tenant claim | `None` → refused under `require_tenant_id` | `tenant_id="alice"` — the user id used as a tenant namespace |

So the WS path failed **open** relative to HTTP: it scoped to a namespace derived from a user id
rather than refusing. Reads stayed confined to that namespace, so it was not a cross-tenant leak,
but it was a second isolation model reachable from the same credential — and a component written
under `tenant_id="alice"` was invisible to the same user's HTTP requests.

The fix keeps `resolve_tenant_id`'s `user_id` fallback (it is the right default for
`ModelPolicy`/budget selection — `RequestContext.tenant_id`, `RunContext.tenant_id` — which this
ADR does not otherwise reason about) but stops it from ever reaching a component read. The
WebSocket dispatcher (`egpt/os/router.py`) now populates `websocket_user_context["tenant_id"]`
the same way `AuthMiddleware` populates `request.state.tenant_id`: the configured claim,
verbatim, no fallback. `egpt.os.utils.tenant_for_ws_hydration` reads that (plus
`websocket_user_context["tenancy_enabled"]`) to scope every WS hydration call — start-workflow,
reconnect, and continue-workflow — with the same fail-closed contract `_tenant_for_hydration`
already gave HTTP, in-memory components exempted the same way. A verified caller with no tenant
claim is now refused identically on both paths; see
`libs/agno/tests/unit/os/test_hydration_resolvers.py::TestTenantForWsHydration` and
`libs/agno/tests/integration/os/test_workflow_runs.py`'s WS/HTTP tenant-reconciliation tests.

**The non-HTTP entry points keep an unscoped default.** `Agent.save/delete`, `Team.save/delete`
and `Workflow.save/delete` take `tenant_id` but default it to `None`. On a **non-strict** db that
means an unscoped write can update a row owned by another tenant sharing the same
`component_id` — and the row keeps its original `tenant_id`, so the owner's correctly-scoped read
then serves the foreign config with nothing to detect.

This is a deliberate trade and is not what the fourteen data-layer methods do, so the asymmetry
needs stating. Requiring a tenant on `Agent.save()` — the framework's most-used public API, and
the one every quickstart calls — would force every single-tenant deployment to write
`save(tenant_id=None)`: pure ceremony for a caller who has exactly one tenant, in exchange for
catching a plumbing mistake that the data layer's own required parameter already catches one
level down. The required parameter earns its cost on internal plumbing; it does not earn it on
the user-facing entry point.

`require_tenant_id` is therefore the control for these three writers, not their default — and
that is only a real control because the flag now survives `clone_with_table_overrides` and
`to_dict`/`from_dict`. It did not in the first version of this change, which meant the stated
mitigation was silently absent on the component rehydration path that clones the db. Both
behaviours are pinned by `TestNonHttpWritersInheritTheDbGuarantee`, so the trade is executable
fact rather than a claim in a docstring, and reversing it is a test change rather than an
archaeology exercise.

## Alternatives Considered

| Alternative | Why rejected |
|---|---|
| API-level scoping only (status quo after EGPT-2.4) | Single point of failure, and no coverage of non-HTTP writers holding the same `BaseDb` |
| Sentinel default (`UNSCOPED`) | Same blast radius, but runtime-only failure where a static one is available — `disable_error_code = ["override"]` means it yields no mypy signal |
| `require_tenant_id` flag alone (non-breaking) | Leaves a forgotten `tenant_id` silently cross-tenant in every deployment that has not opted in — the exact hole this ADR exists to close |
| Hard-require `tenant_id` with no unscoped escape | Breaks every single-tenant deployment and makes pre-multi-tenancy NULL rows permanently unreachable |
| A `ContextVar` carrying the ambient tenant | Rejected by this codebase on exactly this boundary — see `factory/utils.py`, `run/base.py`, `providers/budget_runtime.py`. Identity stays explicitly threaded |
| Centralise the ~90 per-backend tenant predicates | Regression risk across two independent implementations for no enforcement gain over an entry guard plus AST conformance test |
| Add `tenant_id` to `filter_converter.allowed_columns` | `filter_converter` is trace-only, component queries never pass through it, and the traces table has no `tenant_id` column — the SQL would reference a column that does not exist |
| Add a `filter_expr` parameter to `list_components` to make a component whitelist reachable | New public API surface the requirement does not ask for |

## References

- [tenancy.md](../tenancy.md) section 6 — normative description of the three states and strict mode
- [capability-policy.md](../capability-policy.md) — which capabilities a tenant may reference
- `libs/agno/egpt/db/component_scope.py` — the guard and the tenant-column declaration
- `libs/agno/tests/unit/db/test_component_tenant_fail_closed.py` — signature and AST conformance
- `libs/agno/tests/unit/db/test_component_tenant_adversarial.py` — adversarial-tenant sweep
- `libs/agno/tests/integration/os/tenancy_postgres/test_adversarial_tenant_isolation.py` — the same
  on real Postgres, plus every route with real JWTs
