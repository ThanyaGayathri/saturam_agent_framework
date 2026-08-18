"""Entity resolution: folding duplicate candidates into one graph node (SM-G8 #106).

SM-G2 (#100) deliberately leaves two spellings of one entity as two nodes -- ``upsert_entities``
merges on the surface ``name``, not the normalised ``key``, so "Acme" and "Acme Corporation" land
as separate candidates (``test_two_spellings_stay_two_candidates`` pins that boundary). This module
is the ticket that closes it: given the entities a pass already wrote, find the ones that name the
same real-world thing and fold them into one node.

**Reuses the seed's dedup, not a second implementation of it.** The exact-match half of matching --
grouping on the normalised key -- is :func:`~egpt.memory.types.normalize_entity_name`, restated
from ``Curator._normalize`` (``learn/curate.py``) rather than imported, because that method is
private on a class needing a wired ``LearningMachine``; ``test_entity_normalization_matches_the_
curator`` (SM-G2's) pins that the two agree. This module adds nothing to that rule -- it only
consumes it, through :meth:`~egpt.memory.substrates.GraphSubstrate.match_entities`.

**Reads and writes through the graph, not through a batch held in memory.** ``match_entities``'
docstring explains why: the two lookups it composes -- exact key, embedding similarity -- are
targeted, index-backed queries against what the graph already has, not an enumeration
(``memory/graph/entities.py`` and ``episodic.py`` both reserve enumeration semantics for SM-G9
#107). A caller hands this pipeline the entities a pass just wrote; matching happens against the
graph's current state, which already includes anything an earlier candidate in the same run just
merged -- see :meth:`EntityResolutionPipeline.run`.

**Precondition, not enforced here:** every candidate has already reached the graph, typically
through :class:`~egpt.memory.graph.entities.EntityExtractionPipeline`. A candidate this pipeline
never sees a match for is left exactly as its writer left it -- this module never calls
``upsert_entities`` itself, so it cannot be the thing that first lands an unwritten candidate.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from egpt.memory.substrates import GraphSubstrate
from egpt.memory.types import EntityMerge, EntityWrite, MemoryOperationError, Scope, require_scope

__all__ = [
    "DEFAULT_MATCH_LIMIT",
    "DEFAULT_MIN_SCORE",
    "RESOLUTION_KEYS",
    "RESOLVER_ID",
    "EntityResolutionPipeline",
    "EntityResolutionReport",
    "resolution_details",
]

#: Stamped nowhere on the written row -- a merge is not a new fact about the entity, and
#: ``upsert_entities_statement`` has no field for "who last resolved this node" the way
#: ``derived_by`` records who last extracted it. Kept for the report only, matching
#: ``entities.py.EXTRACTOR_ID``'s role there.
RESOLVER_ID = "sm_g8.entity_resolution"

#: :meth:`~egpt.memory.substrates.GraphSubstrate.match_entities`'s own default, restated so a
#: caller constructing this pipeline without an opinion gets the same number the port documents.
DEFAULT_MIN_SCORE = 0.92

#: How many existing entities one candidate is matched against. Small on purpose: a candidate that
#: plausibly names five different existing entities is a sign the embedding text
#: (``entities.embedding_text``) is not discriminating, not a five-way merge waiting to happen.
DEFAULT_MATCH_LIMIT = 5

#: Keys every :class:`EntityResolutionReport` carries, the floor
#: :data:`~egpt.memory.types.CONSOLIDATION_KEYS` provides for the pass that drives this one.
RESOLUTION_KEYS: Tuple[str, ...] = ("resolver", "min_score")


def resolution_details(
    resolver: Optional[str] = None, min_score: Optional[float] = None, **extra: Any
) -> Dict[str, Any]:
    """Build a report payload carrying every key in :data:`RESOLUTION_KEYS`. Extra keys are free."""
    details: Dict[str, Any] = {"resolver": resolver, "min_score": min_score}
    details.update(extra)
    return details


@dataclass
class EntityResolutionReport:
    """What one resolution pass read and wrote.

    ``candidates`` is how many entities were actually offered for resolution -- not
    ``len(candidates)`` on the call, but excluding one already folded away by an earlier
    candidate's merge in the same pass (see :meth:`EntityResolutionPipeline.run`), which was never
    matched against and would inflate this count for a batch with a repeated name. ``merged`` is
    how many of the attempted candidates were folded into another node as a result (a candidate
    with no match is counted in the first and not the second, the same "candidates in, survivors
    out" shape ``EntityExtractionReport`` uses); ``groups`` is how many separate merge operations
    that took -- fewer than ``merged`` whenever one canonical absorbed more than one duplicate.
    """

    scope: Scope
    candidates: int = 0
    merged: int = 0
    groups: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.details.keys() >= set(RESOLUTION_KEYS):
            self.details = resolution_details(**self.details)


class EntityResolutionPipeline:
    """Candidates in, merged graph nodes out -- against the graph's current state (SM-G8 #106).

    ``graph`` must satisfy :meth:`~egpt.memory.substrates.GraphSubstrate.match_entities` and
    :meth:`~egpt.memory.substrates.GraphSubstrate.merge_entities`, both halves: a resolution pass
    that can match but not write, or write but not match, is not a pipeline the caller can run.
    """

    def __init__(
        self,
        graph: GraphSubstrate,
        min_score: float = DEFAULT_MIN_SCORE,
        match_limit: int = DEFAULT_MATCH_LIMIT,
    ) -> None:
        for verb in ("match_entities", "amatch_entities", "merge_entities", "amerge_entities"):
            if not callable(getattr(graph, verb, None)):
                raise MemoryOperationError(
                    f"EntityResolutionPipeline.graph is a {type(graph).__name__}, which is missing "
                    f"{verb!r}. Both halves are required: a pipeline whose async twin is absent "
                    "blocks the consolidation lane's event loop on the first pass.",
                    field_name="graph",
                )
        if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
            raise MemoryOperationError(
                f"min_score must be a number in [0.0, 1.0], got {min_score!r}.", field_name="min_score"
            )
        if not 0.0 <= float(min_score) <= 1.0:
            raise MemoryOperationError(
                f"min_score is {min_score}, outside [0.0, 1.0]. It is compared directly against "
                "GraphSubstrate.match_entities' score, which the port guarantees is in that range "
                "-- but which a backend need not report as raw cosine (Neo4j reports (1 + cosine) "
                "/ 2, so its min_score=0.92 admits a raw cosine no lower than 0.84).",
                field_name="min_score",
            )
        if not isinstance(match_limit, int) or isinstance(match_limit, bool) or match_limit < 1:
            raise MemoryOperationError(
                f"match_limit must be a positive integer, got {match_limit!r}. Zero would ask the "
                "graph to match every candidate against nothing, which is a resolution pass that "
                "reports success having resolved nothing.",
                field_name="match_limit",
            )
        self.graph = graph
        self.min_score = float(min_score)
        self.match_limit = match_limit

    # -- shared decisions -----------------------------------------------------

    @staticmethod
    def _require_own_scope(scope: Scope, candidate: EntityWrite) -> None:
        if candidate.scope != scope:
            raise MemoryOperationError(
                f"Candidate {candidate.name!r} carries scope {candidate.scope.key()} but is being "
                f"resolved under {scope.key()}. Batch per scope; the two must agree.",
                field_name="scope",
            )

    @staticmethod
    def _canonical(group: Sequence[EntityWrite]) -> EntityWrite:
        """Fold ``group`` -- the candidate plus every match -- into one node's worth of write.

        The head is the highest-confidence member, ties broken on content: the same rule
        ``entities.py._merged`` uses to group same-name candidates within one pass, so two rounds
        of folding (same-name, then cross-spelling) settle ties identically rather than by whichever
        one happened to run first. No uuid reaches the ordering, so one corpus resolves to one
        canonical across runs.
        """
        head = sorted(
            group,
            key=lambda c: (-c.confidence, c.entity_type, c.description or "", tuple(c.source_episode_ids)),
        )[0]
        return EntityWrite(
            name=head.name,
            entity_type=head.entity_type,
            scope=head.scope,
            description=head.description,
            # Every other member's own surface form becomes an alias of the survivor, alongside
            # whatever aliases it already carried. `EntityWrite.__post_init__` dedupes and drops
            # `head.name` itself, so a member that already listed the head as an alias is harmless.
            aliases=[alias for member in group for alias in (member.name, *member.aliases)],
            properties=head.properties,
            source_episode_ids=[episode_id for member in group for episode_id in member.source_episode_ids],
            confidence=head.confidence,
            derived_by=head.derived_by,
        )

    @classmethod
    def _merge_for(cls, candidate: EntityWrite, matches: Sequence[Tuple[EntityWrite, float]]) -> EntityMerge:
        group = [candidate, *(entity for entity, _score in matches)]
        canonical = cls._canonical(group)
        duplicates = [member.name for member in group if member.name != canonical.name]
        return EntityMerge(canonical=canonical, duplicate_names=duplicates)

    def _report(self, scope: Scope, candidates_in: int, merges: Sequence[EntityMerge]) -> EntityResolutionReport:
        return EntityResolutionReport(
            scope=scope,
            candidates=candidates_in,
            merged=sum(len(merge.duplicate_names) for merge in merges),
            groups=len(merges),
            details=resolution_details(resolver=RESOLVER_ID, min_score=self.min_score),
        )

    # -- the pass ---------------------------------------------------------------

    def run(self, scope: Scope, candidates: Sequence[EntityWrite]) -> EntityResolutionReport:
        """Resolve each candidate against the graph, merging one at a time.

        **One at a time, not batched at the end.** A candidate is matched against the graph's
        *current* state, which is why the merge for candidate N runs before candidate N+1 is
        matched: three spellings of one entity in the same pass -- "Atlas", "atlas", "Project
        Atlas" -- resolve because the second finds the first already merged and the third matches
        the (now two-alias) survivor of that, not because all three were compared to each other
        directly. Batching every merge to the end would only catch pairs the *original*, unmerged
        candidates happened to match.

        **A candidate already folded away by an earlier iteration is skipped, not re-matched.**
        Both members of one duplicate pair are typically in the same ``candidates`` batch -- they
        were extracted together -- and by the time the second is reached its node is already gone,
        merged into (or renamed by) the first's merge. Matching it anyway would find the survivor
        again on embedding similarity alone and issue a second, redundant ``merge_entities`` call
        for the pair this loop already resolved.
        """
        require_scope(scope, method="run")
        merges: List[EntityMerge] = []
        folded: Set[str] = set()
        attempted = 0
        for candidate in candidates:
            self._require_own_scope(scope, candidate)
            if candidate.name in folded:
                continue
            attempted += 1
            matches = self.graph.match_entities(scope, candidate, limit=self.match_limit, min_score=self.min_score)
            if not matches:
                continue
            merge = self._merge_for(candidate, matches)
            self.graph.merge_entities(scope, [merge])
            merges.append(merge)
            # `candidate.name` too, not only `merge.duplicate_names`: a repeated literal name in
            # `candidates` that turned out to win its own merge as canonical is not in that set,
            # and re-matching it finds nothing (its duplicate is already gone) but still issues a
            # redundant `match_entities` round trip and counts as a second "offered" candidate.
            folded.add(candidate.name)
            folded.update(merge.duplicate_names)
        return self._report(scope, attempted, merges)

    async def arun(self, scope: Scope, candidates: Sequence[EntityWrite]) -> EntityResolutionReport:
        """Async variant of :meth:`run`, making the same decisions through the same helpers."""
        require_scope(scope, method="arun")
        merges: List[EntityMerge] = []
        folded: Set[str] = set()
        attempted = 0
        for candidate in candidates:
            self._require_own_scope(scope, candidate)
            if candidate.name in folded:
                continue
            attempted += 1
            matches = await self.graph.amatch_entities(
                scope, candidate, limit=self.match_limit, min_score=self.min_score
            )
            if not matches:
                continue
            merge = self._merge_for(candidate, matches)
            await self.graph.amerge_entities(scope, [merge])
            merges.append(merge)
            folded.add(candidate.name)
            folded.update(merge.duplicate_names)
        return self._report(scope, attempted, merges)
