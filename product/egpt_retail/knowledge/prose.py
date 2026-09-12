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
reproduces the comparison. Every chunk begins with its own heading, enforced rather than
merely typical (``BodiedChunking`` drops any that does not, see below). **The size cap is not
inert**: 18 raw sections across 13 of the 31 corpus files exceed 12,000 characters before
size-splitting, up to 22,604 characters in one case. Raising the cap to 20,000 measurably
changes the output -- the heading splitter's 1,457 chunks become 1,436, which is 1,049 rather
than 1,028 once ``BodiedChunking`` has dropped the shells -- so the cap genuinely binds for
long sections rather than sitting unused as a defensive bound. It stays at 12,000 because that
value, not 20,000, is what the corpus was reviewed and mutation-tested against.

Every number in the paragraph above is asserted against the real corpus by
``product/tests/test_k02_k03_pure_logic.py``, so it fails a test rather than merely reading
wrong when the corpus changes. It read "8 of the 31" for two review rounds before that test
existed; a count nobody executes is a count that drifts.

**Tagged by origin.** Shipped prose and a customer's own documents land in the same store and
are told apart by ``origin`` in metadata, so a provenance record can say which of the two an
answer came from (the gap report's resolution for customer documents).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from egpt.knowledge.chunking.strategy import ChunkingStrategy

__all__ = [
    "CHUNK_SIZE",
    "ORIGIN_CUSTOMER",
    "ORIGIN_SHIPPED",
    "SPLIT_ON_HEADINGS",
    "CorpusDocument",
    "IngestionReport",
    "BodiedChunking",
    "aingest_corpus",
    "build_chunker",
    "has_body",
    "starts_at_heading",
    "chunking_evidence",
    "ingest_corpus",
    "read_corpus",
]

#: Split on headings down to level three: h1 titles, h2 sections, h3 subsections. Passed
#: explicitly because the library default is ``False`` and silently produces size-based chunks.
SPLIT_ON_HEADINGS = 3

#: Measured, not guessed -- but the cap does bind: 18 raw sections across 13 of the 31 corpus
#: files exceed 12,000 characters, and raising the cap to 20,000 measurably changes the output
#: (the splitter's 1,457 chunks become 1,436). It stays at 12,000 because that is the value the
#: corpus was reviewed and mutation-tested against, not because the cap never fires. Asserted
#: in ``product/tests/test_k02_k03_pure_logic.py``.
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


#: Anchored at column zero, with no leading ``\s*``: CommonMark makes four-space-indented text
#: a code block, so ``    # count the rows`` is a Python comment and not a heading. With the
#: leading ``\s*`` it matched, which made ``has_body`` strip real code as though it were a
#: heading and made ``starts_at_heading`` accept a chunk that starts inside a code block
#: (review round 1 of PR #204). Verified corpus-neutral: every count asserted in
#: ``test_k02_k03_pure_logic.py`` is unchanged by the anchoring, because the shipped corpus has
#: no indented ``#`` line.
_HEADING_LINE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)

#: Leading blank lines only. ``content.lstrip()`` would also strip a first line's indentation,
#: which re-admits the indented-``#`` case the anchor above exists to exclude.
_LEADING_BLANK_LINES = re.compile(r"\A(?:[ \t]*\r?\n)+")


def has_body(content: str) -> bool:
    """Whether a chunk says anything beyond its own heading.

    A heading-level split produces one chunk per heading, and an ``##`` section whose text is
    entirely in the ``###`` subsections beneath it comes out as a bare title. On the retail
    corpus that was 410 of 1,457 chunks -- twenty-eight percent -- each embedded, each matching
    on its title alone, each returning nothing a reader can use. *"who are our repeat buyers"*
    was answered by the chunk ``## 12. Key Metrics and Benchmarks`` and nothing else.

    This checks only what remains after the chunk's own heading is stripped -- it does not
    check that the chunk *starts* at a heading in the first place. Use
    :func:`starts_at_heading` for that; :class:`BodiedChunking` enforces both.
    """
    return bool(_HEADING_LINE.sub("", content).strip())


def starts_at_heading(content: str) -> bool:
    """Whether a chunk's first non-blank line is itself a heading.

    Every shipped corpus file happens to start with ``#``, so a leading chunk containing
    prose before the first heading never occurs today -- but nothing about the shipped
    corpus's formatting is guaranteed of a customer's own document (``ORIGIN_CUSTOMER``),
    which is ingested through this same chunker. Without this check, a document with prose
    before its first heading would produce a leading chunk that ``has_body`` keeps (it has
    real content) but that starts mid-prose with no heading at all, silently breaking the
    "every chunk begins with its own heading" guarantee this module exists to provide.
    """
    stripped = _LEADING_BLANK_LINES.sub("", content)
    return bool(stripped.strip()) and bool(_HEADING_LINE.match(stripped))


class BodiedChunking(ChunkingStrategy):
    """A chunking strategy that drops chunks with no body, or that do not start at a heading.

    Wraps the heading-split strategy rather than subclassing it, so the filter is one small
    thing with one reason to change and the heading split stays the library's.
    """

    def __init__(self, inner: ChunkingStrategy) -> None:
        self.inner = inner
        self.dropped = 0

    def chunk(self, document: Any) -> List[Any]:
        chunks = self.inner.chunk(document)
        kept = [
            chunk
            for chunk in chunks
            if has_body(chunk.content) and starts_at_heading(chunk.content)
        ]
        self.dropped += len(chunks) - len(kept)
        return kept


def build_chunker(
    split_on_headings: int = SPLIT_ON_HEADINGS,
    chunk_size: int = CHUNK_SIZE,
) -> ChunkingStrategy:
    """The chunking strategy: heading split passed explicitly, shells dropped.

    ``MarkdownChunking`` is imported lazily because it requires the optional ``unstructured``
    package; a module-level import would make this whole module unimportable wherever that
    dependency is absent, including in a checker that only wants the constants above.
    """
    from egpt.knowledge.chunking.markdown import MarkdownChunking

    return BodiedChunking(
        MarkdownChunking(split_on_headings=split_on_headings, chunk_size=chunk_size)
    )


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
        shells = sum(1 for c in chunks if not has_body(c.content))
        sizes = [len(chunk.content) for chunk in chunks]
        rows.append(
            {
                "setting": label,
                "chunks": len(chunks),
                "start_at_heading": sum(
                    1 for c in chunks if c.content.lstrip().startswith("#")
                ),
                "largest": max(sizes) if sizes else 0,
                "shells": shells,
            }
        )
    return rows


async def achunking_evidence(text: str, name: str = "sample") -> List[Dict[str, Any]]:
    """Async variant. The work is CPU-bound, so it runs off the event loop."""
    return await asyncio.to_thread(chunking_evidence, text, name)
