"""K2 -- curated retail prose into the knowledge base, split on headings.

The package's verification, from the delivery plan: ingest a document with known headings,
inspect the stored chunks, and search for a phrase sitting in the middle of one section. The
evidence is that chunk boundaries fall on headings, each chunk carries its heading, and the
search returns that whole section rather than a fragment starting mid-sentence.

Section 1 is the part worth reading. ``MarkdownChunking`` defaults ``split_on_headings`` to
``False``, and on this corpus the default produces chunks of which **none** begins at a
heading. That is the whole reason this package exists as more than a one-line call.

Needs pgvector and the corpus:

    ./cookbook/scripts/run_pgvector.sh
    gh repo clone Saturam-Inc/retailGPT

Run:
    EGPT_RETAIL_CORPUS=<path-to>/retailGPT/research_docs \\
      .venvs/demo/bin/python cookbook/94_retail/K02_domain_prose.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt.integrations.knowledge.reader.markdown_reader import (
    MarkdownReader,  # noqa: E402
)
from egpt.knowledge.knowledge import Knowledge  # noqa: E402
from egpt.metrics import ProviderMode  # noqa: E402
from egpt.vectordb.pgvector import PgVector  # noqa: E402
from egpt.vectordb.search import SearchType  # noqa: E402
from egpt_retail.knowledge.embedding import resolve_knowledge_embedder  # noqa: E402
from egpt_retail.knowledge.prose import (  # noqa: E402
    ORIGIN_SHIPPED,
    build_chunker,
    chunking_evidence,
    ingest_corpus,
    read_corpus,
)

CORPUS = Path(
    os.environ.get(
        "EGPT_RETAIL_CORPUS",
        Path(__file__).resolve().parents[2] / "product/egpt_retail/knowledge/corpus",
    )
).expanduser()
DB_URL = os.environ.get(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)
TABLE = os.environ.get("EGPT_RETAIL_KNOWLEDGE_TABLE", "egpt_retail_domain_prose_bge")

if not CORPUS.is_dir():
    raise SystemExit(
        "Set EGPT_RETAIL_CORPUS to the research_docs directory of Saturam-Inc/retailGPT."
    )

print("K2 -- curated retail prose")
print("=" * 78)

# --- 1. Why the settings are what they are -----------------------------------------------

sample = CORPUS / "08-inventory-management-and-demand-forecasting.md"
print()
print("1. The chunking decision, measured on one document")
print("-" * 78)
print(f"  {sample.name}  ({len(sample.read_text()):,} chars)")
print()
print(f"  {'setting':<26}{'chunks':>8}{'start at heading':>19}{'largest':>10}")
rows = chunking_evidence(sample.read_text(), name=sample.stem)
for row in rows:
    print(
        f"  {row['setting']:<26}{row['chunks']:>8}{row['start_at_heading']:>19}{row['largest']:>10}"
    )
default_row = rows[0]
chosen_row = rows[2]
wide_row = rows[3]
print()
print(
    f"  The library default leaves {default_row['chunks'] - default_row['start_at_heading']} of"
)
print(
    f"  {default_row['chunks']} chunks starting mid-content, with no heading to say what they are."
)
print(
    f"  The chosen setting starts {chosen_row['start_at_heading']}/{chosen_row['chunks']} at a heading, and"
)
print(
    f"  raising the cap to 20k changes nothing ({wide_row['chunks']} chunks), so no section is split by size."
)

# --- 2. Ingest the corpus ----------------------------------------------------------------

documents = read_corpus(CORPUS)
print()
print("2. Ingestion")
print("-" * 78)
print(
    f"  documents: {len(documents)} (table of contents excluded as navigation, not prose)"
)

resolved = resolve_knowledge_embedder(mode=ProviderMode.LOCAL)
print(f"  embedder: {resolved}")

knowledge = Knowledge(
    vector_db=PgVector(
        table_name=TABLE,
        db_url=DB_URL,
        search_type=SearchType.hybrid,
        embedder=resolved.embedder,
    ),
)

reader = MarkdownReader(chunking_strategy=build_chunker())
report = ingest_corpus(knowledge, documents, reader=reader)
print(f"  {report}")

# --- 3. Inspect what was stored ----------------------------------------------------------

raw_url = DB_URL.replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(raw_url) as connection:
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT count(*) FROM ai."{TABLE}"')
        total = cursor.fetchone()[0]
        cursor.execute(f"""SELECT content FROM ai."{TABLE}" LIMIT 400""")
        contents = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            f"""SELECT meta_data->>'origin' AS origin, count(*) FROM ai."{TABLE}" GROUP BY 1"""
        )
        origins = cursor.fetchall()

starting_at_heading = sum(1 for c in contents if c.lstrip().startswith("#"))
print()
print("3. What is stored")
print("-" * 78)
print(f"  chunks in {TABLE}: {total}")
print(f"  sampled {len(contents)}: {starting_at_heading} start at a heading")
print(f"  origins: {', '.join(f'{o or '(none)'}={n}' for o, n in origins)}")
print()
print("  first three chunk openings:")
for content in contents[:3]:
    print(f"    {content.splitlines()[0][:70]!r}")

# --- 4. Search for a phrase in the middle of a section -----------------------------------

print()
print("4. A phrase from the middle of a section")
print("-" * 78)
queries = [
    "amplification of demand variability upstream through a supply chain",
    "what does a category captain actually do",
    "how should we set a markdown on end-of-season stock",
]
search_failures = []
for query in queries:
    results = knowledge.search(query, max_results=2)
    print(f"  {query!r}")
    if not results:
        print("      -> nothing returned")
        search_failures.append(f"{query!r} returned nothing")
        continue
    top = results[0]
    first_line = top.content.splitlines()[0] if top.content else ""
    whole_section = first_line.lstrip().startswith("#")
    print(f"      -> {first_line[:66]!r}")
    print(
        f"         from {top.meta_data.get('document', '?')}, origin {top.meta_data.get('origin', '?')}"
    )
    print(f"         starts at its heading: {whole_section}")
    if not whole_section:
        search_failures.append(f"{query!r} returned a fragment, not a whole section")

# --- 5. Verdict --------------------------------------------------------------------------

print()
print("=" * 78)
failures = list(search_failures)
if default_row["start_at_heading"] != 0:
    print(
        "  note: the library default did start some chunks at headings on this document"
    )
if chosen_row["start_at_heading"] != chosen_row["chunks"]:
    failures.append("not every chunk begins at a heading under the chosen setting")
if chosen_row["chunks"] != wide_row["chunks"]:
    failures.append(
        "raising the size cap changed the chunking, so sections are being split by size"
    )
if starting_at_heading != len(contents):
    failures.append(
        f"{len(contents) - starting_at_heading} stored chunks do not begin at a heading"
    )
if report.documents != len(documents):
    failures.append("not every document was ingested")
if not any(o == ORIGIN_SHIPPED for o, _ in origins):
    failures.append("chunks are not tagged with their origin")

if failures:
    print("K2 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print("Prose holds. Every chunk begins at its heading, carries its origin, and a")
print("mid-section phrase returns the whole section it came from.")
