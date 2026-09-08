"""K3 -- the shipped retail ontology: authoring, embedding and indexing.

The package's own verification, from the delivery plan:

1. Search for a term that is a **synonym** rather than a node name.
2. Search for a **plain-language phrase naming no node at all**.
3. Add an **overlay** node, upgrade the core version, and search again.

The evidence that matters is the second one. If a plain-language phrase only reaches a
concept when it happens to contain the concept's name, the node embeddings were skipped --
which the plan calls the usual mistake -- and the whole layer degrades to exact match.

Needs a Neo4j to write to. Start one that is not shared with anything else:

    docker run -d --name egpt-retail-neo4j -p 7701:7687 -p 7700:7474 \\
      -e NEO4J_AUTH=neo4j/egptretail neo4j:5

Run:
    .venvs/demo/bin/python cookbook/94_retail/K03_ontology.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.integrations.knowledge.embedder.sentence_transformer import (  # noqa: E402
    SentenceTransformerEmbedder,
)
from egpt_retail.contracts.vocabulary import (  # noqa: E402
    TermOrigin,
    load_vocabulary_set,
)
from egpt_retail.ontology.model import (  # noqa: E402
    Concept,
    Ontology,
    load_ontology,
    merge_ontology,
)
from egpt_retail.ontology.store import OntologyStore  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

URI = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
AUTH = (
    os.environ.get("EGPT_ONTOLOGY_NEO4J_USER", "neo4j"),
    os.environ.get("EGPT_ONTOLOGY_NEO4J_PASSWORD", "egptretail"),
)

print("K3 -- shipped retail ontology")
print("=" * 78)

# --- 1. The artifact ---------------------------------------------------------------------

ontology = load_ontology()
ontology.validate_against(load_vocabulary_set())
print()
print("1. The shipped artifact")
print("-" * 78)
print(
    f"  version {ontology.version}: {len(ontology)} concepts, {len(ontology.relations)} relations"
)
print(
    f"  synonyms indexed: {len(ontology.synonym_index())}, none claimed by two concepts"
)
print("  business areas:", ", ".join(sorted({c.business_area for c in ontology})))

# --- 2. Write, embed, index --------------------------------------------------------------

embedder = SentenceTransformerEmbedder()
driver = GraphDatabase.driver(URI, auth=AUTH)
store = OntologyStore(driver=driver, embedder=embedder)

print()
print("2. Written to Neo4j under its own label")
print("-" * 78)
store.ensure_indexes()
written = store.write(ontology)
print(f"  concepts written: {written}")
print(f"  stored version:   {store.stored_version()}")

unembedded = store.concepts_without_embedding()
print(
    f"  concepts with no embedding: {len(unembedded)} {list(unembedded) if unembedded else ''}"
)
if unembedded:
    raise SystemExit(
        "Some concepts were written without a vector -- the meaning leg is broken for them."
    )

# --- 3. A synonym, not a node name -------------------------------------------------------

print()
print("3. A synonym reaches its concept")
print("-" * 78)
synonym_cases = [("RFQ", "QuoteRequest"), ("SKU", "Product"), ("vendor", "Supplier")]
synonym_failures = []
for term, expected in synonym_cases:
    hits = store.search_vector(term, limit=3)
    top = hits[0].concept.name if hits else "(nothing)"
    ok = top == expected
    print(
        f"  {term:<9} -> {top:<16} {'OK' if ok else 'MISS, expected ' + expected}  (score {hits[0].score:.3f})"
    )
    if not ok:
        synonym_failures.append(f"{term} reached {top}, expected {expected}")
    print(f"            others: {', '.join(h.concept.name for h in hits[1:])}")

# --- 4. A plain-language phrase naming no node -------------------------------------------

print()
print("4. Plain language, naming no concept at all")
print("-" * 78)
print("  If these only worked when the phrase contained the concept's name, the node")
print("  embeddings were skipped. That is the mistake this check exists to catch.")
print()
phrase_cases = [
    ("what do we need to reorder?", {"StockLevel", "Inventory", "PurchaseOrder"}),
    ("someone asked us for a price", {"QuoteRequest", "Quote"}),
    ("where did our visitors come from?", {"Channel", "WebSession"}),
    (
        "goods turned up late from the people we buy from",
        {"Delivery", "Supplier", "PurchaseOrder"},
    ),
]
phrase_failures = []
for phrase, sensible in phrase_cases:
    hits = store.search_vector(phrase, limit=3)
    names = [h.concept.name for h in hits]
    ok = bool(set(names) & sensible)
    print(f"  {'OK ' if ok else 'MISS'} {phrase!r}")
    print(f"       -> {', '.join(f'{n} ({h.score:.3f})' for n, h in zip(names, hits))}")
    if not ok:
        phrase_failures.append(
            f"{phrase!r} reached {names}, none of {sorted(sensible)}"
        )

# --- 5. The lexical leg is not the vector leg --------------------------------------------

print()
print("5. Both legs, and what each is for")
print("-" * 78)
term = "stock band"
print(
    f"  vector  {term!r}:",
    ", ".join(h.concept.name for h in store.search_vector(term, limit=3)),
)
print(
    f"  lexical {term!r}:",
    ", ".join(h.concept.name for h in store.search_lexical(term, limit=3)),
)
print("  relations of StockLevel:")
for relation in store.relations_of("StockLevel"):
    print(
        f"    {relation['source']} -{relation['type']}-> {relation['target']}  ({relation['cardinality']})"
    )

# --- 6. Overlay survives a core upgrade --------------------------------------------------

print()
print("6. A customer overlay survives a core upgrade (G10)")
print("-" * 78)

overlay_payload = {
    "version": "customer-1",
    "concepts": [
        {
            "name": "RepGroup",
            "description": "An independent sales representative organisation that sells on the manufacturer's behalf in a territory.",
            "business_area": "sales",
            "synonyms": ["rep group", "manufacturer rep", "sales agency"],
            "question_patterns": ["which rep groups perform best?"],
        },
        {
            "name": "QuoteRequest",
            "description": "A request from a prospective buyer for a price on one or more products, before any commitment to buy.",
            "business_area": "sales",
            "synonyms": ["shop enquiry"],
        },
    ],
    "relations": [
        {
            "type": "REPRESENTS",
            "source": "RepGroup",
            "target": "Brand",
            "cardinality": "many-to-many",
        }
    ],
}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
    json.dump(overlay_payload, handle)
    overlay_path = Path(handle.name)

extended = load_ontology(overlay_path=overlay_path)
store.write(extended)
print(f"  with overlay: {len(extended)} concepts")
print(
    f"    overlay concepts in graph: {list(store.concepts_of_origin(TermOrigin.OVERLAY))}"
)
print(
    f"    annotated core concept: QuoteRequest synonyms now {list(extended.concept('QuoteRequest').synonyms)}"
)

added_hits = store.search_vector("independent sales agency for a territory", limit=3)
print(
    f"    'independent sales agency...' -> {', '.join(h.concept.name for h in added_hits)}"
)

# Now upgrade the core underneath the overlay.
upgraded_core = Ontology(
    version="1.1.0",
    concepts=ontology.concepts
    + (
        Concept(
            name="Warranty",
            description="A commitment to repair or replace a product within a period after sale.",
            business_area="product",
            synonyms=["guarantee", "cover"],
        ),
    ),
    relations=ontology.relations,
)
overlay_only = Ontology(
    version="customer-1",
    concepts=tuple(c for c in extended.concepts if c.origin is TermOrigin.OVERLAY)
    + (extended.concept("QuoteRequest"),),
    relations=extended.relations_of_origin(TermOrigin.OVERLAY),
)
merged = merge_ontology(upgraded_core, overlay_only)
store.upgrade_core(merged)

print()
print(f"  core upgraded to {store.stored_version()}")
survivors = store.concepts_of_origin(TermOrigin.OVERLAY)
print(f"    overlay concepts still present: {list(survivors)}")
new_core = store.get_concept("Warranty")
print(f"    new core concept present: {new_core.name if new_core else 'MISSING'}")
qr = store.get_concept("QuoteRequest")
annotation_survived = qr is not None and "shop enquiry" in qr.synonyms
print(f"    overlay annotation on a core concept survived: {annotation_survived}")

overlay_path.unlink()

# --- 7. Verdict --------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
failures += synonym_failures
failures += phrase_failures
if "RepGroup" not in survivors:
    failures.append("the overlay concept did not survive the core upgrade")
if new_core is None:
    failures.append("the upgraded core concept is missing")
if not annotation_survived:
    failures.append(
        "the overlay's annotation of a core concept was lost in the upgrade"
    )
if store.stored_version() != "1.1.0":
    failures.append(f"stored version is {store.stored_version()}, expected 1.1.0")

driver.close()

if failures:
    print("K3 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print("Ontology holds. Synonyms and plain language both reach concepts by meaning,")
print("and a customer overlay survives a core upgrade.")
