"""Relation/fact edge extraction from episodes into typed graph edges (SM-G3 #101).

Episodes in, :class:`~egpt.memory.types.FactWrite` triples out, landed on
:meth:`~egpt.memory.substrates.GraphSubstrate.upsert_facts`. Where SM-G2's entity extraction puts
mentions in the graph as **nodes**, this puts the connections between them in as **edges**: a
typed ``(source, relation, target)`` triple, provenanced back to the episode it was read from.

**Edges between entity names, not entity ids.** ``upsert_facts``'s triple leg
(``cypher.upsert_facts_statements``) ``MERGE``s its two endpoint nodes on ``(scope_key, name)`` --
the same identity :class:`~egpt.memory.graph.entities.EntityExtractionPipeline` writes its nodes
under. A relation whose ``source``/``target`` match an entity's ``name`` lands on that node; one
that does not creates a bare endpoint with nothing but a name, which is a legitimate outcome for a
pass that (deliberately, see below) does not require entity extraction to have already run.

**Not entity resolution.** Two spellings of one entity produce two edges to two different nodes,
exactly as two spellings produce two entity nodes in SM-G2 -- merging either is SM-G8's (#106).

**Driven by the consolidation lane, never the read path** -- ``MemoryProvider.consolidate`` today,
SM-L1's (#122) worker later, matching every other extraction pipeline in this package.

**Provenance is retained within one write, not accumulated across passes.**
``GraphSubstrate.upsert_facts``'s triple leg overwrites the edge's properties on re-write (it has
no analogue of ``upsert_entities``'s ``source_episode_ids`` union) -- stated in that method's own
docstring as "upsert replaces; only invalidate preserves a predecessor." A single pass folds
``source_episode_ids`` across every episode *in that pass* that names the same edge; carrying
provenance forward across passes needs a read of the edge's current row. SM-G7 (#105) added that
read (``GraphSubstrate.recall_facts``); wiring this pipeline to fold against it before writing is
not this ticket's to add, and nothing here does.

The prompt is the seed's, imported from ``learn/stores/entity_memory.py`` -- the same one SM-G2
reuses -- because it already documents relationships as part of what one extraction pass reads for
(the ``sarah_chen --[works_at]--> northstar_analytics`` example in its own text).
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from egpt.memory.graph.passes import select_current_episodes
from egpt.memory.substrates import GraphSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    FactWrite,
    MemoryOperationError,
    MemoryRecord,
    Scope,
    escape_key_slot,
    require_scope,
)

if TYPE_CHECKING:
    from egpt.models.base import Model
    from egpt.models.message import Message

__all__ = [
    "RELATION_EXTRACTOR_ID",
    "RELATION_KEYS",
    "ExtractedRelation",
    "ExtractedRelations",
    "LLMRelationExtractor",
    "RelationExtractionPipeline",
    "RelationExtractionReport",
    "RelationExtractor",
    "relation_details",
    "relation_edge_id_for",
]

#: Stamped on every candidate's ``derived_by``, so a row's producer is readable off the edge.
#: Named apart from entities.py's ``EXTRACTOR_ID`` -- both are re-exported from ``graph/__init__``,
#: and two module-level constants of that name would shadow each other there.
RELATION_EXTRACTOR_ID = "sm_g3.relation_extraction"

#: Same rationale and same default as ``entities.DEFAULT_MAX_EPISODES_PER_PASS``: one episode is
#: one model call, and an uncapped pass is a bill rather than a wait (SM-R9's ~$220/tenant/100k).
DEFAULT_MAX_EPISODES_PER_PASS = 200

#: Keys every :class:`RelationExtractionReport` carries.
RELATION_KEYS: Tuple[str, ...] = ("extractor", "complete")

#: The graph node id segment for a relation edge, distinguishing it from an entity node id
#: (``episodic._ENTITY_SEGMENT``) under the same scope key prefix.
_RELATION_SEGMENT = "relation"

#: `## How to answer` names a section ``ENTITY_EXTRACTION_INSTRUCTIONS`` does not itself use --
#: verified by inspection, not enforced -- so two identical headings never actually meet in the
#: combined prompt. If that seed prompt ever grows one of its own, this guard note is the reminder
#: to rename this section rather than let two headings of the same name reach one model call.
_RESPONSE_INSTRUCTION = (
    "\n## How to answer\n\n"
    "Return the relationships you extracted as structured data -- source/relation/target triples --"
    " not as a tool call. Return an empty list when the interaction names no relationship worth"
    " remembering; that is a valid answer, not a failure.\n\n"
    "`source` and `target` are entity display names as written in the interaction (e.g. "
    "'Sarah Chen'), matching what an entity extraction pass over the same interaction would call "
    "`name` -- not a slug and not the seed's `entity_id`. For example: Sarah Chen "
    "--[works_at]--> NorthStar Analytics, both endpoints exactly as the interaction names them, "
    "not lowercased or underscored. `relation` is the one part of the triple that IS a short, "
    "lowercase, underscored label for the connection (e.g. 'works_at').\n"
)


class ExtractedRelation(BaseModel):
    """One relation as the model reports it. Scope and provenance are the pipeline's to attach,
    matching :class:`~egpt.memory.graph.entities.ExtractedEntity`'s split."""

    # `min_length=1` rejects an empty string at the schema boundary. Without it, a model
    # answering with `source=""` passes validation here and reaches `relation_edge_id_for`
    # three frames deeper -- and if episodic facts already committed before that point, the
    # refusal there surfaces as a `PartialWriteError` with the real cause buried in its chain.
    # `min_length` alone still admits a whitespace-only string (`" "` has length 1), which
    # `_strip_and_check` below closes by stripping before the length check runs.
    source: str = Field(description="The relation's source entity, by display name", min_length=1)
    relation: str = Field(description="A short, lowercase, underscored label for the connection", min_length=1)
    target: str = Field(description="The relation's target entity, by display name", min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("source", "relation", "target", mode="before")
    @classmethod
    def _strip_and_check(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip()
        return value


class ExtractedRelations(BaseModel):
    """The response schema one extraction call returns."""

    relations: List[ExtractedRelation] = Field(default_factory=list)


@runtime_checkable
class RelationExtractor(Protocol):
    """Turns one episode into the relations it states.

    One episode per call, matching :class:`~egpt.memory.graph.entities.EntityExtractor`: an
    extractor handed several would have to attribute each relation back itself.
    """

    def extract(self, episode: MemoryRecord) -> Sequence[ExtractedRelation]:
        """The relations ``episode`` states. An episode naming none returns empty, not ``None``."""
        ...

    async def aextract(self, episode: MemoryRecord) -> Sequence[ExtractedRelation]:
        """Async variant of :meth:`extract`."""
        ...


class LLMRelationExtractor:
    """The default :class:`RelationExtractor`: the seed's prompt, answered as structured output.

    ``model`` is deep-copied per call, matching ``LLMEntityExtractor``, because ``response``
    mutates model state.
    """

    def __init__(self, model: "Model", instructions: Optional[str] = None) -> None:
        if model is None:
            raise MemoryOperationError(
                "LLMRelationExtractor needs a model; relation extraction is an LLM pass on the "
                "consolidation lane. Inject a different RelationExtractor to avoid one.",
                field_name="model",
            )
        self.model = model
        self.instructions = instructions

    def _messages(self, episode: MemoryRecord) -> List["Message"]:
        # Deferred for the same reason `LLMEntityExtractor._messages` defers this import: paid
        # only when a pass actually runs, not when the pipeline is merely imported.
        from egpt.learn.stores.entity_memory import ENTITY_EXTRACTION_INSTRUCTIONS
        from egpt.models.message import Message

        # Not a bug: the seed's entity prompt already documents relationships as part of one
        # extraction pass (its own `sarah_chen --[works_at]--> northstar_analytics` example) -- see
        # the module docstring. `_RESPONSE_INSTRUCTION` redirects the *output shape* to a triple
        # list instead of the seed's CRUD tools.
        system = ENTITY_EXTRACTION_INSTRUCTIONS + _RESPONSE_INSTRUCTION
        if self.instructions:
            system += f"\n## Additional Instructions\n\n{self.instructions}\n"
        return [
            Message(role="system", content=system),
            Message(role="user", content=f"Extract relationships from this interaction:\n\n{episode.content}"),
        ]

    @staticmethod
    def _parse(response: Any, episode_id: str) -> Sequence[ExtractedRelation]:
        """Read the structured answer, refusing anything else rather than returning empty.

        Mirrors ``LLMEntityExtractor._parse``: a model that answered in prose is not an episode
        with no relations, and reporting the two the same way makes a broken deployment look like
        a quiet corpus.
        """
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, ExtractedRelations):
            return parsed.relations
        content = parsed if isinstance(parsed, (str, bytes, bytearray)) else getattr(response, "content", None)
        if isinstance(content, (str, bytes, bytearray)):
            try:
                return ExtractedRelations.model_validate_json(content).relations
            except Exception as exc:
                raise MemoryOperationError(
                    f"The extraction model's answer for episode {episode_id!r} is not the requested "
                    f"schema ({exc}). An unreadable answer is not an episode with no relations.",
                    field_name="response",
                ) from exc
        raise MemoryOperationError(
            f"The extraction model returned no content for episode {episode_id!r}.",
            field_name="response",
        )

    def extract(self, episode: MemoryRecord) -> Sequence[ExtractedRelation]:
        response = deepcopy(self.model).response(messages=self._messages(episode), response_format=ExtractedRelations)
        return self._parse(response, episode.id)

    async def aextract(self, episode: MemoryRecord) -> Sequence[ExtractedRelation]:
        response = await deepcopy(self.model).aresponse(
            messages=self._messages(episode), response_format=ExtractedRelations
        )
        return self._parse(response, episode.id)


def relation_edge_id_for(scope: Scope, source: str, relation: str, target: str) -> str:
    """The id of the graph edge one ``(source, relation, target)`` triple addresses.

    Deterministic in the triple, not random: ``upsert_facts``'s triple leg merges on
    ``(scope_key, id, live)`` (``cypher._IDENTITY``), so a re-extraction of the same statement must
    produce the same id to upsert the existing edge rather than accumulate a duplicate one every
    pass. Mirrors :func:`~egpt.memory.graph.episodic.entity_node_id_for`'s reasoning, with three
    escaped slots instead of one so the source, relation, and target segments cannot collide across
    each other the way an unescaped join would.

    **All three arguments are stripped of leading/trailing whitespace before the id is derived.**
    ``relation_edge_id_for(scope, " Ada ", ...)`` and ``relation_edge_id_for(scope, "Ada", ...)``
    return the same id -- a caller that stores the un-stripped string and later queries by it would
    get a mismatch. ``ExtractedRelation._strip_and_check`` normalises on the LLM path; a caller
    building a :class:`RelationExtractor` by hand and calling this function directly must normalise
    the same way itself.
    """
    for name, value in (("source", source), ("relation", relation), ("target", target)):
        if not isinstance(value, str) or not value.strip():
            raise MemoryOperationError(
                f"A relation edge needs a non-empty {name}; got {value!r}.",
                field_name=name,
            )
    return (
        f"{scope.key()}/{_RELATION_SEGMENT}/"
        f"{escape_key_slot(source.strip())}/{escape_key_slot(relation.strip())}/{escape_key_slot(target.strip())}"
    )


def relation_details(
    extractor: Optional[str] = None,
    complete: bool = True,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a report payload carrying every key in :data:`RELATION_KEYS`. ``complete`` is what a
    scheduler needs: whether the pass reached the end of what it was given. Extra keys are free."""
    details: Dict[str, Any] = {
        "extractor": extractor,
        "complete": complete,
    }
    details.update(extra)
    return details


@dataclass
class RelationExtractionReport:
    """What one extraction pass read and wrote. ``candidates`` is what the extractor returned and
    ``edges_written`` what survived the same-triple merge, so folding several mentions of one
    relation into one edge reads as a fold rather than as losses."""

    scope: Scope
    episodes_read: int = 0
    candidates: int = 0
    edges_written: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.details.keys() >= set(RELATION_KEYS):
            self.details = relation_details(**self.details)


class RelationExtractionPipeline:
    """Episodes in, typed graph edges out -- on the consolidation lane (SM-G3 #101).

    Unlike ``EntityExtractionPipeline``, no embedder: a relation edge is not a candidate SM-G8
    (#106) matches over, so there is nothing here to embed.
    """

    def __init__(
        self,
        graph: GraphSubstrate,
        extractor: RelationExtractor,
        max_episodes_per_pass: int = DEFAULT_MAX_EPISODES_PER_PASS,
    ) -> None:
        for name, candidate, verbs in (
            ("graph", graph, ("upsert_facts", "aupsert_facts")),
            ("extractor", extractor, ("extract", "aextract")),
        ):
            missing = [verb for verb in verbs if not callable(getattr(candidate, verb, None))]
            if missing:
                raise MemoryOperationError(
                    f"RelationExtractionPipeline.{name} is a {type(candidate).__name__}, which is "
                    f"missing {missing}. Both halves are required: a pipeline whose async twin is "
                    "absent blocks the consolidation lane's event loop on the first pass.",
                    field_name=name,
                )
        if not isinstance(max_episodes_per_pass, int) or isinstance(max_episodes_per_pass, bool):
            raise MemoryOperationError(
                f"max_episodes_per_pass must be an integer, got {max_episodes_per_pass!r}.",
                field_name="max_episodes_per_pass",
            )
        if max_episodes_per_pass < 1:
            raise MemoryOperationError(
                f"max_episodes_per_pass is {max_episodes_per_pass}; a pass that reads no episode "
                "reports a clean run having done nothing. Omit the pipeline instead.",
                field_name="max_episodes_per_pass",
            )
        self.graph = graph
        self.extractor = extractor
        self.max_episodes_per_pass = max_episodes_per_pass

    # -- shared decisions -----------------------------------------------------

    def _selected(self, episodes: Sequence[MemoryRecord]) -> Tuple[List[MemoryRecord], bool]:
        return select_current_episodes(episodes, max_episodes_per_pass=self.max_episodes_per_pass, purpose="Relations")

    @staticmethod
    def _candidates(episode: MemoryRecord, extracted: Sequence[ExtractedRelation]) -> List[FactWrite]:
        """One :class:`FactWrite` per relation, with the episode's own scope and provenance."""
        if extracted is None:
            # The Protocol docstring says "an episode naming none returns empty, not None" -- a
            # contract, not a runtime guard. Without this, a custom extractor returning None hits
            # `for item in extracted` and raises a bare TypeError several frames from here, with
            # nothing pointing back at the extractor that violated the contract.
            raise MemoryOperationError(
                f"The extractor returned None for episode {episode.id!r}; return an empty "
                "sequence for an episode with no relations, not None.",
                field_name="extractor",
            )
        candidates: List[FactWrite] = []
        for item in extracted:
            # Already stripped and non-blank: `ExtractedRelation._strip_and_check` runs at
            # construction, so a redundant strip here would only restate what the schema already
            # guarantees. `relation_edge_id_for`'s own internal check stays as the boundary's last
            # line, for a caller that builds a `RelationExtractor` without going through pydantic.
            source, relation, target = item.source, item.relation, item.target
            edge_id = relation_edge_id_for(episode.scope, source, relation, target)
            record = MemoryRecord(
                id=edge_id,
                tier=MemoryTier.SEMANTIC,
                content=f"{source} {relation} {target}",
                scope=episode.scope,
            )
            candidates.append(
                FactWrite(
                    record=record,
                    subject=source,
                    predicate=relation,
                    object=target,
                    confidence=item.confidence,
                    source_episode_ids=[episode.id],
                    derived_by=RELATION_EXTRACTOR_ID,
                )
            )
        return candidates

    @staticmethod
    def _merged(candidates: Sequence[FactWrite]) -> List[FactWrite]:
        """Fold candidates sharing one edge id into one write, unioning their provenance.

        The head is the highest-confidence candidate, ties broken on content -- the same rule
        ``EntityExtractionPipeline._merged`` uses, so no uuid reaches an ordering path and one
        corpus produces one edge set rather than a different one per run.
        """
        groups: Dict[str, List[FactWrite]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.record.id, []).append(candidate)
        merged: List[FactWrite] = []
        for edge_id in sorted(groups):
            group = sorted(
                groups[edge_id],
                key=lambda f: (-f.confidence, f.subject or "", f.predicate or "", f.object or ""),
            )
            head = group[0]
            merged.append(
                FactWrite(
                    record=head.record,
                    subject=head.subject,
                    predicate=head.predicate,
                    object=head.object,
                    confidence=head.confidence,
                    # Deduped, order preserved -- the same rule `EntityWrite.__post_init__` applies
                    # to `source_episode_ids`, spelled here because `FactWrite` has no such guard.
                    source_episode_ids=list(
                        dict.fromkeys(eid for member in group for eid in member.source_episode_ids)
                    ),
                    derived_by=head.derived_by,
                )
            )
        return merged

    @staticmethod
    def _batches(facts: Sequence[FactWrite]) -> List[Tuple[Scope, List[FactWrite]]]:
        """Group by the fact's own scope, which is the scope it must be written under."""
        batched: Dict[Scope, List[FactWrite]] = {}
        for fact in facts:
            batched.setdefault(fact.record.scope, []).append(fact)
        return [(scope, batched[scope]) for scope in sorted(batched, key=lambda s: s.key())]

    def _report(
        self,
        scope: Scope,
        episodes_read: int,
        candidates: int,
        written: Sequence[FactWrite],
        complete: bool,
    ) -> RelationExtractionReport:
        return RelationExtractionReport(
            scope=scope,
            episodes_read=episodes_read,
            candidates=candidates,
            edges_written=len(written),
            details=relation_details(extractor=RELATION_EXTRACTOR_ID, complete=complete),
        )

    # -- the pass -------------------------------------------------------------

    def run(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> RelationExtractionReport:
        """Extract and write. Never called from a read -- see the module docstring."""
        require_scope(scope, method="run")
        selected, truncated = self._selected(episodes)
        candidates: List[FactWrite] = []
        for episode in selected:
            candidates.extend(self._candidates(episode, self.extractor.extract(episode)))
        merged = self._merged(candidates)
        for batch_scope, batch in self._batches(merged):
            self.graph.upsert_facts(batch_scope, batch)
        return self._report(scope, len(selected), len(candidates), merged, not truncated)

    async def arun(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> RelationExtractionReport:
        """Async variant of :meth:`run`, making the same decisions through the same helpers.

        Sequential rather than gathered, matching ``EntityExtractionPipeline.arun``: every episode
        is one model call, and fanning a capped pass out at once is how a consolidation lane
        exhausts a provider's rate limit.
        """
        require_scope(scope, method="arun")
        selected, truncated = self._selected(episodes)
        candidates: List[FactWrite] = []
        for episode in selected:
            candidates.extend(self._candidates(episode, await self.extractor.aextract(episode)))
        merged = self._merged(candidates)
        for batch_scope, batch in self._batches(merged):
            await self.graph.aupsert_facts(batch_scope, batch)
        return self._report(scope, len(selected), len(candidates), merged, not truncated)
