# Memory scope convention

*P0 deliverable. Agreed by Lane K; implemented by K1 in P1. Addresses G1 and G2.*

What scope every run writes under, what it may read, and why the obvious answer is wrong.
Written against the code rather than against the plan, and one of the plan's stated
resolutions does not survive that.

---

## The five slots, and the asymmetry that matters

`Scope` (`libs/agno/egpt/memory/types.py:276`) has five slots:

```
SCOPE_SLOTS = ("tenant_id", "agent_id", "user_id", "team_id", "session_id")
```

`tenant_id` is mandatory and rejected when blank. The other four are optional. Every slot is
stripped, and a blank string is refused with a message telling you to pass `None` instead.

The rule that governs everything below is in `scope_predicate`:

> A slot the **call** scope sets becomes an equality. A slot left `None` contributes no
> clause and therefore means **any**.

So an unset slot widens a read. It does not select rows whose column is empty — it stops
filtering on that column at all. That asymmetry is the whole of this document.

---

## The defect in G2's stated resolution

G2 says:

> Define a system scope convention: scheduled runs write with tenant and agent set and user
> left empty, which `Scope` permits since only the tenant is mandatory.

`Scope` does permit it. The trouble is what happens afterwards, and it is verifiable in three
lines:

| Scope | Predicate |
|---|---|
| Scheduled write, user unset | `tenant_id = :tenant_id AND agent_id = :agent_id` |
| Interactive read as `alice` | `tenant_id = :tenant_id AND user_id = :user_id` |
| Tenant-wide read, user unset | `tenant_id = :tenant_id` |

Three consequences, none of them what was intended:

1. **A user-scoped read can never see a scheduled run's writes.** The read filters
   `user_id = 'alice'`; the scheduled row has no `user_id`. It does not match.
2. **The only read that reaches those rows is tenant-wide**, and a tenant-wide read also
   returns every other user's private memories. Under D3 there is one deployment per
   customer, but there are many people inside it, so this is not a small leak.
3. **There is no way to ask for "rows with no user".** An unset slot means *any*, and the
   empty-string sentinel that would express *none* is rejected by `Scope.__post_init__`
   with the reason spelled out: a blank slot counts as set and narrows every read to rows
   that are also blank.

So scheduled writes under G2's wording are either invisible or reachable only by a read that
leaks. The resolution needs one change.

---

## The convention

**Scheduled runs write with `user_id` set to `__scheduler__`.**

Not a name invented here. The framework already assigns it and already protects it:

| Where | What |
|---|---|
| `os/middleware/jwt.py:34` | `INTERNAL_SCHEDULER_USER_ID = "__scheduler__"` |
| `os/auth.py:255` | sets `request.state.user_id = "__scheduler__"` |
| `os/actor.py:15` | documents it as the platform scheduler principal |
| `os/service_accounts.py:60` | forbids a leading underscore in service-account names, *so that nothing can collide with it* |
| `os/middleware/user_scope.py:149` | refuses a client claiming it |

Using the identity the platform already stamps means the scheduler's memory partition and
its audit attribution are the same string, and no new reserved word has to be defended.

### Write scopes

| Run | tenant | agent | user | session |
|---|---|---|---|---|
| **Interactive** | customer slug | agent id | the person | session id |
| **Scheduled** | customer slug | agent id | `__scheduler__` | the run id |

### Read scopes

| Reader | Scope | Returns |
|---|---|---|
| **Interactive** | tenant + user | That person's memories, across agents and sessions |
| **System** | tenant + `__scheduler__` | What scheduled runs have learned |
| **Both** | two reads, merged | — |

`agent_id` and `session_id` are deliberately dropped on reads, which is what
`_managers.py` already does. A memory learned in one session must be recallable in the next,
or the second-conversation test in K1 fails by construction.

**Never read tenant-only.** It is the one scope that crosses users, and it is indistinguishable
from a correctly narrow read at the call site. If a caller needs both a person's memories and
the system's, that is two reads.

### What a scheduled run may read

- **Its own prior system memories** — tenant + `__scheduler__`. This is what lets a nightly
  run know what the last one found.
- **Nothing belonging to a person.** A scheduled run has no user, and the only scope that
  would reach a user's rows is the tenant-wide read this convention forbids. If a scheduled
  run genuinely needs a person's context, that is a product decision to surface, not a scope
  to widen.

---

## Two operational rules that fall out of it

**Every agent that writes memory needs a stable `id` or `name`.** `agent_id` is
`generate_id_from_name(agent.name)`, and for an agent with neither, that is a uuid4-derived
value minted per construction — a fresh partition on every process start. Writes still land;
they land somewhere new each restart. This is why reads drop `agent_id` today, and it is a
startup check K1 should carry: an agent with a memory provider and no stable identity is
misconfigured.

**A single-tenant deployment still needs a tenant id.** `_managers.py:576` returns without
touching memory when `run_context.tenant_id is None`, silently. One deployment per customer
with the tenancy flags off produces exactly that, so the customer slug is stamped onto every
run context by middleware from configuration. G1's startup check must fail loudly rather than
let the deployment run memoryless — a system that remembers nothing and reports nothing is
worse than one that refuses to start.

---

## Verification

`cookbook/94_retail/K01_memory_scope_convention.py` builds each scope above against the real
`Scope` class, and runs the fragment `scope_predicate` actually emits against a one-row SQLite
table to decide whether a stored row is visible to a given read. That last part matters: an
earlier version reimplemented the "unset means any" rule in Python, so the matrix agreed with
itself regardless of what the real function did — mutating `scope_predicate` to emit `IS NULL`
for an unset slot left it entirely unmoved. It now fails, which is the only reason to trust it.

It needs no database. SQLite stands in for the warehouse because the fragment is plain
parameterised SQL with `:name` binds and no dialect-specific syntax.

## Open

**Consolidation across the boundary.** Memory consolidation turns episodes into facts. Whether
a scheduled run's episodes may consolidate into facts that a user-scoped read can see is not
settled here, and the honest default is no — a fact derived from system episodes stays under
`__scheduler__`. Raise it when the first use case wants otherwise.
