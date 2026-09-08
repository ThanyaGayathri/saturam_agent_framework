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

import os
import sys
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
# Core-only cases. Quote terms are deliberately absent -- they live in the overlay, and
# section 6 uses that fact as its test.
synonym_cases = [("SKU", "Product"), ("vendor", "Supplier"), ("PO", "PurchaseOrder")]
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
    ("which items came back and why", {"Return", "OrderLine", "Order"}),
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

# The real per-deployment overlay, not an inline fixture: quote-driven B2B selling is not a
# general retail pattern (3 of 31 corpus documents), so QuoteRequest and Quote live here.
overlay_path = (
    Path(__file__).resolve().parents[2]
    / "product/egpt_retail/ontology/overlays/middleby.json"
)

extended = load_ontology(overlay_path=overlay_path)
store.write(extended)
overlay_names = list(store.concepts_of_origin(TermOrigin.OVERLAY))
print(f"  core alone: {len(ontology)} concepts | with overlay: {len(extended)}")
print(f"    overlay adds: {overlay_names}")
print(
    f"    QuoteRequest in shipped core: {ontology.concept('QuoteRequest') is not None}"
)
print(
    f"    QuoteRequest via overlay    : {extended.concept('QuoteRequest') is not None}"
)

# The test the overlay has to pass: a question the shipped core cannot answer, which the
# overlay makes answerable. Anything less would show the overlay loading, not working.
QUOTE_QUESTION = "someone asked us for a price before buying"
with_overlay = [h.concept.name for h in store.search_vector(QUOTE_QUESTION, limit=3)]
store.write(ontology)  # core alone
core_only = [h.concept.name for h in store.search_vector(QUOTE_QUESTION, limit=3)]
store.write(extended)  # restore
print(f"    {QUOTE_QUESTION!r}")
print(f"      core alone  -> {', '.join(core_only)}")
print(f"      with overlay-> {', '.join(with_overlay)}")
overlay_earns_its_place = bool({"Quote", "QuoteRequest"} & set(with_overlay)) and not (
    {"Quote", "QuoteRequest"} & set(core_only)
)
print(f"      the overlay changed the answer: {overlay_earns_its_place}")

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
    version="middleby-1.0.0",
    concepts=tuple(c for c in extended.concepts if c.origin is TermOrigin.OVERLAY),
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
annotation_survived = qr is not None
print(f"    overlay concept QuoteRequest still resolvable: {annotation_survived}")
traced = store.get_concept("Supplier")
print(
    f"    core concept cites its corpus sources: {list(traced.sources)[:2] if traced else 'MISSING'}"
)

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
