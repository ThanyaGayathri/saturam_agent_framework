"""
Tenant-Scoped Hydration
=======================

Demonstrates tenant-scoped, cached hydration of DB-defined components (EGPT-2.5).

Two tenants each store an agent under the same database. Hydrating with a tenant_id
resolves only that tenant's component: a foreign tenant's id behaves exactly like a
missing one. A ComponentConfigCache parked on the Registry collapses repeat reads
(one DB round-trip per config, which is what makes team-member fan-out cheap), and
invalidate() is how a mutation makes the next hydration re-read.

Prerequisite: ./cookbook/scripts/run_pgvector.sh
"""

from egpt.agent.agent import Agent, get_agent_by_id
from egpt.db.base import ComponentType
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.models.openai import OpenAIResponses
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache

# ---------------------------------------------------------------------------
# Create Database Client and Registry
# ---------------------------------------------------------------------------
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

# EgptOS parks a cache on its registry automatically; a programmatic caller parks one
# explicitly. TTL 0 would disable it.
registry = Registry(component_config_cache=ComponentConfigCache(ttl_seconds=30))

# ---------------------------------------------------------------------------
# Create One Agent per Tenant
# ---------------------------------------------------------------------------
ACME_AGENT_ID = "acme-support-agent"
GLOBEX_AGENT_ID = "globex-billing-agent"


def seed_tenant_agents() -> None:
    """Store one agent per tenant. tenant_id is stamped on the rows at write time."""
    db.create_component_with_config(
        component_id=ACME_AGENT_ID,
        component_type=ComponentType.AGENT,
        name="Acme Support Agent",
        config=Agent(
            id=ACME_AGENT_ID,
            name="Acme Support Agent",
            model=OpenAIResponses(id="gpt-5.5"),
            instructions=["You handle support questions for Acme."],
        ).to_dict(),
        stage="published",
        tenant_id="acme",
    )
    db.create_component_with_config(
        component_id=GLOBEX_AGENT_ID,
        component_type=ComponentType.AGENT,
        name="Globex Billing Agent",
        config=Agent(
            id=GLOBEX_AGENT_ID,
            name="Globex Billing Agent",
            model=OpenAIResponses(id="gpt-5.5"),
            instructions=["You handle billing questions for Globex."],
        ).to_dict(),
        stage="published",
        tenant_id="globex",
    )


# ---------------------------------------------------------------------------
# Run Tenant-Scoped Hydration Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if db.get_component(ACME_AGENT_ID) is None:
        seed_tenant_agents()

    # A tenant hydrates its own component.
    acme_agent = get_agent_by_id(
        db=db, id=ACME_AGENT_ID, registry=registry, tenant_id="acme"
    )
    print(f"acme sees its own agent: {acme_agent.name if acme_agent else None}")

    # A foreign tenant's component is indistinguishable from a missing one.
    cross_tenant = get_agent_by_id(
        db=db, id=GLOBEX_AGENT_ID, registry=registry, tenant_id="acme"
    )
    print(f"acme asking for globex's agent gets: {cross_tenant}")

    # The second hydration is served from the cache -- no DB read.
    acme_agent_again = get_agent_by_id(
        db=db, id=ACME_AGENT_ID, registry=registry, tenant_id="acme"
    )
    print(
        f"cached hydration returns: {acme_agent_again.name if acme_agent_again else None}"
    )

    # After a mutation, invalidate so the next hydration re-reads. The components HTTP API
    # does this automatically; programmatic writers rely on the TTL or call it themselves.
    registry.component_config_cache.invalidate(ACME_AGENT_ID)
    print(
        "cache invalidated for the acme agent; next hydration will re-read the database"
    )

    # Run the hydrated agent (requires OPENAI_API_KEY).
    if acme_agent:
        acme_agent.print_response(
            "A customer cannot log in. What should I ask them first?"
        )
