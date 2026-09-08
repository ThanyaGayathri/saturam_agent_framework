"""Curated retail prose into the knowledge base -- layer 1 of the domain knowledge.

What a metric means, why a pattern occurs, what good looks like. Written by us, judged by a
retail practitioner, and changed only when we release a version. Ingested here, retrieved
later through the one entry point K10 builds.

**Split on headings, not by size.** ``MarkdownChunking`` defaults ``split_on_headings`` to
``False``, and the default is quietly wrong for curated prose: on the retail corpus it
produced twenty-four chunks of which **none** began at a heading, so every one started
mid-sentence and carried no indication of which section it came from. A reader handed that
chunk cannot tell what it is about, and neither can a model.

The settings below were chosen by measurement rather than taste; ``chunking_evidence()``
reproduces the comparison. Every chunk begins with its own heading, and no section is ever
split by size -- raising the cap from 12,000 to 20,000 characters changes nothing, which is
what proves the cap never binds on this corpus.

**Tagged by origin.** Shipped prose and a customer's own documents land in the same store and
are told apart by ``origin`` in metadata, so a provenance record can say which of the two an
answer came from (the gap report's resolution for customer documents).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "CHUNK_SIZE",
    "ORIGIN_CUSTOMER",
    "ORIGIN_SHIPPED",
    "SPLIT_ON_HEADINGS",
    "CorpusDocument",
    "IngestionReport",
    "aingest_corpus",
    "build_chunker",
    "chunking_evidence",
    "ingest_corpus",
    "read_corpus",
]

#: Split on headings down to level three: h1 titles, h2 sections, h3 subsections. Passed
#: explicitly because the library default is ``False`` and silently produces size-based chunks.
SPLIT_ON_HEADINGS = 3

#: Large enough that no section in the retail corpus is ever split by size. Measured, not
#: guessed: at 12,000 and at 20,000 the corpus chunks identically, so the cap never binds. It
#: stays as a bound against a pathologically long section rather than as a chunking strategy.
CHUNK_SIZE = 12_000

#: Our own curated writing, delivered with every deployment.
ORIGIN_SHIPPED = "shipped"

#: The customer's own material, ingested alongside ours and distinguishable in provenance.
ORIGIN_CUSTOMER = "customer"


@dataclass(frozen=True)
class CorpusDocument:
    """One markdown document to ingest."""

    path: Path
    #: Stable identifier, used as the content name so a re-ingest upserts rather than duplicates.
    name: str
    origin: str = ORIGIN_SHIPPED
    #: The embedding tag every chunk carries, matching what memory stamps. See
    #: ``egpt_retail.knowledge.embedding``.
    embedding_version: str = ""

    @property
    def metadata(self) -> Dict[str, str]:
        """What every chunk of this document carries.

        ``origin`` is the load-bearing one: it is what lets an answer say whether a claim came
        from our shipped prose or from the customer's own document, which is a different
        statement about how much to trust it.
        """
        return {
            "origin": self.origin,
            "document": self.name,
            "layer": "domain_prose",
            "embedding_version": self.embedding_version,
        }


@dataclass(frozen=True)
class IngestionReport:
    """What an ingestion run did, for the test log and for provenance."""

    documents: int
    origin: str
    names: Tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.documents} {self.origin} document(s) ingested"


def build_chunker(
    split_on_headings: int = SPLIT_ON_HEADINGS,
    chunk_size: int = CHUNK_SIZE,
) -> Any:
    """The chunking strategy, with the heading split passed explicitly.

    Imported lazily because ``MarkdownChunking`` requires the optional ``unstructured``
    package; a module-level import would make this whole module unimportable wherever that
    dependency is absent, including in a checker that only wants the constants above.
    """
    from egpt.knowledge.chunking.markdown import MarkdownChunking

    return MarkdownChunking(split_on_headings=split_on_headings, chunk_size=chunk_size)


def read_corpus(
    directory: Path,
    origin: str = ORIGIN_SHIPPED,
    exclude: Sequence[str] = ("00-table-of-contents.md", "README.md"),
    embedding_version: str = "",
) -> List[CorpusDocument]:
    """Every markdown document in a directory, in name order.

    Two files are excluded by default and both are exclusions of the same kind: they sit in the
    directory but are not the corpus. The table of contents is navigation, a list of links whose
    chunks retrieve as a wall of anchors competing with the sections they point at. ``README.md``
    is our note *about* the corpus -- it was ingested once, silently, and turned 31 documents
    into 32 before the count was read carefully.
    """
    excluded = set(exclude)
    return [
        CorpusDocument(
            path=path,
            name=path.stem,
            origin=origin,
            embedding_version=embedding_version,
        )
        for path in sorted(Path(directory).glob("*.md"))
        if path.name not in excluded
    ]


def ingest_corpus(
    knowledge: Any,
    documents: Sequence[CorpusDocument],
    *,
    reader: Optional[Any] = None,
    upsert: bool = True,
) -> IngestionReport:
    """Ingest curated prose into the knowledge base.

    One ``insert`` per document rather than one for the directory, because each document
    carries its own metadata and a directory insert would flatten them into one origin and one
    name.
    """
    if not documents:
        return IngestionReport(documents=0, origin=ORIGIN_SHIPPED, names=())
    active_reader = reader if reader is not None else _default_reader()
    for document in documents:
        knowledge.insert(
            name=document.name,
            path=str(document.path),
            metadata=document.metadata,
            reader=active_reader,
            upsert=upsert,
        )
    return IngestionReport(
        documents=len(documents),
        origin=documents[0].origin,
        names=tuple(d.name for d in documents),
    )


async def aingest_corpus(
    knowledge: Any,
    documents: Sequence[CorpusDocument],
    *,
    reader: Optional[Any] = None,
    upsert: bool = True,
) -> IngestionReport:
    """Async variant."""
    if not documents:
        return IngestionReport(documents=0, origin=ORIGIN_SHIPPED, names=())
    active_reader = reader if reader is not None else _default_reader()
    for document in documents:
        await knowledge.ainsert(
            name=document.name,
            path=str(document.path),
            metadata=document.metadata,
            reader=active_reader,
            upsert=upsert,
        )
    return IngestionReport(
        documents=len(documents),
        origin=documents[0].origin,
        names=tuple(d.name for d in documents),
    )


def _default_reader() -> Any:
    """A markdown reader carrying the heading-split chunker."""
    from egpt.integrations.knowledge.reader.markdown_reader import MarkdownReader

    return MarkdownReader(chunking_strategy=build_chunker())


def chunking_evidence(text: str, name: str = "sample") -> List[Dict[str, Any]]:
    """Compare chunking settings on one document, and return what each produced.

    Exists because the choice of settings is the substance of this package, and a setting
    nobody can re-measure is a setting nobody will question when it drifts. The row that
    matters is the default: it is what the library does if the heading split is left unset.
    """
    from egpt.knowledge.chunking.markdown import MarkdownChunking
    from egpt.knowledge.document import Document

    document = Document(content=text, name=name)
    rows: List[Dict[str, Any]] = []
    for label, kwargs in [
        ("library default", {}),
        ("split_on_headings=True", {"split_on_headings": True}),
        (
            "chosen: headings=3, 12k",
            {"split_on_headings": SPLIT_ON_HEADINGS, "chunk_size": CHUNK_SIZE},
        ),
        (
            "headings=3, 20k",
            {"split_on_headings": SPLIT_ON_HEADINGS, "chunk_size": 20_000},
        ),
    ]:
        chunks = MarkdownChunking(**kwargs).chunk(document)  # type: ignore[arg-type]
        sizes = [len(chunk.content) for chunk in chunks]
        rows.append(
            {
                "setting": label,
                "chunks": len(chunks),
                "start_at_heading": sum(
                    1 for c in chunks if c.content.lstrip().startswith("#")
                ),
                "largest": max(sizes) if sizes else 0,
            }
        )
    return rows


async def achunking_evidence(text: str, name: str = "sample") -> List[Dict[str, Any]]:
    """Async variant. The work is CPU-bound, so it runs off the event loop."""
    return await asyncio.to_thread(chunking_evidence, text, name)
