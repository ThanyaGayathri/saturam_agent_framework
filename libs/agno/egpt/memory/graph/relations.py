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

**Bi-temporal valid-time (SM-G4 #102).** Every candidate's ``t_valid``/``t_invalid`` come from
:class:`~egpt.memory.graph.temporal.ValidTimeExtractor`, run once per episode against its content
and reused across every relation that episode produced (they share the content they were dated
from). The four-timestamp envelope and its point-in-time query semantics are not this module's;
SM-G1 (#99) built both generically on :class:`~egpt.memory.types.MemoryRecord` and the Neo4j
substrate, which also stamps ``created_at``/``expired_at`` (the transaction axis) at write time --
this module's job is only ``t_valid``/``t_invalid`` (the valid-time axis), left unset before it.

**Contradiction -> invalidation, not delete (SM-G5 #103).** Before each scope's batch is written,
:meth:`RelationExtractionPipeline._contradictions_for` looks up the live edges sharing a
candidate's ``(subject, relation)`` through :meth:`~egpt.memory.substrates.GraphSubstrate.recall_facts`
-- a targeted lookup, not an enumeration, same as :mod:`~egpt.memory.graph.resolve`'s
``match_entities`` call. An existing edge naming a **different** ``object``, whose ``t_valid``
precedes the candidate's, is what this module calls contradicted: it is retired through
:meth:`~egpt.memory.substrates.GraphSubstrate.invalidate` (expire-and-reinsert, SM-R10's SCD-2)
**after** the new edge lands, never deleted and never mutated in place. The old row stays
queryable at any instant before the candidate's ``t_valid`` (ADR 0003 D7).

**Same-pass candidates are checked against each other too (SM-L5 #126).**
:meth:`RelationExtractionPipeline._contradicted_within` applies the identical rule --
same ``(subject, relation)``, different ``object``, earlier ``t_valid`` loses -- pairwise across
one ``run``/``arun`` call's own merged candidates, not only against what the graph already held
going in. Before SM-L5, two candidates naming the same subject+relation with different objects in
one pass both landed as live edges with neither retiring the other; ADR 0003's "graph is sole
authority for semantic contradiction" gives no exemption for same-pass writes, so leaving them
uncompared was a real gap, not a simplification.

**Every relation is treated as single-valued for this purpose.** A subject can only state one
current object per relation label; a second, later object supersedes the first. That is the right
call for a functional relation (``works_at``, ``lives_in``) and the wrong one for a genuinely
multi-valued relation (``knows``, ``friends_with``), where a second object is an addition, not a
correction -- this pass cannot tell the two apart from the triple alone. Distinguishing them needs
relation-type awareness that neither this module nor SM-L5 builds; both the existing-edge and
same-pass checks above treat every relation as functional. No ticket owns that distinction yet --
flagged here, not silently assumed away, until one does.

The prompt is the seed's, imported from ``learn/stores/entity_memory.py`` -- the same one SM-G2
reuses -- because it already documents relationships as part of what one extraction pass reads for
(the ``sarah_chen --[works_at]--> northstar_analytics`` example in its own text).
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Sequence, Set, Tuple, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from egpt.memory.graph.passes import select_current_episodes
from egpt.memory.graph.temporal import RuleBasedValidTimeExtractor, ValidTimeExtractor, ValidTimeSpan
from egpt.memory.metrics import meter_response_extraction_cost
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

#: Keys every :class:`RelationExtractionReport` carries. Not exhaustive: ``edges_invalidated``
#: (SM-L5 #126) is a known extra key ``_report`` always adds on top of this floor, read separately
#: by ``CoLocatedMemoryProvider._report`` -- see that key's own comment at the ``relation_details``
#: call site in :meth:`RelationExtractionPipeline._report`.
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
        model = deepcopy(self.model)
        response = model.response(messages=self._messages(episode), response_format=ExtractedRelations)
        meter_response_extraction_cost(episode.scope, model, response)
        return self._parse(response, episode.id)

    async def aextract(self, episode: MemoryRecord) -> Sequence[ExtractedRelation]:
        model = deepcopy(self.model)
        response = await model.aresponse(messages=self._messages(episode), response_format=ExtractedRelations)
        meter_response_extraction_cost(episode.scope, model, response)
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
    relation into one edge reads as a fold rather than as losses. ``edges_invalidated`` is how many
    existing edges this pass's candidates contradicted and retired (SM-G5 #103) -- distinct from
    ``edges_written``, since one retirement is a second write against an *existing* row's id, not
    one of the new ids counted there."""

    scope: Scope
    episodes_read: int = 0
    candidates: int = 0
    edges_written: int = 0
    edges_invalidated: int = 0
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
        valid_time_extractor: Optional[ValidTimeExtractor] = None,
    ) -> None:
        valid_time_extractor = (
            valid_time_extractor if valid_time_extractor is not None else RuleBasedValidTimeExtractor()
        )
        # Method-presence, not `isinstance(candidate, <Protocol>)`, deliberately: `GraphSubstrate`,
        # `RelationExtractor` and `ValidTimeExtractor` are all `@runtime_checkable`, but that only
        # verifies method *names* are present, not signatures -- `RelationExtractor` and
        # `ValidTimeExtractor` both declare `extract`/`aextract`, so an `isinstance` check against
        # either would pass for an instance of the other exactly as readily as this check does
        # (round-2 review, PR #101). Checking all three collaborators the same way keeps the loop's
        # symmetry meaningful; switching only one entry to `isinstance` would buy no additional
        # protection while making that entry's validation inconsistent with its two siblings here.
        for name, candidate, verbs in (
            (
                "graph",
                graph,
                ("upsert_facts", "aupsert_facts", "recall_facts", "arecall_facts", "invalidate", "ainvalidate"),
            ),
            ("extractor", extractor, ("extract", "aextract")),
            ("valid_time_extractor", valid_time_extractor, ("extract", "aextract")),
        ):
            missing = [verb for verb in verbs if not callable(getattr(candidate, verb, None))]
            if missing:
                raise MemoryOperationError(
                    f"RelationExtractionPipeline.{name} is a {type(candidate).__name__}, which is "
                    f"missing {missing}. Every method listed is required: a pipeline missing even "
                    "one -- sync or async -- either cannot make a decision this pipeline depends "
                    "on, or blocks the consolidation lane's event loop the first time it is asked "
                    "to await one that is not there.",
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
        self.valid_time_extractor = valid_time_extractor

    # -- shared decisions -----------------------------------------------------

    def _selected(self, episodes: Sequence[MemoryRecord]) -> Tuple[List[MemoryRecord], bool]:
        return select_current_episodes(episodes, max_episodes_per_pass=self.max_episodes_per_pass, purpose="Relations")

    @staticmethod
    def _ingest_time(episode: MemoryRecord) -> Optional[int]:
        """The instant a fallback span anchors on when its episode states no date of its own.

        ``episode.t_valid`` first -- when the episode is the seed's own occurrence time, that is
        the closest available answer to "when did this become true", closer than the pass's own
        wall clock, which drifts from it under async/batched consolidation (SM-L1 #122). Falls back
        to ``episode.created_at`` (when the episode was recorded) and then to ``None``, which
        :class:`~egpt.memory.graph.temporal.ValidTimeExtractor` reads as "use the current instant".
        """
        return episode.t_valid if episode.t_valid is not None else episode.created_at

    @staticmethod
    def _candidates(
        episode: MemoryRecord, extracted: Sequence[ExtractedRelation], span: ValidTimeSpan
    ) -> List[FactWrite]:
        """One :class:`FactWrite` per relation, with the episode's own scope, provenance, and
        valid-time span. ``span`` is shared across every relation in ``extracted`` -- they were all
        read from the same episode content, so they cannot disagree about when it dates them."""
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
                t_valid=span.t_valid,
                t_invalid=span.t_invalid,
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

    # -- contradiction -> invalidation (SM-G5 #103) ----------------------------

    @staticmethod
    def _by_relation(batch: Sequence[FactWrite]) -> List[Tuple[str, List[FactWrite]]]:
        """``batch``'s triple candidates, grouped by relation label -- one ``recall_facts`` round
        trip per relation, not per candidate. A candidate that is not a full triple carries nothing
        to look up a contradiction for and is dropped here.

        Checked field-by-field, not via ``fact.is_triple()``: the boolean tells a caller *whether*
        a fact is triple-shaped, but mypy cannot follow it back to narrow ``fact.predicate`` from
        ``Optional[str]`` to ``str`` for the ``setdefault`` key below -- the narrowing has to
        happen on the attribute itself.
        """
        grouped: Dict[str, List[FactWrite]] = {}
        for fact in batch:
            predicate = fact.predicate
            if predicate and fact.subject and fact.object:
                grouped.setdefault(predicate, []).append(fact)
        return [(relation, grouped[relation]) for relation in sorted(grouped)]

    @staticmethod
    def _subjects(facts: Sequence[FactWrite]) -> List[str]:
        """The distinct, non-empty subjects in ``facts``.

        The ``if fact.subject:`` guard below is not a second, redundant check against
        :meth:`_by_relation`'s own filter -- it is what lets mypy narrow ``fact.subject`` from
        ``Optional[str]`` to ``str`` for ``subjects.add``, the same reason :meth:`_by_relation`
        checks fields directly rather than trusting ``fact.is_triple()``. Every ``fact`` this
        method is actually called with already satisfies it by construction.
        """
        subjects: Set[str] = set()
        for fact in facts:
            if fact.subject:
                subjects.add(fact.subject)
        return sorted(subjects)

    @staticmethod
    def _contradicted(existing: Sequence[MemoryRecord], candidates: Sequence[FactWrite]) -> Dict[str, int]:
        """Existing live edges any of ``candidates`` supersedes -- ``{edge_id: t_invalid}``.

        A contradiction is the same subject naming a **different** object under a relation
        ``existing`` was already filtered to (see :meth:`_by_relation`) -- treating every relation
        as single-valued, per the module docstring's scope note. There is no separate "same edge"
        check: given that filter, an equal object is the only way ``rec.id`` could equal the
        candidate's, so excluding it already excludes the other. Only an edge whose own interval
        already ends by the candidate's ``t_valid``, or whose own ``t_valid`` is not strictly
        before it, is excluded: the first has nothing left to contradict, and the second is not
        older than the candidate, so retiring it would supersede a fact with one that does not
        postdate it.

        Two or more candidates contradicting the same existing edge keep the **earliest**
        ``t_valid`` -- that is the instant the edge actually stopped being current, not whichever
        candidate happened to be checked last.

        **An existing edge with no ``t_valid`` of its own is eligible.** ``rec.t_valid is None``
        is read as "always valid, since before any known instant" -- the same convention
        ``ValidWindow``/``MemoryRecord`` use everywhere else an unset lower bound appears -- so it
        is treated as older than any candidate rather than excluded. Correct given that reading,
        not exercised end-to-end here: reaching it through the pipeline needs a live edge whose
        ``t_valid`` was never populated, which ``RuleBasedValidTimeExtractor`` cannot produce
        (``ValidTimeSpan.t_valid`` is never ``None``) -- only a pre-SM-G4 row or a hand-built
        ``FactWrite`` would carry one.
        """
        found: Dict[str, int] = {}
        for candidate in candidates:
            if candidate.record.t_valid is None:
                continue
            for rec in existing:
                if (
                    rec.provenance.get("subject") != candidate.subject
                    or rec.provenance.get("object") == candidate.object
                    or (rec.t_valid is not None and rec.t_valid >= candidate.record.t_valid)
                    or (rec.t_invalid is not None and rec.t_invalid <= candidate.record.t_valid)
                ):
                    continue
                current = found.get(rec.id)
                if current is None or candidate.record.t_valid < current:
                    found[rec.id] = candidate.record.t_valid
        return found

    @staticmethod
    def _contradicted_within(candidates: Sequence[FactWrite]) -> Dict[str, int]:
        """Contradictions between two candidates in the **same** batch -- ``{edge_id: t_invalid}``,
        same shape as :meth:`_contradicted` (SM-L5 #126, see the module docstring).

        Same rule as :meth:`_contradicted`, applied pairwise across ``candidates`` instead of
        against ``existing``: same ``(subject, relation)``, a **different** ``object``, and the
        earlier ``t_valid`` loses to the later one. Both sides are new -- neither is in the graph
        yet -- so what a losing candidate contributes here is its own edge id, the one
        ``_write_batch`` is about to write via ``upsert_facts`` and then immediately retire, not an
        ``existing`` row's.

        ``candidates`` is assumed already merged (:meth:`_merged`): one entry per edge id, so no
        two entries can already share one, and every pair compared is genuinely two different
        edges. Also assumed to already share **one scope**: this method takes no ``scope``
        argument and applies no scope filter of its own, relying entirely on its one caller,
        :meth:`_write_batch`/:meth:`_awrite_batch`, to hand it a single-scope batch -- guaranteed
        today by :meth:`_batches` splitting a tenant-wide pass before either write method ever
        runs. A future caller passing a mixed-scope slice would silently produce cross-scope
        contradictions with no error raised. A tie (equal ``t_valid``) contradicts neither, matching
        :meth:`_contradicted`'s own
        reading of "starting at the exact same instant." Three or more candidates sharing one
        ``(subject, relation)`` are compared pairwise, not just adjacent-in-sort-order: a middle
        candidate can lose to a later one and still beat an earlier one, and each such pair is
        independent -- for each loser the accumulation below keeps the **earliest** ``t_invalid``
        seen across all pairings, equivalent to finding the earliest-starting winner among
        everything that beat this loser and using its own ``t_valid`` -- computed as a running
        minimum here so nothing needs to be sorted first, the same merge :meth:`_contradicted`
        already does for multiple contradictors of one edge.

        **O(n²) in ``len(candidates)``.** Acceptable at today's
        ``DEFAULT_MAX_EPISODES_PER_PASS`` and typical relation density -- negligible next to the
        LLM call latency each pass already pays -- but a grouped O(n) pass (bucket first by
        ``(subject, predicate)``, as :meth:`_by_relation` already does, then compare only within
        each bucket) would be worth it if pass sizes grow substantially past that default.
        """
        found: Dict[str, int] = {}
        for i in range(len(candidates)):
            a = candidates[i]
            a_t_valid = a.record.t_valid
            if a_t_valid is None or not a.is_triple():
                continue
            for j in range(i + 1, len(candidates)):
                b = candidates[j]
                b_t_valid = b.record.t_valid
                if (
                    b_t_valid is None
                    or not b.is_triple()
                    or a.subject != b.subject
                    or a.predicate != b.predicate
                    or a.object == b.object
                    or a_t_valid == b_t_valid
                ):
                    continue
                # Both `a_t_valid`/`b_t_valid` are typed local `int`s (not `Optional[int]`) by this
                # point, guarded above -- no assert needed to satisfy the type checker, and none of
                # the guards above can be weakened without this line failing to type-check first.
                loser, winner_t = (a, b_t_valid) if a_t_valid < b_t_valid else (b, a_t_valid)
                current = found.get(loser.record.id)
                if current is None or winner_t < current:
                    found[loser.record.id] = winner_t
        return found

    @staticmethod
    def _merge_contradictions(*sources: Dict[str, int]) -> Dict[str, int]:
        """Union several ``{edge_id: t_invalid}`` maps, keeping the **earliest** ``t_invalid`` per
        id -- the same "earliest wins" rule :meth:`_contradicted` applies within one source,
        extended across :meth:`_contradictions_for` and :meth:`_contradicted_within`'s independent
        results. The two never actually name the same id in practice (an id
        :meth:`_contradicted_within` can produce belongs to a candidate in this batch, never to an
        ``existing`` graph row), but merging rather than assuming disjointness costs nothing and
        does not depend on that invariant holding forever.
        """
        merged: Dict[str, int] = {}
        for source in sources:
            for edge_id, t_invalid in source.items():
                current = merged.get(edge_id)
                if current is None or t_invalid < current:
                    merged[edge_id] = t_invalid
        return merged

    def _contradictions_for(self, scope: Scope, batch: Sequence[FactWrite]) -> Dict[str, int]:
        """No ``until`` bound on the ``recall_facts`` call: it already filters to current belief
        (``expired_at IS NULL``) on the transaction axis, which is every row this batch could
        possibly need to retire. A ``t_invalid``-closed valid-time interval is a *different* axis --
        handled by :meth:`_contradicted`'s own ``rec.t_invalid`` check, not by narrowing this
        lookup.

        ``entities=self._subjects(facts)`` matches a fact edge whose subject **or** object is one
        of these names (``recall_facts``'s own contract) -- a superset of what this batch's
        candidates can actually contradict, since only the subject side is relevant here. An edge
        where one of these names appears only as the *object* is over-fetched and then dropped by
        :meth:`_contradicted`'s own ``subject != candidate.subject`` check. Correct and harmless
        (no narrower entity filter exists on the port), just wider than the eventual result.
        """
        contradictions: Dict[str, int] = {}
        for relation, facts in self._by_relation(batch):
            existing = self.graph.recall_facts(scope, entities=self._subjects(facts), relation=relation)
            contradictions.update(self._contradicted(existing, facts))
        return contradictions

    async def _acontradictions_for(self, scope: Scope, batch: Sequence[FactWrite]) -> Dict[str, int]:
        """Async variant of :meth:`_contradictions_for`; see its docstring for the missing
        ``until``."""
        contradictions: Dict[str, int] = {}
        for relation, facts in self._by_relation(batch):
            existing = await self.graph.arecall_facts(scope, entities=self._subjects(facts), relation=relation)
            contradictions.update(self._contradicted(existing, facts))
        return contradictions

    def _write_batch(self, scope: Scope, batch: Sequence[FactWrite]) -> int:
        """Write one scope's batch, then retire whatever it supersedes.

        Contradiction is looked up **before** the write, from two independent sources merged by
        :meth:`_merge_contradictions`: :meth:`_contradictions_for` against the graph's state as it
        stood before this batch (a candidate is never compared against **itself** there -- the
        edge it would eventually become does not exist yet), and :meth:`_contradicted_within`
        (SM-L5 #126) pairwise across the batch's own candidates against **each other**, which by
        construction never compares an entry against itself either (``i < j`` in its own loop).
        Invalidation runs **after** ``upsert_facts`` lands, not before: a caller retrying a failed
        write must not find edges already retired for candidates that never reached the graph.

        **Assumes one writer per (scope, subject, relation) at a time.** Two concurrent passes
        contradicting the same existing edge each compute their own contradiction set against the
        graph's state as they separately read it -- neither sees the other's not-yet-committed
        write -- so both can call ``invalidate`` on the same edge. The backend's own concurrency
        contract decides what that means (the Neo4j backend's single transaction per invalidation
        makes it safe: the second call still expire-and-reinserts, just against whatever the first
        left as live). What this method does not do is detect or serialize against a concurrent
        writer itself -- consolidation runs single-threaded per scope today, so this has not been a
        live concern, but it is worth stating rather than leaving implicit.
        """
        contradictions = self._merge_contradictions(
            self._contradictions_for(scope, batch), self._contradicted_within(batch)
        )
        self.graph.upsert_facts(scope, batch)
        # Sorted for deterministic call order across runs (matches this module's own convention
        # elsewhere, e.g. `_batches`) -- not for correctness: each `invalidate` call targets a
        # distinct edge id, so the order among them has no effect on the outcome.
        for edge_id, t_invalid in sorted(contradictions.items()):
            self.graph.invalidate(scope, edge_id, t_invalid)
        return len(contradictions)

    async def _awrite_batch(self, scope: Scope, batch: Sequence[FactWrite]) -> int:
        """Async variant of :meth:`_write_batch`, making the same decisions in the same order."""
        contradictions = self._merge_contradictions(
            await self._acontradictions_for(scope, batch), self._contradicted_within(batch)
        )
        await self.graph.aupsert_facts(scope, batch)
        for edge_id, t_invalid in sorted(contradictions.items()):
            await self.graph.ainvalidate(scope, edge_id, t_invalid)
        return len(contradictions)

    def _report(
        self,
        scope: Scope,
        episodes_read: int,
        candidates: int,
        written: Sequence[FactWrite],
        complete: bool,
        invalidated: int,
    ) -> RelationExtractionReport:
        return RelationExtractionReport(
            scope=scope,
            episodes_read=episodes_read,
            candidates=candidates,
            edges_written=len(written),
            edges_invalidated=invalidated,
            # `edges_invalidated` repeated inside `details`, not only as the report's own field:
            # `CoLocatedMemoryProvider._relations`/`_arelations` (SM-L5 #126) return only
            # `.details` to `consolidate`/`aconsolidate`, discarding the typed report and this
            # field along with it -- which is why `ConsolidationReport.contradictions_resolved`
            # stayed hard-coded to 0 even once this pipeline was wired and invalidating edges.
            # An extra key, not a `RELATION_KEYS` floor member (see `RelationSummary`'s docstring
            # in `co_located.py`): promoting it to the floor is a separate decision this ticket
            # does not make.
            details=relation_details(extractor=RELATION_EXTRACTOR_ID, complete=complete, edges_invalidated=invalidated),
        )

    # -- the pass -------------------------------------------------------------

    def run(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> RelationExtractionReport:
        """Extract and write. Never called from a read -- see the module docstring."""
        require_scope(scope, method="run")
        selected, truncated = self._selected(episodes)
        candidates: List[FactWrite] = []
        for episode in selected:
            span = self.valid_time_extractor.extract(episode.content, ingest_time=self._ingest_time(episode))
            candidates.extend(self._candidates(episode, self.extractor.extract(episode), span))
        merged = self._merged(candidates)
        invalidated = 0
        for batch_scope, batch in self._batches(merged):
            invalidated += self._write_batch(batch_scope, batch)
        return self._report(scope, len(selected), len(candidates), merged, not truncated, invalidated)

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
            span = await self.valid_time_extractor.aextract(episode.content, ingest_time=self._ingest_time(episode))
            candidates.extend(self._candidates(episode, await self.extractor.aextract(episode), span))
        merged = self._merged(candidates)
        invalidated = 0
        for batch_scope, batch in self._batches(merged):
            invalidated += await self._awrite_batch(batch_scope, batch)
        return self._report(scope, len(selected), len(candidates), merged, not truncated, invalidated)
