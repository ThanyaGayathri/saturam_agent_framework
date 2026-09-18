"""AZ-C -- K2's domain prose through a backend-selected vector store.

The plan's Verify scenario (``docs/plan/azure-migration-plan.md``, AZ-C): ingest a document
with known headings, search for a phrase sitting in the middle of one section, and confirm
the chunk boundaries fall on headings and the correct section comes back -- first on pgvector,
which is the baseline, then on Azure AI Search, diffing the two result sets.

Two sections, one scenario. Section 1 runs the pgvector path against compose Postgres and
records its result set. Section 2 runs the identical scenario through the ``azure_ai_search``
backend when ``EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT`` (and a key, or ambient identity) is
set, and diffs the two; with no service configured it says so and skips -- the same boundary
AZ-B recorded in ``TEST_LOG.md``. Chunking is backend-independent, so the heading-boundary
checks are asserted regardless; the pgvector result set is the baseline the Azure leg is
compared to. Exit status is non-zero on any failed check.

Both legs build the store through ``egpt_retail.knowledge.vector.build_knowledge_vector_db``,
the same function ``load-knowledge`` and ``suggest`` use -- this cookbook exercises the
product's wiring, not a private copy of it. Dimension and scoring are AZ-B's decisions
(``AZ_B_dimension_and_scoring_decisions.md``), inherited through the embedder and
``SearchType.hybrid``; nothing here restates either.

Needs compose Postgres (``docker compose -f deploy/compose.yaml up -d postgres``) and the
local embedder (``sentence-transformers``, downloads ``BAAI/bge-base-en-v1.5`` on first use).

Run:
    .venvs/demo/bin/python cookbook/95_azure_migration/AZ_C_knowledge_prose.py
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))
# A laptop has no Azure embedding deployment; the production default is frontier.
os.environ.setdefault("EGPT_RETAIL_PROVIDER_MODE", "local")

import psycopg  # noqa: E402
from egpt.integrations.knowledge.reader.markdown_reader import (
    MarkdownReader,  # noqa: E402
)
from egpt.knowledge.knowledge import Knowledge  # noqa: E402
from egpt.metrics import ProviderMode  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.knowledge.embedding import resolve_knowledge_embedder  # noqa: E402
from egpt_retail.knowledge.prose import (  # noqa: E402
    build_chunker,
    chunking_evidence,
    ingest_corpus,
    read_corpus,
    starts_at_heading,
)
from egpt_retail.knowledge.vector import build_knowledge_vector_db  # noqa: E402

CORPUS = Path(__file__).resolve().parents[2] / "product/egpt_retail/knowledge/corpus"
#: One document with known headings -- the same sample K02 measures its chunking on.
DOCUMENT = "08-inventory-management-and-demand-forecasting.md"
DB_URL = os.environ.get(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)
# Its own table and index, not the deployment's (K02's rule: two writers of one table double
# the chunk count without either being wrong).
TABLE = os.environ.get("EGPT_RETAIL_COOKBOOK_TABLE", "egpt_retail_prose_az_c")
INDEX = os.environ.get("EGPT_RETAIL_COOKBOOK_INDEX", "egpt-retail-prose-az-c")
AZURE_ENDPOINT = os.environ.get("EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT")
AZURE_KEY = os.environ.get("EGPT_RETAIL_KNOWLEDGE_SEARCH_API_KEY")
TOP = 3

#: Phrases from the *middle* of a section -- never its heading, never its first line -- and
#: the heading the whole section should come back under.
QUERIES = [
    (
        "four primary causes of the bullwhip effect identified by Hau Lee",
        "### 8.1 Definition and Mechanics",
    ),
    (
        "sharing daily store-level POS data with suppliers through Retail Link",
        "### 8.3 Bullwhip Mitigation Strategies",
    ),
]

failures: List[str] = []


def check(condition: bool, message: str) -> None:
    print(f"  [{'ok' if condition else 'FAIL'}] {message}")
    if not condition:
        failures.append(message)


def run_scenario(
    label: str, settings: RetailSettings, embedder: Any
) -> Dict[str, List[Dict[str, Any]]]:
    """Ingest the document and run every query; return the result set per query.

    The same code for both backends -- that is the point. The only thing that differs
    between the two calls is ``settings.retail_knowledge_backend``.
    """
    documents = [d for d in read_corpus(CORPUS) if d.path.name == DOCUMENT]
    if len(documents) != 1:
        raise SystemExit(f"{DOCUMENT} is not in {CORPUS}")

    vector_db = build_knowledge_vector_db(settings, embedder)
    # Idempotent: the store is this script's own; a second run must not double the rows
    # (it did -- 84 stored for 42 chunks -- before this line existed).
    if vector_db.exists():
        vector_db.drop()
    print(f"  vector db: {type(vector_db).__name__}")
    knowledge = Knowledge(vector_db=vector_db)
    reader = MarkdownReader(chunking_strategy=build_chunker())
    report = ingest_corpus(knowledge, documents, reader=reader)
    print(f"  {report}")

    results: Dict[str, List[Dict[str, Any]]] = {}
    for query, expected_heading in QUERIES:
        hits = knowledge.search(query, max_results=TOP)
        rows = []
        for hit in hits:
            first_line = hit.content.splitlines()[0] if hit.content else ""
            rows.append(
                {
                    "heading": first_line.strip(),
                    "document": hit.meta_data.get("document"),
                    "origin": hit.meta_data.get("origin"),
                    "score": hit.meta_data.get("similarity_score"),
                    "starts_at_heading": starts_at_heading(hit.content),
                }
            )
        results[query] = rows
        print(f"  {query!r}")
        for rank, row in enumerate(rows, 1):
            score = row["score"]
            shown = f"{score:.4f}" if isinstance(score, float) else str(score)
            print(f"      {rank}. {row['heading'][:60]!r}  score={shown}")
        check(bool(rows), f"{label}: {query!r} returned something")
        if rows:
            check(
                all(r["starts_at_heading"] for r in rows),
                f"{label}: every returned chunk begins at its heading",
            )
            check(
                rows[0]["heading"] == expected_heading,
                f"{label}: top result is {expected_heading!r} (got {rows[0]['heading']!r})",
            )
    return results


print("AZ-C -- knowledge prose through a backend-selected vector db")
print("=" * 78)

# --- 0. Chunking is backend-independent, so measure it once, before either store ----------

print()
print("0. Chunk boundaries fall on headings (no backend involved)")
print("-" * 78)
sample = (CORPUS / DOCUMENT).read_text()
rows = chunking_evidence(sample, name=Path(DOCUMENT).stem)
chosen = rows[2]
print(
    f"  {DOCUMENT}: {chosen['chunks']} chunks under the chosen setting, "
    f"{chosen['start_at_heading']} start at a heading, {chosen['shells']} heading-only shells"
)
check(
    chosen["start_at_heading"] == chosen["chunks"],
    "every chunk begins at a heading under the chosen setting",
)
for query, expected_heading in QUERIES:
    check(
        expected_heading in sample and query.split()[0] not in expected_heading,
        f"expected heading {expected_heading!r} exists and the query is not its title",
    )

resolved = resolve_knowledge_embedder(mode=ProviderMode.LOCAL)
print(f"  embedder: {resolved}")

# --- 1. pgvector, the baseline ------------------------------------------------------------

print()
print("1. pgvector (compose Postgres) -- the baseline")
print("-" * 78)
pg_settings = RetailSettings(
    retail_knowledge_backend="pgvector",
    retail_knowledge_table=TABLE,
    retail_db_url=DB_URL,
    retail_provider_mode=ProviderMode.LOCAL,
)
baseline = run_scenario("pgvector", pg_settings, resolved.embedder)

raw_url = DB_URL.replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(raw_url) as connection:
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT content FROM ai."{TABLE}"')
        stored = [row[0] for row in cursor.fetchall()]
at_heading = sum(1 for content in stored if starts_at_heading(content))
print(f"  stored in {TABLE}: {len(stored)} chunks, {at_heading} begin at a heading")
check(at_heading == len(stored), "pgvector: every stored chunk begins at a heading")
check(
    len(stored) == chosen["chunks"] - chosen["shells"],
    "pgvector: stored chunk count is the heading split minus the dropped shells",
)

# --- 2. Azure AI Search, the migration target ---------------------------------------------

print()
print("2. azure_ai_search -- the same scenario, diffed against the baseline")
print("-" * 78)
azure_results: Optional[Dict[str, List[Dict[str, Any]]]] = None
if not AZURE_ENDPOINT:
    print(
        "  SKIPPED: EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT is not set. No Azure AI Search"
    )
    print(
        "  service is reachable from this environment (no Azure CLI, no subscription --"
    )
    print(
        "  the same boundary AZ-B recorded in TEST_LOG.md), so the Azure leg cannot run"
    )
    print("  and this section proves nothing about it. Set the endpoint and")
    print("  EGPT_RETAIL_KNOWLEDGE_SEARCH_API_KEY to run it.")
else:
    az_settings = RetailSettings(
        retail_knowledge_backend="azure_ai_search",
        retail_knowledge_search_index=INDEX,
        retail_knowledge_search_endpoint=AZURE_ENDPOINT,
        retail_knowledge_search_api_key=AZURE_KEY,
        retail_provider_mode=ProviderMode.LOCAL,
    )
    azure_results = run_scenario("azure_ai_search", az_settings, resolved.embedder)

    print()
    print("  diff against the pgvector baseline (headings, in rank order):")
    for query, _ in QUERIES:
        pg_headings = [r["heading"] for r in baseline[query]]
        az_headings = [r["heading"] for r in azure_results[query]]
        same_top = (
            bool(pg_headings) and bool(az_headings) and pg_headings[0] == az_headings[0]
        )
        print(f"    {query!r}")
        print(f"      pgvector:        {pg_headings}")
        print(f"      azure_ai_search: {az_headings}")
        # The top section must agree: that is the correctness property. Ranks below it and
        # the absolute scores may differ -- pgvector blends a weighted vector/text score,
        # Azure AI Search fuses by reciprocal rank (AZ-B decision 2) -- and that difference
        # is expected, not a bug, so it is printed rather than asserted.
        check(
            same_top, f"azure_ai_search: top section agrees with pgvector for {query!r}"
        )
        if pg_headings != az_headings:
            print(
                "      (lower ranks differ: expected under RRF versus the weighted blend)"
            )

# --- 3. Verdict ---------------------------------------------------------------------------

print()
print("=" * 78)
if failures:
    print("AZ-C does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "pgvector leg holds: chunk boundaries fall on headings, every stored chunk begins at"
)
print("its heading, and a mid-section phrase returns the whole section it came from.")
if azure_results is None:
    print(
        "Azure AI Search leg: NOT RUN (no service configured). Nothing is claimed for it."
    )
else:
    print("Azure AI Search leg holds and agrees with the baseline on the top section.")
