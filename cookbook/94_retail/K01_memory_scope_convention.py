"""K1 -- the memory scope convention, checked against the real Scope class.

The P0 deliverable is an agreement, but an agreement about scoping is worth exactly as much
as the predicate it produces, so this builds every scope in
`product/egpt_retail/contracts/MEMORY_SCOPE.md` and prints what the substrate would actually
filter on.

Needs no database. `scope_predicate` is pure, and its output is the thing being agreed.

Run:
    .venv/bin/python cookbook/94_retail/K01_memory_scope_convention.py
"""

from egpt.integrations.memory.vector.pgvector.sql import scope_predicate
from egpt.memory.types import SCOPE_SLOTS, Scope
from egpt.os.middleware.jwt import INTERNAL_SCHEDULER_USER_ID

TENANT = "middleby"
AGENT = "rfq_explainer"


def predicate(scope: Scope) -> str:
    fragment, _binds = scope_predicate(scope)
    return fragment


def show(label: str, scope: Scope) -> None:
    print(f"  {label}")
    print(f"    key       {scope.key()}")
    print(f"    predicate {predicate(scope)}")


print("Memory scope convention -- K1 / G1 / G2")
print("=" * 78)
print()
print("Slots:", ", ".join(SCOPE_SLOTS))
print("Only tenant_id is mandatory. A set slot filters; an unset slot means ANY.")
print("Scheduler identity, from the framework:", INTERNAL_SCHEDULER_USER_ID)

# --- 1. Why G2's stated resolution does not work ---------------------------------------

print()
print("1. G2 as written: scheduled runs write with user left unset")
print("-" * 78)
g2_write = Scope(tenant_id=TENANT, agent_id=AGENT)
alice_read = Scope(tenant_id=TENANT, user_id="alice")
tenant_wide = Scope(tenant_id=TENANT)
show("scheduled write (user unset)", g2_write)
show("interactive read as alice", alice_read)
show("tenant-wide read", tenant_wide)

print()
print("  Consequences:")
print("    - alice's read filters user_id = 'alice'; the scheduled row has no user_id,")
print("      so it can never match. The write is invisible to every user-scoped read.")
print("    - The only scope that reaches it is the tenant-wide read, which returns")
print("      every other person's memories too.")
try:
    Scope(tenant_id=TENANT, user_id="")
    print("    - FAIL: a blank user_id was accepted as a 'no user' sentinel")
except ValueError:
    print("    - And there is no 'rows with no user' scope: a blank slot is refused,")
    print("      because blank counts as SET and would narrow reads to blank rows.")

# --- 2. The convention -----------------------------------------------------------------

print()
print("2. The convention: scheduled runs write as __scheduler__")
print("-" * 78)
scheduled_write = Scope(
    tenant_id=TENANT,
    agent_id=AGENT,
    user_id=INTERNAL_SCHEDULER_USER_ID,
    session_id="run-2026-09-07-0200",
)
interactive_write = Scope(
    tenant_id=TENANT, agent_id=AGENT, user_id="alice", session_id="sess-91"
)
system_read = Scope(tenant_id=TENANT, user_id=INTERNAL_SCHEDULER_USER_ID)

print("  Writes")
show("interactive", interactive_write)
show("scheduled", scheduled_write)
print()
print("  Reads (agent and session dropped, as _managers.py already does)")
show("interactive, alice", alice_read)
show("system", system_read)

# --- 3. The isolation the convention buys ----------------------------------------------

print()
print("3. Isolation")
print("-" * 78)


def matches(row: Scope, read: Scope) -> bool:
    """Whether a stored row satisfies a read's equality predicate."""
    for slot in SCOPE_SLOTS:
        wanted = getattr(read, slot)
        if wanted is not None and getattr(row, slot) != wanted:
            return False
    return True


rows = {
    "alice's memory": interactive_write,
    "bob's memory": Scope(
        tenant_id=TENANT, agent_id=AGENT, user_id="bob", session_id="sess-92"
    ),
    "scheduled run's memory": scheduled_write,
}
reads = {
    "alice reads": alice_read,
    "system reads": system_read,
    "tenant-wide read (forbidden)": tenant_wide,
}
print(f"  {'':<30}" + "".join(f"{name:<26}" for name in rows))
for read_name, read_scope in reads.items():
    cells = "".join(
        f"{('yes' if matches(row, read_scope) else 'no'):<26}" for row in rows.values()
    )
    print(f"  {read_name:<30}{cells}")

print()
alice_sees_scheduled = matches(scheduled_write, alice_read)
system_sees_alice = matches(interactive_write, system_read)
tenant_sees_all = all(matches(row, tenant_wide) for row in rows.values())
print(
    "  alice's read sees the scheduled run's memory :",
    alice_sees_scheduled,
    "(want False)",
)
print(
    "  system read sees alice's memory              :",
    system_sees_alice,
    "(want False)",
)
print(
    "  tenant-wide read sees everything             :",
    tenant_sees_all,
    "(why it is forbidden)",
)
print()
print("  A caller needing both does two reads and merges. Never one tenant-wide read.")

# --- 4. The two failure modes the convention has to survive ----------------------------

print()
print("4. Failure modes")
print("-" * 78)
try:
    Scope(tenant_id="")
    print("  FAIL: an unscoped tenant was accepted")
except ValueError as exc:
    print("  Blank tenant refused:", str(exc).split(".")[0])
print("  G1: a run whose tenant_id is None makes _managers.py return without touching")
print("      memory, silently. The startup check must fail loudly instead.")
print("  Agents that write memory need a stable id or name, or agent_id is a fresh")
print("      uuid4-derived partition on every process start.")

print()
print("=" * 78)
ok = not alice_sees_scheduled and not system_sees_alice and tenant_sees_all
print("Convention holds." if ok else "Convention does NOT hold -- see above.")
