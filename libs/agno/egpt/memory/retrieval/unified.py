"""One retrieval interface spanning the KB and memory, source-labeled (SM-S4, #121).

KB (``knowledge/``, curated/static/read-only) and memory (dynamic/write-heavy/temporal) differ on
mutability, time, and ownership (docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md) and stay
separate stores -- decisions D7/D8, ADR 0003. This module does not touch either store; it composes
:class:`~egpt.memory.provider.MemoryProvider` and :class:`KnowledgeRetrievable` -- the retrieval
slice of :class:`~egpt.knowledge.protocol.KnowledgeProtocol` that a real ``Knowledge`` instance
already satisfies -- behind one call that labels each result by the store it came from.

Ranking the two together is out of scope: a KB document's score is a vector-DB similarity (or
``None`` pre-rerank) and a memory record's may already be fused across substrates by the seam
(SM-H2, #132), so the two are not comparable yet. :class:`UnifiedSlice` keeps them apart rather
than interleaving by a comparison this ticket does not make.
"""

import asyncio
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Hashable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Union,
    runtime_checkable,
)

from egpt.filters import FilterExpr
from egpt.knowledge.document import Document
from egpt.memory.provider import MemoryProvider
from egpt.memory.retrieval.fuse import DEFAULT_RRF_K, rrf_rank
from egpt.memory.types import MemoryQuery, MemoryRecord, Scope

#: What ``knowledge.retrieve``/``aretrieve`` actually accept as ``filters`` (matches
#: ``Knowledge.retrieve``, ``knowledge/knowledge.py``) -- narrower than ``Any`` so a caller
#: passing e.g. a bare string is a type error here rather than a runtime surprise inside the leg.
KbFilters = Optional[Union[Dict[str, Any], List[FilterExpr]]]

__all__ = [
    "AsyncLeg",
    "KbFilters",
    "KnowledgeRetrievable",
    "Leg",
    "RetrievalSource",
    "UnifiedResult",
    "UnifiedSlice",
    "aunified_read",
    "default_identity",
    "unified_read",
]

#: An extra retrieval leg: given the query, return its results already ranked best-first.
#:
#: A callable rather than a store, because the framework cannot know what the fourth store
#: *is*. A deployment with an ontology graph and a semantic layer has two more places worth
#: asking, and both are its own; what belongs here is the fan-out, the labelling and the
#: fusion, not a hardcoded list of everyone's stores.
Leg = Callable[[MemoryQuery], Sequence["UnifiedResult"]]

#: The async counterpart. Legs are awaited concurrently on the async path.
AsyncLeg = Callable[[MemoryQuery], Awaitable[Sequence["UnifiedResult"]]]


@runtime_checkable
class KnowledgeRetrievable(Protocol):
    """The KB leg's actual contract -- narrower than :class:`~egpt.knowledge.protocol.KnowledgeProtocol`.

    The full protocol also requires ``build_context``/``get_tools``/``aget_tools``, none of which
    this module calls; annotating against it would reject a KB stand-in that implements only
    retrieval. And ``KnowledgeProtocol.retrieve``/``aretrieve`` declare ``**kwargs`` only, so a
    conformant minimal implementation can silently drop ``max_results`` -- this protocol declares
    the parameters ``unified_read``/``aunified_read`` actually pass, so mypy catches a leg that
    does not honour them.
    """

    def retrieve(
        self, query: str, max_results: Optional[int] = None, filters: KbFilters = None, **kwargs: Any
    ) -> List[Document]: ...

    async def aretrieve(
        self, query: str, max_results: Optional[int] = None, filters: KbFilters = None, **kwargs: Any
    ) -> List[Document]: ...


class RetrievalSource(str, Enum):
    """Which store a :class:`UnifiedResult` came from.

    Two members because two stores are built in here. A deployment's own legs -- an ontology,
    a semantic layer -- name themselves through :attr:`UnifiedResult.origin`, which is a plain
    string for exactly that reason: a frozen enum in the framework cannot enumerate the stores
    a product will add, and pretending otherwise would either lock the set or fill it with
    nouns no framework user has.
    """

    KB = "kb"
    MEMORY = "memory"


@dataclass(frozen=True)
class UnifiedResult:
    """One result, labeled by the store it came from and the leg that produced it."""

    content: str
    #: The built-in store, for the two legs this module retrieves itself. An extra leg that
    #: is neither carries whichever it most resembles and distinguishes itself by ``origin``.
    source: RetrievalSource
    score: Optional[float] = None
    id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: The leg's name -- ``"kb"``, ``"memory"``, or whatever an extra leg was registered as.
    #: This is what a caller renders as provenance, and what fusion reports per hit.
    origin: str = ""

    @property
    def label(self) -> str:
        """The leg name, falling back to the store when a result predates ``origin``."""
        return self.origin or self.source.value


@dataclass(frozen=True)
class UnifiedSlice:
    """The fan-out result: KB and memory results held apart, plus one labeled view of both.

    A leg with no store wired is an empty list, not an absent one -- the same "did not run"
    convention the memory seam's own substrate legs use (``RetrievalCandidates``, returned by
    ``VectorSubstrate.search_candidates``, ``memory/types.py``), so a caller can distinguish "no
    KB configured" from "KB configured but no match" by whether it passed ``knowledge`` in the
    first place, not by inspecting this result.
    """

    kb: List[UnifiedResult] = field(default_factory=list)
    memory: List[UnifiedResult] = field(default_factory=list)
    #: Any extra legs the caller wired, keyed by the name it registered them under and held
    #: in registration order.
    extra: Dict[str, List[UnifiedResult]] = field(default_factory=dict)

    @property
    def results(self) -> List[UnifiedResult]:
        """Every leg's results, unfused, in leg order. Each carries its own ``origin``."""
        out = [*self.kb, *self.memory]
        for results in self.extra.values():
            out.extend(results)
        return out

    def legs(self) -> Dict[str, List[UnifiedResult]]:
        """The results keyed by leg name, ready for fusion.

        Empty legs are kept. A leg that ran and matched nothing is a different fact from a leg
        that was never wired, and dropping it here would erase the difference exactly where a
        caller checking whether a leg contributed would look.
        """
        return {"kb": self.kb, "memory": self.memory, **self.extra}

    def fused(
        self,
        *,
        k: int = DEFAULT_RRF_K,
        identity: Optional[Callable[[UnifiedResult], Hashable]] = None,
        limit: Optional[int] = None,
    ) -> List[UnifiedResult]:
        """One ranking across every leg, by Reciprocal Rank Fusion.

        This is what the package could not do before: the RRF that fuses memory's own
        substrate legs lived in ``fuse.py`` typed to ``MemoryRecord``, so nothing above the
        memory store could reach it and this class held its legs apart rather than ranking
        them. Rank-based fusion is what makes ranking them defensible at all -- a KB
        document's similarity, a memory record's fused score and a graph hit's traversal
        distance are three incomparable scales, and RRF reads none of them. It reads position.

        Each returned result carries the fused score, the ``origin`` of the leg that ranked
        it best, ``metadata["fused_from"]`` -- how many legs had it -- and
        ``metadata["fused_origins"]``, naming them.

        Both provenance fields are here because neither substitutes for the other. The count
        is what a caller ranks or filters on; the names are what it renders, and a
        corroborated hit reports only one ``origin`` by construction -- the fused item keeps
        the copy from whichever leg saw it first -- so without the list the second store's
        contribution would be invisible on screen. "The prose and the ontology both say this"
        is a different claim from "two legs said this".
        """
        ranked = rrf_rank(
            self.legs(),
            identity=identity or default_identity,
            tie_break=lambda r: (r.content, r.label, r.id or ""),
            k=k,
        )
        fused = [
            replace(
                entry.item,
                score=entry.score,
                metadata={
                    **entry.item.metadata,
                    "fused_from": entry.legs,
                    "fused_origins": list(entry.sources),
                },
            )
            for entry in ranked
        ]
        return fused[:limit] if limit is not None else fused


def default_identity(result: UnifiedResult) -> Hashable:
    """When two results from different legs are the same finding: when they say the same thing.

    Ids are per-store, so identity by id would make every cross-store pair distinct and reduce
    fusion to an interleave -- the one thing a unified read exists to improve on. Normalised
    content is the only comparable key two stores actually share, and the case it gets right
    is the case that matters: a domain rule the prose states and memory has also recorded
    should read as corroborated by two legs, not as two separate hits that happen to match.
    """
    return " ".join(result.content.split()).casefold()


def _kb_result(document: Document) -> UnifiedResult:
    return UnifiedResult(
        content=document.content,
        source=RetrievalSource.KB,
        origin="kb",
        score=document.reranking_score,
        id=document.id if document.id is not None else document.content_id,
        metadata=dict(document.meta_data or {}),
    )


def _memory_result(record: MemoryRecord) -> UnifiedResult:
    return UnifiedResult(
        content=record.content,
        source=RetrievalSource.MEMORY,
        origin="memory",
        score=record.score,
        id=record.id,
        metadata={**record.metadata, "tier": record.tier.value},
    )


def _labelled(name: str, results: Sequence[UnifiedResult]) -> List[UnifiedResult]:
    """Stamp a leg's own name onto its results.

    Done here rather than trusted to the leg: ``origin`` is what a caller renders as
    provenance and what fusion groups by, and a leg that forgot to set it -- or set it to
    something other than the key it was registered under -- would produce results whose stated
    source is not the leg that produced them. The registration key is the truth.
    """
    return [replace(result, origin=name) for result in results]


def _kb_leg(
    knowledge: Optional[KnowledgeRetrievable], query: MemoryQuery, kb_filters: KbFilters
) -> List[UnifiedResult]:
    # ``limit=0`` must mean "zero results", but ``Knowledge.search`` computes
    # ``max_results or self.max_results`` -- 0 is falsy, so a caller asking for nothing would
    # silently get the KB's default page instead. Handled here, at the boundary, rather than in
    # ``Knowledge`` (a shared file every other consumer of it also depends on).
    if knowledge is None or query.limit == 0:
        return []
    documents = knowledge.retrieve(query.text, max_results=query.limit, filters=kb_filters)
    return [_kb_result(document) for document in documents]


def _memory_leg(memory: Optional[MemoryProvider], scope: Scope, query: MemoryQuery) -> List[UnifiedResult]:
    if memory is None:
        return []
    memory_slice = memory.read(scope, query)
    return [_memory_result(record) for record in memory_slice.records]


def unified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeRetrievable] = None,
    kb_filters: KbFilters = None,
    legs: Optional[Mapping[str, Leg]] = None,
) -> UnifiedSlice:
    """Fan ``query`` out across the KB, memory and any extra legs, each result labeled.

    ``query.text`` and ``query.limit`` serve both legs -- one request envelope, not a KB-specific
    and a memory-specific query type, since the two already carry what each leg's own ``retrieve``/
    ``read`` needs.

    Both legs are always attempted, even if one raises: the two stores are independent, so one
    failing must not silently discard the other's, already-fetched, successful results. If a leg
    raises, its exception is re-raised after both legs have run -- KB's before memory's, if both
    raised. See :func:`aunified_read` for why the async variant needs the same "run both, then
    raise" shape for an additional reason (an un-awaited task, not just a discarded result).

    Args:
        kb_filters: Forwarded to ``knowledge.retrieve`` only -- there is no memory-side
            equivalent, so the memory leg always reads with the unfiltered ``query``. Pass
            ``None`` (the default) to retrieve from the KB without filtering.
        legs: Extra named legs, each a callable returning results ranked best-first. A
            deployment's own stores -- an ontology graph, a semantic layer -- arrive here.
            Omitting a leg is how a caller disables it, which is what makes it possible to
            show that a leg was contributing rather than decorating.

    Every leg runs even if an earlier one raised, and the first exception is re-raised
    afterwards in leg order. The stores are independent, so one failing must not discard
    another's already-fetched results.
    """
    kb_results: List[UnifiedResult] = []
    kb_error: Optional[Exception] = None
    try:
        kb_results = _kb_leg(knowledge, query, kb_filters)
    except Exception as exc:
        kb_error = exc

    memory_results: List[UnifiedResult] = []
    memory_error: Optional[Exception] = None
    try:
        memory_results = _memory_leg(memory, scope, query)
    except Exception as exc:
        memory_error = exc

    extra_results: Dict[str, List[UnifiedResult]] = {}
    extra_errors: List[Exception] = []
    for name, leg in (legs or {}).items():
        try:
            extra_results[name] = _labelled(name, leg(query))
        except Exception as exc:
            extra_results[name] = []
            extra_errors.append(exc)

    if kb_error is not None:
        raise kb_error
    if memory_error is not None:
        raise memory_error
    if extra_errors:
        raise extra_errors[0]

    return UnifiedSlice(kb=kb_results, memory=memory_results, extra=extra_results)


async def _akb_leg(
    knowledge: Optional[KnowledgeRetrievable], query: MemoryQuery, kb_filters: KbFilters
) -> List[UnifiedResult]:
    if knowledge is None or query.limit == 0:
        return []
    documents = await knowledge.aretrieve(query.text, max_results=query.limit, filters=kb_filters)
    return [_kb_result(document) for document in documents]


async def _amemory_leg(memory: Optional[MemoryProvider], scope: Scope, query: MemoryQuery) -> List[UnifiedResult]:
    if memory is None:
        return []
    memory_slice = await memory.aread(scope, query)
    return [_memory_result(record) for record in memory_slice.records]


async def aunified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeRetrievable] = None,
    kb_filters: KbFilters = None,
    legs: Optional[Mapping[str, AsyncLeg]] = None,
) -> UnifiedSlice:
    """Async variant of :func:`unified_read`.

    The two legs share no state and have no ordering dependency, so they run concurrently via
    ``asyncio.gather`` rather than one awaited after the other. ``return_exceptions=True`` so a
    raising leg does not leave the other running un-awaited -- ``gather``'s default behaviour
    does not cancel a sibling task when one raises, so without this the surviving task keeps
    running with no one left to observe its result or a later exception. Both legs are awaited to
    completion, then the first exception (KB's before memory's) is re-raised, matching the sync
    variant's ordering.
    """
    names = list((legs or {}).keys())
    outcomes = await asyncio.gather(
        _akb_leg(knowledge, query, kb_filters),
        _amemory_leg(memory, scope, query),
        *((legs or {})[name](query) for name in names),
        return_exceptions=True,
    )
    kb_outcome, memory_outcome, extra_outcomes = outcomes[0], outcomes[1], outcomes[2:]

    extra: Dict[str, List[UnifiedResult]] = {}
    first_extra_error: Optional[BaseException] = None
    for name, outcome in zip(names, extra_outcomes):
        if isinstance(outcome, BaseException):
            extra[name] = []
            first_extra_error = first_extra_error or outcome
        else:
            extra[name] = _labelled(name, outcome)

    if isinstance(kb_outcome, BaseException):
        raise kb_outcome
    if isinstance(memory_outcome, BaseException):
        raise memory_outcome
    if first_extra_error is not None:
        raise first_extra_error
    return UnifiedSlice(kb=list(kb_outcome), memory=list(memory_outcome), extra=extra)
