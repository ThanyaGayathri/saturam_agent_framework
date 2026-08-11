"""
Lifecycle Audit Trail
=====================

Demonstrates the append-only audit trail for component definitions (EGPT-2.6).

Walks one agent through its whole lifecycle -- create a draft, edit it, publish it,
publish a second version, roll back -- then reads the history back. Every mutation
appends one row recording who did it, to which version, when, and what changed. The
row is written inside the mutation's own transaction, so a refused change (editing a
published version, deleting a published one) leaves no trace at all.

Attribution comes from the actor argument. Over HTTP the components router fills it
from the authenticated principal; a programmatic caller like this one passes it
explicitly, and omitting it records NULL rather than inventing a name.

Prerequisite: ./cookbook/scripts/run_pgvector.sh
"""

from egpt.agent.agent import Agent
from egpt.db.base import ComponentType
from egpt.db.component_audit import ComponentAuditAction
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.models.openai import OpenAIResponses

# ---------------------------------------------------------------------------
# Create Database Client
# ---------------------------------------------------------------------------
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

AGENT_ID = "audited-support-agent"
TENANT = "acme"


def reset_for_a_repeatable_demo() -> None:
    """Clear anything a previous run of this file left behind.

    Demo hygiene only. The audit trail deliberately survives a hard delete of its component,
    so without this the record count would grow on every run and the output would stop
    matching the narrative. A real deployment never deletes audit rows -- prune by age if the
    table grows, and see docs/component-lifecycle.md.
    """
    db.delete_component(AGENT_ID, hard_delete=True, tenant_id=TENANT)
    audit_table = db._get_table(
        table_type="component_audit", create_table_if_not_found=True
    )
    with db.Session() as sess, sess.begin():
        sess.execute(audit_table.delete().where(audit_table.c.component_id == AGENT_ID))


def agent_config(instruction: str) -> dict:
    return Agent(
        id=AGENT_ID,
        name="Support Agent",
        model=OpenAIResponses(id="gpt-5.5"),
        instructions=[instruction],
    ).to_dict()


# ---------------------------------------------------------------------------
# Walk the Lifecycle
# ---------------------------------------------------------------------------
def walk_the_lifecycle() -> None:
    """Create a draft, edit it, publish it, add a second version, then roll back."""
    db.create_component_with_config(
        component_id=AGENT_ID,
        component_type=ComponentType.AGENT,
        name="Support Agent",
        config=agent_config("You answer support questions."),
        tenant_id=TENANT,
        actor="alice",
        notes="initial draft",
    )

    # Editing a draft is free.
    db.upsert_config(
        AGENT_ID,
        version=1,
        config=agent_config("You answer support questions politely."),
        tenant_id=TENANT,
        actor="alice",
        notes="soften the tone",
    )

    # Publishing has no method of its own: it is an upsert with stage="published",
    # which also points current_version at the version. The audit action is derived
    # from that stage transition, so it records a publish rather than an update.
    db.upsert_config(
        AGENT_ID, version=1, stage="published", tenant_id=TENANT, actor="alice"
    )

    # A second version, published in turn.
    db.upsert_config(
        AGENT_ID,
        config=agent_config("You answer support questions and escalate billing ones."),
        tenant_id=TENANT,
        actor="bob",
    )
    db.upsert_config(
        AGENT_ID, version=2, stage="published", tenant_id=TENANT, actor="bob"
    )

    # Rollback repoints current_version at an already-published version. Nothing is
    # deleted or rewritten -- v2 is still there, it is simply no longer current.
    db.set_current_version(
        AGENT_ID,
        version=1,
        tenant_id=TENANT,
        actor="carol",
        audit_note="regression in v2",
    )


# ---------------------------------------------------------------------------
# Show That Refusals Leave No Trace
# ---------------------------------------------------------------------------
def attempt_refused_changes() -> None:
    """Published versions are immutable, and a refusal is never audited."""
    try:
        db.upsert_config(
            AGENT_ID,
            version=1,
            config=agent_config("rewritten"),
            tenant_id=TENANT,
            actor="mallory",
        )
    except ValueError as exc:
        print(f"  refused: {exc}")

    # v2 is published but no longer current after the rollback. Deleting it used to
    # succeed; it is refused now.
    try:
        db.delete_config(AGENT_ID, version=2, tenant_id=TENANT, actor="mallory")
    except ValueError as exc:
        print(f"  refused: {exc}")


# ---------------------------------------------------------------------------
# Read the History
# ---------------------------------------------------------------------------
def print_history() -> None:
    """Read the trail back, oldest first.

    Ordered by audit_id rather than created_at: the whole walk above lands in the
    same second or two, and only audit_id gives it a total order.
    """
    rows, total = db.list_component_audit(AGENT_ID, tenant_id=TENANT, limit=100)
    print(f"\n{total} audit records")
    for row in reversed(rows):
        version = f"v{row['version']}" if row["version"] is not None else "-"
        actor = row["actor"] or "(unattributed)"
        note = f" note={row['note']!r}" if row["note"] else ""
        details = f" details={row['details']}" if row["details"] else ""
        print(
            f"  {row['audit_id']:>3}  {row['action']:<22} {version:<4} by {actor}{note}{details}"
        )


def print_publishes_only() -> None:
    """The history can be filtered by action or by version."""
    rows, total = db.list_component_audit(
        AGENT_ID, tenant_id=TENANT, action=ComponentAuditAction.CONFIG_PUBLISH.value
    )
    print(f"\n{total} publishes")
    for row in reversed(rows):
        print(
            f"  v{row['version']} by {row['actor']}, replacing {row['details']['previous_current_version']}"
        )


def print_tenant_isolation() -> None:
    """The history is scoped like every other component read: another tenant sees nothing."""
    _, other_total = db.list_component_audit(AGENT_ID, tenant_id="globex")
    print(f"\nAnother tenant's view of the same component: {other_total} records")


# ---------------------------------------------------------------------------
# Run the Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    reset_for_a_repeatable_demo()
    walk_the_lifecycle()
    print("\nAttempting changes that must be refused:")
    attempt_refused_changes()
    print_history()
    print_publishes_only()
    print_tenant_isolation()

    # Cleanup. The audit trail deliberately survives this: the table carries no
    # foreign key to components, so the history outlives the component it describes.
    db.delete_component(AGENT_ID, hard_delete=True, tenant_id=TENANT, actor="carol")
    surviving, total = db.list_component_audit(AGENT_ID, tenant_id=TENANT, limit=100)
    print(f"\nAfter hard-deleting the component, {total} audit records remain")
    print(f"  last recorded action: {surviving[0]['action']}")
