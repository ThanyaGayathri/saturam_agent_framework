"""
Seed, Export and Import Definitions
===================================

Demonstrates portable agent definitions (EGPT-2.8).

Three things happen here. First, a team is built and saved into tenant "acme". Then it is
exported to a bundle -- a self-contained JSON document carrying the team, the agents it
depends on, and the links between them, with connection secrets removed. Finally the bundle
is imported into tenant "globex" under new ids, and the copy is hydrated and inspected.

Two details are worth watching in the output. The exported config has no db_url even though
the saved agents were built against a real Postgres: the bundle names the database by id and
lists what it dropped. And the imported team's members are the prefixed ids, not the
originals -- renaming a component rewrites its links, its config self-id, and the member
references that hydration reads.

The same machinery seeds a tenant from templates:

    ./scripts/seed_agents.py --db-url postgresql+psycopg://ai:ai@localhost:5532/ai --tenant-id acme

Re-running that is safe: an unchanged definition writes nothing at all.

Prerequisite: ./cookbook/scripts/run_pgvector.sh
"""

from egpt.agent.agent import Agent
from egpt.components.io import import_component
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.models.openai import OpenAIResponses
from egpt.registry import Registry
from egpt.registry.io import dump_document, export_component
from egpt.team import Team, get_team_by_id

# ---------------------------------------------------------------------------
# Create Database Client
# ---------------------------------------------------------------------------
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai", id="os-db")

registry = Registry()
registry.add_db(db)

SOURCE_TENANT = "acme"
TARGET_TENANT = "globex"
PREFIX = "globex-"

COMPONENT_IDS = ["support-team", "faq-agent", "research-agent"]


def reset_for_a_repeatable_demo() -> None:
    """Clear anything a previous run left behind.

    Demo hygiene only. Without it the second run would report every component as unchanged,
    which is the correct behaviour but makes the narrative below impossible to follow.
    """
    for component_id in COMPONENT_IDS:
        for full_id, tenant in (
            (component_id, SOURCE_TENANT),
            (PREFIX + component_id, TARGET_TENANT),
        ):
            # Checked first because delete_component logs an error for a missing component,
            # which would fill a first run's output with noise about nothing being wrong.
            if db.get_component(full_id, tenant_id=tenant) is not None:
                db.delete_component(full_id, hard_delete=True, tenant_id=tenant)


# ---------------------------------------------------------------------------
# Create the Definition
# ---------------------------------------------------------------------------
def build_and_save_a_team() -> None:
    """Save a team and its two members into the source tenant."""
    faq_agent = Agent(
        id="faq-agent",
        name="FAQ Agent",
        model=OpenAIResponses(id="gpt-5.5"),
        db=db,
        instructions=["Answer from the FAQ only."],
    )
    research_agent = Agent(
        id="research-agent",
        name="Research Agent",
        model=OpenAIResponses(id="gpt-5.5"),
        db=db,
        instructions=["Find background material and cite it."],
    )
    team = Team(
        id="support-team",
        name="Support Team",
        model=OpenAIResponses(id="gpt-5.5"),
        description="Answers customer questions, researching first when it needs to.",
        members=[faq_agent, research_agent],
        db=db,
    )

    faq_agent.save(db=db, stage="published")
    research_agent.save(db=db, stage="published")
    team.save(db=db, stage="published")

    # Agent.save predates tenant threading (EGPT-2.7 carries that), so the rows land
    # untenanted. Put just these three components in the source tenant so the export below has
    # something to be scoped to -- narrowly, by id, rather than stamping the whole table, which
    # would claim any other component in this database for "acme".
    from sqlalchemy import text

    with db.Session() as sess, sess.begin():
        for table, id_column in (
            (db.components_table_name, "component_id"),
            (db.component_configs_table_name, "component_id"),
            (db.component_links_table_name, "parent_component_id"),
        ):
            sess.execute(
                text(
                    f"UPDATE {db.db_schema}.{table} SET tenant_id = :tenant WHERE {id_column} = ANY(:ids)"
                ),
                {"tenant": SOURCE_TENANT, "ids": COMPONENT_IDS},
            )


# ---------------------------------------------------------------------------
# Run the Round Trip
# ---------------------------------------------------------------------------
def export_the_definition() -> dict:
    """Export the team with everything it depends on."""
    document = export_component(db, "support-team", tenant_id=SOURCE_TENANT)

    print("Exported bundle")
    print("  components:", [entry["component_id"] for entry in document["components"]])
    print("  redacted:  ", document["redacted"] or "nothing")

    team_entry = next(
        e for e in document["components"] if e["component_id"] == "support-team"
    )
    print(
        "  team links:",
        [
            (link["link_kind"], link["child_component_id"])
            for link in team_entry["links"]
        ],
    )

    agent_entry = next(
        e for e in document["components"] if e["component_id"] == "faq-agent"
    )
    print("  the agent's db block, post-redaction:", agent_entry["config"].get("db"))
    print()
    return document


def import_into_another_tenant(document: dict) -> None:
    """Import the bundle into a second tenant under prefixed ids.

    The prefix is not decoration: component_id is a global primary key, so the same corpus
    cannot exist twice without one. Importing without it raises a collision.
    """
    results = import_component(
        db,
        document,
        tenant_id=TARGET_TENANT,
        registry=registry,
        id_prefix=PREFIX,
        actor="onboarding-script",
        audit_note="onboarding globex",
    )

    print("Imported into", TARGET_TENANT)
    for result in results:
        print(f"  {result.action:<12} {result.component_id} v{result.version}")
    print()


def hydrate_the_copy() -> None:
    """Load the imported team and show that its members came across renamed."""
    team = get_team_by_id(
        db, PREFIX + "support-team", registry=registry, tenant_id=TARGET_TENANT
    )

    print("Hydrated", team.name, "from tenant", TARGET_TENANT)
    print("  members:", [member.id for member in team.members])

    # The original is untouched, and neither tenant can see the other's copy.
    original = get_team_by_id(
        db, "support-team", registry=registry, tenant_id=SOURCE_TENANT
    )
    print("  original members:", [member.id for member in original.members])
    print(
        "  the source tenant cannot see the copy:",
        get_team_by_id(
            db, PREFIX + "support-team", registry=registry, tenant_id=SOURCE_TENANT
        ),
    )
    print()


def show_the_bundle_is_a_file(document: dict) -> None:
    """A bundle is just JSON. This is what a template file looks like."""
    text = dump_document(document)
    print("The bundle serializes to", len(text), "bytes of canonical JSON")
    print("First lines:")
    for line in text.splitlines()[:6]:
        print("   ", line)


if __name__ == "__main__":
    reset_for_a_repeatable_demo()
    build_and_save_a_team()
    bundle = export_the_definition()
    import_into_another_tenant(bundle)
    hydrate_the_copy()
    show_the_bundle_is_a_file(bundle)
