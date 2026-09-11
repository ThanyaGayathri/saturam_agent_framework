"""K12+K13 as the product runs them -- ``egpt-retail suggest``, live, then cleaned up.

The architecture review's sequence: connect, build the semantic layer, *then immediately
suggest*. ``harvest`` builds the layer; this exercises the "then": the entrypoint gathers the
harvest, the ontology and K10's domain prose, runs both suggestion agents through R8's routing,
and files what survives assembly as K7 proposals with an adopt action. Nothing here writes to
the graph or the registry -- the proposals table is the only thing touched, and every row this
files is removed again at exit.

Two runs. A dry run first, so the report is visible without a side effect; then a filing run,
with the rows checked in the proposals table by proposer, the identical proposals re-filed to
show K7's no-op, and every row deleted at exit. The shared dev
store rule: a cookbook removes everything it writes, and removal is registered with atexit
because a failing assertion exits before the bottom of the file.

Needs Azure credentials, the ontology from K3, the prose from K2 (load-knowledge), the harvest
from K4/K5, Neo4j and the local embedder:

    set -a; . ./azure_key.env; set +a
    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K13_suggest_on_connect.py
"""

import atexit
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt.metrics import ProviderMode  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.kpi import KpiDefinition  # noqa: E402
from egpt_retail.entrypoints.suggest import suggest  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.semantic.proposals import PROPOSAL_SCHEMA  # noqa: E402
from egpt_retail.suggestions import KPI_SUGGESTION_ID, USE_CASE_SUGGESTION_ID  # noqa: E402

if not os.environ.get("AZURE_OPENAI_API_KEY"):
    raise SystemExit(
        "AZURE_OPENAI_API_KEY is not set. Source azure_key.env and re-run."
    )

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
PASSWORD = os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail")
PROPOSERS = (f"{KPI_SUGGESTION_ID}@1.0.0", f"{USE_CASE_SUGGESTION_ID}@1.0.0")

settings = RetailSettings(
    retail_customer_slug="middleby",
    retail_db_url=CONTROL_PLANE.replace("postgresql://", "postgresql+psycopg://", 1),
    retail_neo4j_uri=NEO4J,
    retail_neo4j_password=PASSWORD,
    retail_provider_mode=ProviderMode.LOCAL,
    # The corpus K02 loaded for cookbooks, the table K10 reads. A deployment's
    # `load-knowledge` fills the settings default instead.
    retail_knowledge_table=os.environ.get(
        "EGPT_RETAIL_KNOWLEDGE_TABLE", "egpt_retail_domain_prose_bge"
    ),
    retail_azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
    retail_azure_small_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
)

print("K13 -- suggest on connect (the entrypoint, live)")
print("=" * 78)

# K13 sits downstream of K11: with an empty registry the use case agent correctly refuses.
# Seed the same four KPIs K13_use_case_suggestion.py seeds, and remove them at exit.
# autocommit, because `suggest()` opens its OWN connection to the same database. The first
# run of this cookbook seeded the registry inside an open transaction and then waited
# forever on that connection's locks from the second -- forty minutes, 1.5s of CPU.
control_plane = psycopg.connect(CONTROL_PLANE)
control_plane.autocommit = True
registry = KpiRegistryStore(control_plane)
registry.ensure_schema()
SEEDED = [
    KpiDefinition(
        identifier="k13_deal__open_value",
        name="Open pipeline value",
        description="Total value of deals not yet in a closed stage.",
        sql="SELECT sum(amount) FROM crm_deal WHERE stage NOT LIKE 'closed%'",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal"}),
        concept="QuoteRequest",
    ),
    KpiDefinition(
        identifier="k13_deal__stage_ageing_days",
        name="Days in current stage",
        description="How long each deal has sat in the stage it is in.",
        sql=(
            "SELECT d.deal_id, max(h.days_in_stage) FROM crm_deal d "
            "JOIN crm_deal_stage_history h ON h.deal_id = d.deal_id GROUP BY d.deal_id"
        ),
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal", "crm_deal_stage_history"}),
        concept="QuoteRequest",
    ),
    KpiDefinition(
        identifier="k13_session__rage_click_rate",
        name="Rage click rate",
        description="Share of replayed sessions containing a rage click.",
        sql=(
            "SELECT avg(CASE WHEN rage_click_count > 0 THEN 1.0 ELSE 0 END) "
            "FROM session_replay_session"
        ),
        grain="overall",
        business_area="marketing",
        source_tables=frozenset({"session_replay_session"}),
        concept="WebSession",
    ),
    KpiDefinition(
        identifier="k13_contact__reachable",
        name="Reachable contacts",
        description="Contacts holding an email address.",
        sql="SELECT count(*) FROM crm_contact WHERE email IS NOT NULL",
        grain="overall",
        business_area="customer",
        source_tables=frozenset({"crm_contact"}),
        concept=None,
    ),
]
registry.save_many(SEEDED)


def _count_filed() -> int:
    with control_plane.cursor() as cursor:
        cursor.execute(
            f"SELECT count(*) FROM {PROPOSAL_SCHEMA}.proposal WHERE proposer = ANY(%s)",
            (list(PROPOSERS),),
        )
        return int(cursor.fetchone()[0])


@atexit.register
def _cleanup() -> None:
    if control_plane.closed:
        return
    with control_plane.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {PROPOSAL_SCHEMA}.proposal WHERE proposer = ANY(%s)",
            (list(PROPOSERS),),
        )
    for kpi in SEEDED:
        registry.delete(kpi.identifier)
    control_plane.close()


before = _count_filed()

# --- 1. Dry run: the report, no side effect ----------------------------------------------

print()
print("1. egpt-retail suggest --dry-run")
print("-" * 78)
dry = suggest(settings, dry_run=True)
for line in dry.lines():
    print(f"  {line}")

# --- 2. Filing run: proposals land in K7's table ------------------------------------------

print()
print("2. egpt-retail suggest (files proposals)")
print("-" * 78)
filed = suggest(settings)
for line in filed.lines():
    print(f"  {line}")
after = _count_filed()
print(f"  rows in {PROPOSAL_SCHEMA}.proposal by these proposers: {before} -> {after}")

# --- 3. Idempotence: what K7 promises, and what it does not ---------------------------------

print()
print("3. Filing the same proposals again")
print("-" * 78)
same_again = filed.kpis.as_proposals() + filed.use_cases.as_proposals()
refiled = filed_store_new = None
with psycopg.connect(CONTROL_PLANE) as connection:
    from egpt_retail.semantic.proposals import ProposalStore

    connection.autocommit = True
    refiled = ProposalStore(connection).propose_many(same_again)
print(f"  identical proposals offered again: {len(same_again)}; new: {refiled}")
print(
    "  K7 keys a proposal by its content, so the same assembled output is a no-op. What"
)
print(
    "  it does NOT promise is that a second model call produces the same content -- the"
)
print(
    "  first run of this cookbook re-ran the agents and 15 rephrased proposals landed as"
)
print(
    "  new rows. A nightly pass therefore needs identity by kind and target for the two"
)
print("  suggestion kinds, not by payload. Filed as K-13; not papered over here.")

# --- 4. Verdict ---------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if dry.proposals_new != 0:
    failures.append("the dry run filed proposals")
if not dry.kpis.suggestions and not dry.use_cases.suggestions:
    failures.append("the agents proposed nothing against a harvest with tables")
if filed.proposals_new == 0 and filed.proposals_offered > 0:
    failures.append(
        "the filing run offered proposals and filed none. Either they were already "
        "present from an earlier run this cookbook failed to clean up, or propose_many "
        "is refusing them"
    )
if after - before != filed.proposals_new:
    failures.append(
        f"the table gained {after - before} rows but the run reports {filed.proposals_new}"
    )
if refiled != 0:
    failures.append(
        f"re-filing the identical proposals created {refiled} new row(s); K7's content id "
        "should have made it a no-op"
    )
if not filed.context.prose:
    failures.append(
        "no domain prose reached the agents. The review names it as an input; if the "
        "corpus is not loaded, run egpt-retail load-knowledge first"
    )

if failures:
    print("Suggest-on-connect does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Suggest-on-connect holds. Both agents ran through R8's routing with the harvest, the"
)
print(
    "ontology and K10's prose; every surviving suggestion was filed as a K7 proposal for a"
)
print("person to adopt; the dry run filed nothing; and the second pass was a no-op.")
