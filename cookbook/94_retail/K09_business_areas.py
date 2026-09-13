"""K9 -- which business areas a deployment supports. A graph query, never a judgment.

The package's scenario, from the delivery plan: with no source connected, list areas; connect
the sample source, confirm its mappings, list again; then run the detection twice on unchanged
data.

Evidence: areas exist before any source is connected -- which is what lets a customer
configure them up front; after connecting, exactly the areas reachable from confirmed mappings
are supported; and two runs agree exactly.

**The two facts this package keeps apart.** An area *exists* because the shipped ontology has
concepts in it -- that needs no customer data at all. An area is *supported* because some
concept in it has a table mapped to it, and only an accepted proposal writes that edge (D10).
A concept nobody mapped contributes nothing however well described it is.

**This cookbook temporarily removes the graph's INSTANTIATES edges** to reach the
no-source-connected state honestly, and restores them in a `finally`. Passing an empty
dictionary to the pure function would have demonstrated the arithmetic and not the query.

Section 7 asks the same question over HTTP (issue #225, B3): `GET /business-areas` on the
real router must name exactly the areas the detection names, every area carrying its label
and subject areas; `PUT /business-areas` adds an overlay area (against a scratch copy of the
overlay, never the tracked file), refuses a redefinition of a core area with 409 (G10), and
an empty `PUT` clears the overlay's own contribution.

Needs the ontology from K3, the harvest from K4/K5, and Neo4j:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K09_business_areas.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt_retail.api.routers.business_areas import (  # noqa: E402
    router as business_areas_router,
)
from egpt_retail.contracts.semantic import Concept  # noqa: E402
from egpt_retail.contracts.vocabulary import load_vocabulary_set  # noqa: E402
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology  # noqa: E402
from egpt_retail.semantic.areas import (  # noqa: E402
    areas_in_ontology,
    detect_areas,
    evidence_from_mappings,
    summary,
    supported_areas,
)
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.proposals import (  # noqa: E402
    Proposal,
    ProposalKind,
    ProposalStore,
)
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail"))
OVERLAY = Path("product/egpt_retail/ontology/overlays/middleby.json")
PROPOSER = "k09_cookbook"

print("K9 -- business area detection")
print("=" * 78)

ontology = load_ontology(
    ARTIFACT_PATH, overlay_path=OVERLAY if OVERLAY.exists() else None
)

# --- 1. Areas exist before anything is connected ---------------------------------------------

print()
print("1. Areas exist before a single source is connected")
print("-" * 78)
declared = areas_in_ontology(ontology)
print(f"  areas in the shipped ontology: {len(declared)}")
print(f"  {', '.join(declared)}")
print("  No database was consulted to produce that list. This is what lets a customer")
print("  configure the areas their business has before a connector has ever run.")

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
harvest = harvest_schema(warehouse, "silver", source="egpt_warehouse", sample=False)
warehouse.close()

driver = GraphDatabase.driver(NEO4J, auth=AUTH)
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()
graph.write_harvest(harvest, snapshot_version="k09-demo.1")

control_plane = psycopg.connect(CONTROL_PLANE)
proposals = ProposalStore(control_plane)
proposals.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.proposal WHERE proposer = %s", (PROPOSER,))
control_plane.commit()

# Everything the graph already holds, so this cookbook can put it back.
with driver.session() as session:
    saved_edges = session.run(
        "MATCH (t:SemanticTable)-[:INSTANTIATES]->(c:SemanticConcept) "
        "RETURN t.schema AS schema, t.name AS table, c.name AS concept"
    ).data()

try:
    # --- 2. No source connected ---------------------------------------------------------------

    with driver.session() as session:
        session.run(
            "MATCH (:SemanticTable)-[r:INSTANTIATES]->(:SemanticConcept) DELETE r"
        )

    unconnected = detect_areas(
        ontology, evidence_from_mappings(ontology, graph.mapped_concepts())
    )
    print()
    print("2. With no source connected")
    print("-" * 78)
    print(f"  areas listed: {len(unconnected)}")
    print(f"  supported:    {list(supported_areas(unconnected))}")
    print(f"  {unconnected[0].name}: {unconnected[0].reason}")

    # --- 3. Connect a source: propose, accept, apply -------------------------------------------

    mappings = [
        ("silver.crm_deal", "QuoteRequest"),
        ("silver.crm_contact", "Contact"),
        ("silver.analytics_event", "WebSession"),
    ]
    # The concepts have to exist before a mapping can attach to one -- and they enter the
    # graph the same way, through an accepted proposal. Written here directly because K8's
    # enrichment is a separate package; what matters for K9 is that the INSTANTIATES edge
    # arrives only through review.
    for _, concept_name in mappings:
        concept = next((c for c in ontology.concepts if c.name == concept_name), None)
        if concept is None:
            continue
        # No business_area written here on purpose: the area is the ontology's fact, and K9
        # reads it from there. A node whose area an agent left blank still counts.
        graph.write_concept(Concept(name=concept.name, description=concept.description))

    drafted = [
        Proposal(
            kind=ProposalKind.MAPPING,
            target=table,
            payload={"table": table, "concept": concept},
            proposer=PROPOSER,
        )
        for table, concept in mappings
    ]
    proposals.propose_many(drafted)

    before_review = detect_areas(
        ontology, evidence_from_mappings(ontology, graph.mapped_concepts())
    )
    print()
    print("3. Proposed but not yet reviewed")
    print("-" * 78)
    print(
        f"  proposals pending: {len([p for p in proposals.pending() if p.proposer == PROPOSER])}"
    )
    print(f"  supported: {list(supported_areas(before_review))}")
    print(
        "  A proposal is not a connection. Nothing is supported until someone accepts."
    )

    outcome = proposals.accept([p.identifier for p in drafted], "kiruthiga")
    applied = proposals.apply_accepted(graph)

    connected = detect_areas(
        ontology, evidence_from_mappings(ontology, graph.mapped_concepts())
    )
    print()
    print("4. Accepted and applied")
    print("-" * 78)
    print(f"  accepted: {outcome}; applied to the graph: {len(applied.applied)}")
    for status in connected:
        if status.supported:
            print(f"    {status.name:<14} {status.reason}")
    print(f"  supported: {list(supported_areas(connected))}")

    # --- 5. Two runs on unchanged data --------------------------------------------------------

    again = detect_areas(
        ontology, evidence_from_mappings(ontology, graph.mapped_concepts())
    )
    identical = again == connected
    print()
    print("5. Run it twice on unchanged data")
    print("-" * 78)
    print(f"  identical, field for field: {identical}")
    print(f"  summary: {summary(connected)}")

finally:
    # Put the graph back exactly as it was, whatever happened above.
    with driver.session() as session:
        session.run(
            "MATCH (:SemanticTable)-[r:INSTANTIATES]->(:SemanticConcept) DELETE r"
        )
        for edge in saved_edges:
            session.run(
                "MATCH (t:SemanticTable {schema: $schema, name: $table}) "
                "MATCH (c:SemanticConcept {name: $concept}) "
                "MERGE (t)-[:INSTANTIATES]->(c)",
                schema=edge["schema"],
                table=edge["table"],
                concept=edge["concept"],
            )
        session.run(
            "MATCH (c:SemanticConcept) WHERE c.name IN ['Contact'] "
            "AND NOT (c)<-[:INSTANTIATES]-() AND NOT ()-[:AUTHORITATIVE_SOURCE]->(c) "
            "DETACH DELETE c"
        )
        restored_edges = session.run(
            "MATCH (t:SemanticTable)-[:INSTANTIATES]->(c:SemanticConcept) "
            "RETURN count(*) AS n"
        ).single()["n"]
    with control_plane.cursor() as cursor:
        cursor.execute("DELETE FROM semantic.proposal WHERE proposer = %s", (PROPOSER,))
    control_plane.commit()
    control_plane.close()

print()
print("6. Restored")
print("-" * 78)
print(f"  INSTANTIATES edges before: {len(saved_edges)}, after: {restored_edges}")

# --- 7. The same answer over HTTP: GET/PUT /business-areas (issue #225, B3) -----------------

print()
print(
    "7. Over HTTP: GET /business-areas agrees with the detection; PUT applies an overlay"
)
print("-" * 78)

# The router reads RetailSettings. Point it at this cookbook's graph, and at a scratch copy
# of the overlay, so a PUT here never edits the tracked middleby.json.
os.environ.setdefault("EGPT_RETAIL_NEO4J_URI", NEO4J)
os.environ.setdefault("EGPT_RETAIL_NEO4J_PASSWORD", AUTH[1])
scratch_dir = Path(tempfile.mkdtemp(prefix="k09-overlay-"))
scratch_overlay = scratch_dir / "overlay.json"
original_overlay = json.loads(OVERLAY.read_text()) if OVERLAY.exists() else {}
if OVERLAY.exists():
    scratch_overlay.write_text(OVERLAY.read_text())
os.environ["EGPT_RETAIL_ONTOLOGY_OVERLAY"] = str(scratch_overlay)

http_app = FastAPI()
http_app.include_router(business_areas_router)
client = TestClient(http_app)
http_failures = []

listing = client.get("/business-areas")
body = listing.json()
print(
    f"  GET /business-areas -> {listing.status_code}, {len(body.get('areas', []))} "
    f"area(s), vocabulary {body.get('vocabulary_version')}"
)
if listing.status_code != 200:
    http_failures.append(f"GET /business-areas -> {listing.status_code}: {body}")
else:
    over_http = {a["id"] for a in body["areas"] if a["supported"]}
    direct = set(
        supported_areas(
            detect_areas(
                ontology, evidence_from_mappings(ontology, graph.mapped_concepts())
            )
        )
    )
    print(
        f"  supported over HTTP: {sorted(over_http) or '-'}; "
        f"direct detection: {sorted(direct) or '-'}"
    )
    if over_http != direct:
        http_failures.append(
            f"GET reports {sorted(over_http)} supported; the detection says {sorted(direct)}"
        )
    vocabulary_ids = load_vocabulary_set()["business_area"].values
    listed = {a["id"] for a in body["areas"]}
    if not vocabulary_ids <= listed:
        http_failures.append(
            f"GET omits vocabulary areas {sorted(vocabulary_ids - listed)}"
        )
    if any(not a["label"] for a in body["areas"]):
        http_failures.append("an area came back without a label")
    print(
        "  every area carries a label and its subject areas, e.g. "
        + next(
            f"{a['id']}: {a['label']} -> {a['subject_areas']}"
            for a in body["areas"]
            if a["id"] == "inventory"
        )
    )

added = client.put(
    "/business-areas",
    json={
        "areas": [
            {
                "id": "store_ops",
                "label": "Store Ops",
                "subject_areas": ["Labor & Staffing", "Foot Traffic"],
                "description": "In-store operations.",
            }
        ]
    },
)
print(f"  PUT adding store_ops -> {added.status_code}")
if added.status_code != 200:
    http_failures.append(f"PUT /business-areas -> {added.status_code}: {added.json()}")
else:
    store_ops = next((a for a in added.json()["areas"] if a["id"] == "store_ops"), None)
    print(
        f"  store_ops came back: origin={store_ops and store_ops['origin']}, "
        f"supported={store_ops and store_ops['supported']} (no concept, so no mapping)"
    )
    if not store_ops or store_ops["origin"] != "overlay" or store_ops["supported"]:
        http_failures.append(
            "the added area did not come back as an unsupported overlay area"
        )
    written = json.loads(scratch_overlay.read_text())
    if len(written.get("concepts", [])) != len(original_overlay.get("concepts", [])):
        http_failures.append("PUT disturbed the overlay's own concepts")

conflict = client.put(
    "/business-areas",
    json={
        "areas": [{"id": "sales", "label": "Sales", "description": "Something else."}]
    },
)
print(f"  PUT redefining core 'sales' -> {conflict.status_code} (G10: expected 409)")
if conflict.status_code != 409:
    http_failures.append(
        f"redefining a core area answered {conflict.status_code}, not 409: {conflict.json()}"
    )

cleared = client.put("/business-areas", json={"areas": []})
still_there = any(a["id"] == "store_ops" for a in cleared.json().get("areas", []))
print(f"  PUT [] -> {cleared.status_code}; store_ops gone: {not still_there}")
if cleared.status_code != 200 or still_there:
    http_failures.append("PUT [] did not clear the overlay's own area")
shutil.rmtree(scratch_dir, ignore_errors=True)

driver.close()

# --- 8. Verdict --------------------------------------------------------------------------------

print()
print("=" * 78)
failures = list(http_failures)
if len(declared) < 5:
    failures.append(
        f"only {len(declared)} areas in the ontology; expected the shipped set"
    )
if len(unconnected) != len(declared):
    failures.append(
        "the unconnected listing dropped areas rather than marking them unsupported"
    )
if supported_areas(unconnected):
    failures.append(
        f"areas were supported with no source connected: {supported_areas(unconnected)}"
    )
if supported_areas(before_review):
    failures.append(
        f"a proposal alone made an area supported: {supported_areas(before_review)}. "
        "Nothing may be supported until a person accepts (D10)."
    )
expected = {"sales", "customer", "marketing"}
if set(supported_areas(connected)) != expected:
    failures.append(
        f"expected exactly {sorted(expected)} supported after connecting, got "
        f"{list(supported_areas(connected))}"
    )
if not identical:
    failures.append(
        "two runs over unchanged data disagreed; the detection is not deterministic"
    )
if any(s.supported and not s.tables for s in connected):
    failures.append("an area is supported with no table named as evidence")
if any(not s.supported and s.tables for s in connected):
    failures.append("an unsupported area carries evidence, which cannot both be true")
if restored_edges != len(saved_edges):
    failures.append(
        f"the cookbook did not restore the graph: {len(saved_edges)} edges before, "
        f"{restored_edges} after"
    )

if failures:
    print("K9 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Detection holds. Areas exist before any source is connected; a proposal alone supports"
)
print(
    "nothing; after acceptance exactly the areas reachable from confirmed mappings are"
)
print("supported, each naming its evidence; and two runs agree field for field.")
