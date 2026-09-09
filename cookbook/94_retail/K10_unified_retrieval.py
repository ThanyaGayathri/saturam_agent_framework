"""K10 -- one question, every store, one ranking (the gap report's finding).

The package's scenario, from the delivery plan: ask a question needing two sources -- a domain
rule plus a table name -- then disable one leg and ask again.

Evidence: the result contains hits from both sources, each labelled with origin and score; and
with a leg disabled the answer visibly degrades, proving the leg was contributing rather than
decorating.

**Why this needed building at all.** Before it, every capability retrieved its own way: memory
through the provider, prose through `Knowledge`, the ontology through its own vector index, and
the semantic layer not at all. Four call sites, four rankings, and no way to ask one question of
all of them -- so a question needing a domain rule *and* a table name got a good answer from
whichever store the caller happened to think of, and no answer from the rest.

**Why fusing them is defensible.** A prose chunk's score is a vector similarity, an ontology
hit's is a Neo4j index score, and the semantic layer's is a name overlap. The three are not on
one scale and never will be. Reciprocal Rank Fusion reads none of them -- it reads each leg's
*position* -- which is exactly why the framework's RRF, until K10 typed to `MemoryRecord` and
unreachable from knowledge, is the right thing to have made reachable rather than replaced.

Needs the prose from K2, the ontology from K3, the semantic layer from K4/K5, and pgvector:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K10_unified_retrieval.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))
os.environ.setdefault("EGPT_RETAIL_PROVIDER_MODE", "local")

from egpt.integrations.knowledge.embedder.sentence_transformer import (  # noqa: E402,F401
    SentenceTransformerEmbedder,
)
from egpt.knowledge.knowledge import Knowledge  # noqa: E402
from egpt.memory.types import MemoryQuery, Scope  # noqa: E402
from egpt.metrics import ProviderMode  # noqa: E402
from egpt.vectordb.pgvector import PgVector  # noqa: E402
from egpt.vectordb.search import SearchType  # noqa: E402
from egpt_retail.knowledge.embedding import resolve_knowledge_embedder  # noqa: E402
from egpt_retail.ontology.store import OntologyStore  # noqa: E402
from egpt_retail.retrieval import retail_unified_read  # noqa: E402
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

DB_URL = os.environ.get(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)
# The table K2's loader actually wrote, embedder suffix and all: the prose is embedded at a
# specific model and width, and a table holding another model's vectors is not interchangeable.
PROSE_TABLE = os.environ.get(
    "EGPT_RETAIL_KNOWLEDGE_TABLE", "egpt_retail_domain_prose_bge"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail"))

# A question no single store can answer. "Quote request" is domain language the prose explains
# and the ontology defines; "which table" is a fact only this customer's semantic layer holds.
QUESTION = "what is a quote request and which table holds deal records"

print("K10 -- unified retrieval")
print("=" * 78)

resolved = resolve_knowledge_embedder(mode=ProviderMode.LOCAL)
knowledge = Knowledge(
    vector_db=PgVector(
        table_name=PROSE_TABLE,
        db_url=DB_URL,
        search_type=SearchType.hybrid,
        embedder=resolved.embedder,
    ),
)
driver = GraphDatabase.driver(NEO4J, auth=AUTH)
ontology = OntologyStore(driver=driver, embedder=resolved.embedder)
semantic = SemanticGraphStore(driver=driver)
scope = Scope(tenant_id="middleby")
query = MemoryQuery(text=QUESTION, limit=5)

# --- 1. Every leg, asked at once -----------------------------------------------------------

everything = retail_unified_read(
    scope, query, knowledge=knowledge, ontology=ontology, semantic=semantic
)

print()
print("1. One question, four legs")
print("-" * 78)
print(f"  question: {QUESTION!r}")
for name, results in everything.legs().items():
    print(f"  {name:<10} {len(results)} hit(s)")
    for result in results[:2]:
        print(f"      {result.content[:66].strip()}")

# --- 2. The fused ranking, each hit labelled ------------------------------------------------

fused = everything.fused(limit=6)
print()
print("2. Fused into one ranking -- by position, never by score")
print("-" * 78)
for rank, result in enumerate(fused, start=1):
    origins = ", ".join(result.metadata["fused_origins"])
    print(f"  {rank}. [{origins}] score={result.score:.5f}")
    print(f"     {result.content[:70].strip()}")

origins_present = {result.label for result in fused}
has_prose = any(r.label == "kb" for r in fused)
has_semantic = any(r.label == "semantic" for r in fused)
print()
print(f"  origins in the ranking: {sorted(origins_present)}")
print(f"  a domain rule from the prose: {has_prose}")
print(f"  a table name from the semantic layer: {has_semantic}")
print("  Neither store could have answered the whole question on its own.")

# --- 3. Disable the semantic leg -------------------------------------------------------------

without_semantic = retail_unified_read(
    scope, query, knowledge=knowledge, ontology=ontology, semantic=None
)
degraded = without_semantic.fused(limit=6)

print()
print("3. The semantic leg disabled")
print("-" * 78)
print(f"  legs asked: {sorted(without_semantic.legs())}")
print(f"  origins in the ranking: {sorted({r.label for r in degraded})}")
print(f"  any table name left: {any(r.label == 'semantic' for r in degraded)}")
print("  The question is now half-answerable: the domain language survives, the table")
print("  name is gone. That is the leg proving it was contributing, not decorating.")

# --- 4. Disable the prose leg ----------------------------------------------------------------

without_prose = retail_unified_read(
    scope, query, knowledge=None, ontology=ontology, semantic=semantic
)
no_prose = without_prose.fused(limit=6)

print()
print("4. The prose leg disabled")
print("-" * 78)
print(f"  origins in the ranking: {sorted({r.label for r in no_prose})}")
print(f"  any prose left: {any(r.label == 'kb' for r in no_prose)}")

# --- 5. A leg that is asked and finds nothing is not a leg that was never asked ---------------

nonsense = retail_unified_read(
    scope,
    MemoryQuery(text="zzzz_no_such_thing_anywhere", limit=5),
    knowledge=knowledge,
    ontology=ontology,
    semantic=semantic,
)
print()
print("5. Asked and found nothing, versus never asked")
print("-" * 78)
print(f"  all four legs present: {sorted(nonsense.legs())}")
print(f"  semantic hits: {len(nonsense.legs()['semantic'])}")
print(f"  legs when semantic is not wired: {sorted(without_semantic.legs())}")
print(
    "  An empty leg is still a leg. A caller can tell 'no match' from 'not configured'."
)

driver.close()

# --- 6. Verdict -------------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if set(everything.legs()) != {"kb", "memory", "ontology", "semantic"}:
    failures.append(f"expected four legs, got {sorted(everything.legs())}")
if not has_prose:
    failures.append("no prose hit reached the fused ranking")
if not has_semantic:
    failures.append("no semantic-layer hit reached the fused ranking")
if len(origins_present) < 2:
    failures.append(
        f"the ranking drew on only {origins_present} -- a unified read that reaches one store "
        "is the thing this package exists to replace"
    )
if any(r.score is None for r in fused):
    failures.append("a fused hit carries no score")
if any(not r.metadata.get("fused_origins") for r in fused):
    failures.append("a fused hit does not name the leg(s) it came from")
if "semantic" in {r.label for r in degraded}:
    failures.append("a disabled leg still contributed hits")
if "semantic" in without_semantic.legs():
    failures.append(
        "an unwired leg appears in legs(); 'no match' and 'not configured' are now "
        "indistinguishable"
    )
if any(r.label == "kb" for r in no_prose):
    failures.append("the prose leg still contributed after being disabled")
if [r.content for r in degraded] == [r.content for r in fused]:
    failures.append(
        "disabling a leg changed nothing about the answer -- the leg was decorating"
    )
if "semantic" not in nonsense.legs():
    failures.append("a leg that matched nothing was dropped rather than reported empty")

if failures:
    print("K10 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Unified retrieval holds. One question reaches every store, the ranking is fused by"
)
print(
    "position rather than by incomparable scores, every hit names the leg it came from,"
)
print("and disabling a leg visibly removes what only that leg could answer.")
