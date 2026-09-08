"""``egpt-retail load-knowledge`` -- load the shipped domain knowledge, then exit.

The deploy-time counterpart to ``migrate``. That one brings the control-plane *schema* to the
current version; this one brings the shipped *content* to it: the retail ontology into Neo4j
and the curated retail prose into pgvector.

Deployed as a Helm ``post-install,post-upgrade`` hook Job, not a ``pre-`` one: it writes
through the application's own code paths and needs the schema migration to have finished
first. Never an initContainer — with N replicas that is N concurrent loads of the same content.

**Idempotent, and re-runnable on every upgrade.** The ontology write replaces its own label and
nothing else; the prose ingest upserts by document name. Running it twice changes nothing,
which is what makes it safe as an upgrade hook rather than an install-only step.

**What it refuses rather than doing quietly.** A vector index built at one width and an
embedder producing another is the failure this entrypoint exists to catch: nothing errors, the
writes are accepted, and retrieval silently degrades. Moving a deployment between Mode A (768)
and Mode B (1024) is exactly that, so the load stops unless ``--rebuild-indexes`` says the
operator meant it.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, List, Optional, Sequence

from egpt_retail.app.settings import RetailSettings
from egpt_retail.knowledge.embedding import resolve_knowledge_embedder
from egpt_retail.knowledge.prose import build_chunker, ingest_corpus, read_corpus
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology
from egpt_retail.ontology.store import OntologyStore

__all__ = ["load_knowledge", "main", "resolve_corpus_path"]

log = logging.getLogger("egpt_retail.load_knowledge")

#: The corpus shipped inside the package, used when nothing is configured.
PACKAGE_CORPUS = Path(__file__).resolve().parent.parent / "knowledge" / "corpus"


def resolve_corpus_path(settings: RetailSettings) -> Path:
    """Where the curated prose is, configured or shipped."""
    if settings.retail_corpus_path:
        return Path(settings.retail_corpus_path).expanduser()
    return PACKAGE_CORPUS


def load_knowledge(
    settings: Optional[RetailSettings] = None,
    *,
    ontology: bool = True,
    prose: bool = True,
    rebuild_indexes: bool = False,
    driver: Any = None,
    knowledge: Any = None,
) -> List[str]:
    """Load the shipped ontology and prose. Returns one report line per step.

    ``driver`` and ``knowledge`` are injectable so a test can drive this without a database;
    left unset, both are built from settings.
    """
    settings = settings or RetailSettings()
    report: List[str] = []

    embedder = resolve_knowledge_embedder(mode=settings.retail_provider_mode)
    report.append(f"embedder: {embedder}")
    log.info("Knowledge embedder resolved: %s", embedder)

    if ontology:
        report.extend(
            _load_ontology(
                settings, embedder, rebuild_indexes=rebuild_indexes, driver=driver
            )
        )
    if prose:
        report.extend(_load_prose(settings, embedder, knowledge=knowledge))
    return report


def _load_ontology(
    settings: RetailSettings,
    embedder: Any,
    *,
    rebuild_indexes: bool,
    driver: Any = None,
) -> List[str]:
    owned = driver is None
    if owned:
        if not settings.retail_neo4j_password:
            raise SystemExit(
                "EGPT_RETAIL_NEO4J_PASSWORD is not set, so the ontology has nowhere to go. "
                "Refusing to start rather than reporting a successful load of nothing."
            )
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.retail_neo4j_uri,
            auth=(settings.retail_neo4j_user, settings.retail_neo4j_password),
        )
    try:
        store = OntologyStore(
            driver=driver,
            embedder=embedder.embedder,
            database=settings.retail_neo4j_database,
        )
        overlay = (
            Path(settings.retail_ontology_overlay)
            if settings.retail_ontology_overlay
            else None
        )
        artifact = load_ontology(ARTIFACT_PATH, overlay_path=overlay)
        artifact.validate_relations()

        existing = store.index_dimensions()
        if (
            existing is not None
            and existing != embedder.dimensions
            and not rebuild_indexes
        ):
            raise SystemExit(
                f"The ontology vector index exists at {existing} dimensions and the configured "
                f"embedder produces {embedder.dimensions}. Every stored vector came from the "
                "previous model and is not comparable with the new one. Re-run with "
                "--rebuild-indexes to drop the index and re-embed."
            )
        store.ensure_indexes(rebuild_on_dimension_change=rebuild_indexes)
        written = store.write(artifact)

        lines = [
            f"ontology: {written} concepts at version {artifact.version}"
            + (f", overlay {overlay.name}" if overlay else ", no overlay"),
            f"ontology: vector index at {store.index_dimensions()} dimensions",
        ]
        missing = store.concepts_without_embedding()
        if missing:
            raise SystemExit(
                f"{len(missing)} concept(s) were written with no vector: {list(missing)}. "
                "They would be reachable only by exact name, and nothing downstream reports it."
            )
        return lines
    finally:
        if owned:
            driver.close()


def _load_prose(
    settings: RetailSettings, embedder: Any, *, knowledge: Any = None
) -> List[str]:
    corpus = resolve_corpus_path(settings)
    if not corpus.is_dir():
        raise SystemExit(
            f"No corpus at {corpus}. Set EGPT_RETAIL_CORPUS_PATH, or check the package ships "
            "product/egpt_retail/knowledge/corpus."
        )
    documents = read_corpus(corpus)
    if not documents:
        raise SystemExit(f"{corpus} holds no markdown documents.")

    if knowledge is None:
        from egpt.knowledge.knowledge import Knowledge
        from egpt.vectordb.pgvector import PgVector
        from egpt.vectordb.search import SearchType

        knowledge = Knowledge(
            vector_db=PgVector(
                table_name=settings.retail_knowledge_table,
                db_url=settings.retail_db_url,
                search_type=SearchType.hybrid,
                embedder=embedder.embedder,
            ),
        )

    from egpt.integrations.knowledge.reader.markdown_reader import MarkdownReader

    report = ingest_corpus(
        knowledge, documents, reader=MarkdownReader(chunking_strategy=build_chunker())
    )
    return [f"prose: {report} into {settings.retail_knowledge_table}"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="egpt-retail load-knowledge",
        description="Load the shipped retail ontology and prose, then exit.",
    )
    parser.add_argument(
        "--ontology-only", action="store_true", help="Skip the prose ingest."
    )
    parser.add_argument(
        "--prose-only", action="store_true", help="Skip the ontology write."
    )
    parser.add_argument(
        "--rebuild-indexes",
        action="store_true",
        help="Drop and rebuild a vector index whose width no longer matches the embedder. "
        "Needed when moving between provider modes; refuses silently otherwise.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    if args.ontology_only and args.prose_only:
        raise SystemExit("--ontology-only and --prose-only are mutually exclusive.")
    lines = load_knowledge(
        ontology=not args.prose_only,
        prose=not args.ontology_only,
        rebuild_indexes=args.rebuild_indexes,
    )
    for line in lines:
        log.info("%s", line)
    return 0
