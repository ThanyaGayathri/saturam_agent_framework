"""Entity extraction from episodes into temporal-graph nodes (SM-G2 #100).

Episodes in, embedded :class:`~egpt.memory.types.EntityWrite` candidates out, landed on
:meth:`~egpt.memory.substrates.GraphSubstrate.upsert_entities`.

Three boundaries, each with a different owner:

* **Candidates, not resolved entities.** Two spellings of one entity stay two nodes; merging them
  is SM-G8 (#106), which matches over what this produces.
* **Nodes, not edges.** A typed relation between entities is SM-G3 (#101).
* **Driven by the consolidation lane, never the read path** -- ``MemoryProvider.consolidate``
  today, SM-L1's (#122) worker later.

The prompt is the seed's, imported from ``learn/stores/entity_memory.py``: two texts for one job
drift, and an entity the learn store keeps while the graph does not is a node missing from every
multi-hop path.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from pydantic import BaseModel, Field

from egpt.memory.substrates import GraphSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    EntityWrite,
    MemoryOperationError,
    MemoryRecord,
    Scope,
    require_scope,
)

if TYPE_CHECKING:
    from egpt.knowledge.embedder import Embedder
    from egpt.models.base import Model
    from egpt.models.message import Message

__all__ = [
    "ENTITY_KEYS",
    "EXTRACTOR_ID",
    "EntityExtractionPipeline",
    "EntityExtractionReport",
    "EntityExtractor",
    "ExtractedEntities",
    "ExtractedEntity",
    "LLMEntityExtractor",
    "embedding_text",
    "entity_details",
]

#: Stamped on every candidate's ``derived_by``, so a row's producer is readable off the node.
EXTRACTOR_ID = "sm_g2.entity_extraction"

#: How many episodes one pass extracts from. Each is one model call and SM-R9 sizes graph
#: extraction at ~$220/tenant per 100k memories, so an uncapped pass is a bill rather than a wait.
#: The tail is re-read rather than resumed until SM-L1 (#122) brings a cursor.
DEFAULT_MAX_EPISODES_PER_PASS = 200

#: Keys every :class:`EntityExtractionReport` carries, the floor
#: :data:`~egpt.memory.types.CONSOLIDATION_KEYS` provides for the pass that drives this one.
ENTITY_KEYS: Tuple[str, ...] = ("extractor", "embedding_version", "complete")

#: Appended to the shared prompt. The seed store gives the model CRUD tools and lets it save; here
#: the pipeline owns the write, so the model is asked for the entities instead.
_RESPONSE_INSTRUCTION = (
    "\n## How to answer\n\n"
    "Return the entities you extracted as structured data. Return an empty list when the "
    "interaction names no entity worth remembering -- that is a valid answer, not a failure.\n\n"
    "Note: the shared prompt above describes `entity_id` as a lowercase-underscored identifier "
    "(e.g. 'acme_corp'). This schema has no such field -- `name` is the display name as written "
    "in the interaction (e.g. 'Acme Corp'), not a slug; the normalised grouping key is derived "
    "downstream and is not yours to produce.\n"
)


class ExtractedEntity(BaseModel):
    """One entity as the model reports it. Scope, provenance and embedding are the pipeline's to
    attach: an extractor reporting its own scope would be echoing the one it was handed."""

    name: str = Field(description="The entity's display name, as written in the interaction")
    entity_type: str = Field(description="person, company, project, product, system, or concept")
    description: Optional[str] = Field(default=None, description="Brief description of the entity")
    aliases: List[str] = Field(default_factory=list, description="Other names the interaction used")
    properties: Dict[str, str] = Field(default_factory=dict, description="Key-value attributes")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedEntities(BaseModel):
    """The response schema one extraction call returns."""

    entities: List[ExtractedEntity] = Field(default_factory=list)


@runtime_checkable
class EntityExtractor(Protocol):
    """Turns one episode into the entities it mentions.

    One episode per call, not a batch: an extractor handed several would have to attribute each
    entity back itself, and SM-R7 section 7 makes that trail load-bearing.
    """

    def extract(self, episode: MemoryRecord) -> Sequence[ExtractedEntity]:
        """The entities ``episode`` mentions. An episode naming none returns empty, not ``None``."""
        ...

    async def aextract(self, episode: MemoryRecord) -> Sequence[ExtractedEntity]:
        """Async variant of :meth:`extract`."""
        ...


class LLMEntityExtractor:
    """The default :class:`EntityExtractor`: the seed's prompt, answered as structured output.

    ``model`` is deep-copied per call, as ``EntityMemoryStore`` does, because ``response`` mutates
    model state. ``instructions`` is appended, matching ``EntityMemoryConfig.instructions``.
    """

    def __init__(self, model: "Model", instructions: Optional[str] = None) -> None:
        if model is None:
            raise MemoryOperationError(
                "LLMEntityExtractor needs a model; entity extraction is an LLM pass on the "
                "consolidation lane. Inject a different EntityExtractor to avoid one.",
                field_name="model",
            )
        self.model = model
        self.instructions = instructions

    def _messages(self, episode: MemoryRecord) -> List["Message"]:
        # Both deferred, matching the pattern `memory/adapters/seed.py` already uses for a
        # cross-service reference (its `LearningStore` import at line 1180): `egpt.learn`'s own
        # `__init__` eagerly imports its full stack (`LearningMachine`, every store), so a
        # module-level import here would drag all of it into every `import egpt.memory.graph` --
        # paid only when a pass actually runs, not when the pipeline is merely imported.
        from egpt.learn.stores.entity_memory import ENTITY_EXTRACTION_INSTRUCTIONS
        from egpt.models.message import Message

        system = ENTITY_EXTRACTION_INSTRUCTIONS + _RESPONSE_INSTRUCTION
        if self.instructions:
            system += f"\n## Additional Instructions\n\n{self.instructions}\n"
        return [
            Message(role="system", content=system),
            Message(role="user", content=f"Extract entities from this interaction:\n\n{episode.content}"),
        ]

    @staticmethod
    def _parse(response: Any, episode_id: str) -> Sequence[ExtractedEntity]:
        """Read the structured answer, refusing anything else rather than returning empty.

        A model that answered in prose is not an episode with no entities, and reporting the two the
        same way makes a broken deployment look like a quiet corpus.
        """
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, ExtractedEntities):
            return parsed.entities
        # A provider that fills `parsed` with the raw JSON is as common as one that leaves it None
        # and fills `content`; both are the requested schema, unparsed.
        content = parsed if isinstance(parsed, (str, bytes, bytearray)) else getattr(response, "content", None)
        if isinstance(content, (str, bytes, bytearray)):
            try:
                return ExtractedEntities.model_validate_json(content).entities
            except Exception as exc:
                raise MemoryOperationError(
                    f"The extraction model's answer for episode {episode_id!r} is not the requested "
                    f"schema ({exc}). An unreadable answer is not an episode with no entities.",
                    field_name="response",
                ) from exc
        raise MemoryOperationError(
            f"The extraction model returned no content for episode {episode_id!r}.",
            field_name="response",
        )

    def extract(self, episode: MemoryRecord) -> Sequence[ExtractedEntity]:
        response = deepcopy(self.model).response(messages=self._messages(episode), response_format=ExtractedEntities)
        return self._parse(response, episode.id)

    async def aextract(self, episode: MemoryRecord) -> Sequence[ExtractedEntity]:
        response = await deepcopy(self.model).aresponse(
            messages=self._messages(episode), response_format=ExtractedEntities
        )
        return self._parse(response, episode.id)


def embedding_text(entity: EntityWrite) -> str:
    """What an entity is embedded as. More than the name, because SM-G8 (#106) matches what
    :func:`~egpt.memory.types.normalize_entity_name` did not collapse -- and a bare name puts
    "atlas" the project and "atlas" the person at cosine 1.0."""
    parts = [entity.name, entity.entity_type]
    if entity.description:
        parts.append(entity.description)
    return " | ".join(parts)


def entity_details(
    extractor: Optional[str] = None,
    embedding_version: Optional[str] = None,
    complete: bool = True,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a report payload carrying every key in :data:`ENTITY_KEYS`. ``complete`` is what a
    scheduler needs: whether the pass reached the end of what it was given. Extra keys are free."""
    details: Dict[str, Any] = {
        "extractor": extractor,
        "embedding_version": embedding_version,
        "complete": complete,
    }
    details.update(extra)
    return details


@dataclass
class EntityExtractionReport:
    """What one extraction pass read and wrote. ``candidates`` is what the extractor returned and
    ``entities_written`` what survived the same-name merge, so folding ten mentions into one entity
    reads as a fold rather than as nine losses."""

    scope: Scope
    episodes_read: int = 0
    candidates: int = 0
    entities_written: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.details.keys() >= set(ENTITY_KEYS):
            self.details = entity_details(**self.details)


class EntityExtractionPipeline:
    """Episodes in, embedded entity nodes out -- on the consolidation lane (SM-G2 #100).

    ``embedder`` is required rather than optional: "dedup-ready" is what SM-G8 (#106) consumes, and
    an unembedded candidate is not. ``embedding_version`` is derived from the embedder when unset;
    the canonical ``model/dim/metric/rev`` spelling is SM-V2's (#96).
    """

    def __init__(
        self,
        graph: GraphSubstrate,
        extractor: EntityExtractor,
        embedder: "Embedder",
        embedding_version: Optional[str] = None,
        max_episodes_per_pass: int = DEFAULT_MAX_EPISODES_PER_PASS,
    ) -> None:
        # One loop over all three, so a deployment missing a leg fails when it is built rather than
        # on the first pass that happened to find an episode -- and so a collaborator added later
        # cannot be the one nobody remembered to check.
        for name, candidate, verbs in (
            ("graph", graph, ("upsert_entities", "aupsert_entities")),
            ("extractor", extractor, ("extract", "aextract")),
            ("embedder", embedder, ("get_embedding", "async_get_embedding")),
        ):
            missing = [verb for verb in verbs if not callable(getattr(candidate, verb, None))]
            if missing:
                raise MemoryOperationError(
                    f"EntityExtractionPipeline.{name} is a {type(candidate).__name__}, which is "
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
            # Zero would be a pipeline that reports success having read nothing, which is the
            # silently-empty class this package refuses everywhere else.
            raise MemoryOperationError(
                f"max_episodes_per_pass is {max_episodes_per_pass}; a pass that reads no episode "
                "reports a clean run having done nothing. Omit the pipeline instead.",
                field_name="max_episodes_per_pass",
            )
        self.graph = graph
        self.extractor = extractor
        self.embedder = embedder
        self.embedding_version = embedding_version
        self.max_episodes_per_pass = max_episodes_per_pass

    # -- shared decisions -----------------------------------------------------

    def _selected(self, episodes: Sequence[MemoryRecord]) -> Tuple[List[MemoryRecord], bool]:
        """The episodes this pass extracts from, and whether it left any **unread**.

        Unread, not skipped: a superseded row was examined and passed over, so reporting the pass
        incomplete for one would tell a scheduler to re-run a pass with nothing left to do.
        """
        chosen: List[MemoryRecord] = []
        for index, episode in enumerate(episodes):
            if episode.tier is not MemoryTier.EPISODIC:
                raise MemoryOperationError(
                    f"Episode {episode.id!r} is {episode.tier.value!r}. Entities are extracted from "
                    f"the {MemoryTier.EPISODIC.value} tier; a semantic fact is already distilled, "
                    "and re-extracting one would attribute its entities to a derived row.",
                    field_name="tier",
                )
            # A superseded episode's entities belong to its live revision. Extracting from it would
            # attribute them to a row no read returns.
            if episode.is_current_belief():
                chosen.append(episode)
            if len(chosen) == self.max_episodes_per_pass:
                return chosen, index + 1 < len(episodes)
        return chosen, False

    @staticmethod
    def _candidates(episode: MemoryRecord, extracted: Sequence[ExtractedEntity]) -> List[EntityWrite]:
        """Attach the scope and the provenance the extractor deliberately does not carry."""
        return [
            EntityWrite(
                name=item.name,
                entity_type=item.entity_type,
                # The **episode's** scope, not the pass's. A pass scoped to a tenant legitimately
                # scans every user's episodes, and filing one user's entity tenant-wide is the
                # cross-user leak `co_located._by_scope` exists to prevent one tier up.
                scope=episode.scope,
                description=item.description,
                aliases=list(item.aliases),
                properties=dict(item.properties),
                source_episode_ids=[episode.id],
                confidence=item.confidence,
                derived_by=EXTRACTOR_ID,
            )
            for item in extracted
        ]

    @staticmethod
    def _merged(candidates: Sequence[EntityWrite]) -> List[EntityWrite]:
        """Fold candidates sharing a ``(scope, name)`` into one node's worth of write.

        The head is the highest-confidence candidate, ties broken on content: no uuid reaches an
        ordering path, so one corpus produces one description rather than a different one per run.
        Candidates sharing a normalised ``key`` but not a ``name`` are **left apart** -- merging
        those is SM-G8's (#106).
        """
        groups: Dict[Tuple[str, str], List[EntityWrite]] = {}
        for candidate in candidates:
            groups.setdefault((candidate.scope.key(), candidate.name), []).append(candidate)
        merged = []
        for group_key in sorted(groups):
            group = sorted(
                groups[group_key],
                key=lambda c: (-c.confidence, c.entity_type, c.description or "", tuple(c.source_episode_ids)),
            )
            head = group[0]
            merged.append(
                EntityWrite(
                    name=head.name,
                    entity_type=head.entity_type,
                    scope=head.scope,
                    description=head.description,
                    # Duplicates across members (and the entity's own name) are deduped and sorted
                    # by `EntityWrite.__post_init__`, not here -- the same rule as `source_episode_ids`.
                    aliases=[alias for member in group for alias in member.aliases],
                    properties=head.properties,
                    source_episode_ids=[eid for member in group for eid in member.source_episode_ids],
                    confidence=head.confidence,
                    derived_by=head.derived_by,
                )
            )
        return merged

    def _embedded(self, entity: EntityWrite, vector: Sequence[float], expected_dim: Optional[int]) -> EntityWrite:
        """A copy carrying ``vector``, refusing a dimension that disagrees with the pass's: mixed
        dimensions in one column family cannot be searched together, and left alone the failure
        surfaces at SM-G8's (#106) match rather than at the write that caused it."""
        values = list(vector)
        if expected_dim is not None and len(values) != expected_dim:
            raise MemoryOperationError(
                f"The embedder returned {len(values)} dimensions for entity {entity.name!r} and "
                f"{expected_dim} for an earlier one in the same pass. One embedder produces one "
                "dimension; two in a column family cannot be searched together.",
                field_name="embedding",
            )
        return entity.with_embedding(values, self._embedding_version(len(values)))

    def _embedding_version(self, dim: int) -> str:
        """Derived from the embedder when the caller named none, so no node carries a vector with
        no record of what produced it. ``model/dim/metric/rev`` is SM-V2's (#96); this is the
        honest subset available here."""
        if self.embedding_version is not None:
            return self.embedding_version
        return f"{getattr(self.embedder, 'id', type(self.embedder).__name__)}/d{dim}"

    @staticmethod
    def _batches(entities: Sequence[EntityWrite]) -> List[Tuple[Scope, List[EntityWrite]]]:
        """Group by the entity's own scope, which is the scope it must be written under."""
        batched: Dict[Scope, List[EntityWrite]] = {}
        for entity in entities:
            batched.setdefault(entity.scope, []).append(entity)
        return [(scope, batched[scope]) for scope in sorted(batched, key=lambda s: s.key())]

    def _report(
        self,
        scope: Scope,
        episodes_read: int,
        candidates: int,
        written: Sequence[EntityWrite],
        complete: bool,
    ) -> EntityExtractionReport:
        return EntityExtractionReport(
            scope=scope,
            episodes_read=episodes_read,
            candidates=candidates,
            entities_written=len(written),
            details=entity_details(
                extractor=EXTRACTOR_ID,
                embedding_version=written[0].embedding_version if written else None,
                complete=complete,
            ),
        )

    # -- the pass -------------------------------------------------------------

    def run(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> EntityExtractionReport:
        """Extract, embed, and write. Never called from a read -- see the module docstring."""
        require_scope(scope, method="run")
        selected, truncated = self._selected(episodes)
        candidates: List[EntityWrite] = []
        for episode in selected:
            candidates.extend(self._candidates(episode, self.extractor.extract(episode)))
        embedded: List[EntityWrite] = []
        dim: Optional[int] = None
        for entity in self._merged(candidates):
            ready = self._embedded(entity, self.embedder.get_embedding(embedding_text(entity)), dim)
            if ready.embedding is None:
                raise MemoryOperationError(
                    f"_embedded returned {ready.name!r} with no embedding; with_embedding "
                    "always sets one, so this names a bug in this method rather than bad input.",
                    field_name="embedding",
                )
            dim = len(ready.embedding)
            embedded.append(ready)
        for batch_scope, batch in self._batches(embedded):
            self.graph.upsert_entities(batch_scope, batch)
        return self._report(scope, len(selected), len(candidates), embedded, not truncated)

    async def arun(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> EntityExtractionReport:
        """Async variant of :meth:`run`, making the same decisions through the same helpers.

        Sequential rather than gathered: every episode is one model call, and fanning a capped pass
        out at once is how a consolidation lane exhausts a provider's rate limit. Bounded
        concurrency belongs with the scheduler that knows the budget, which is SM-L1's (#122).
        """
        require_scope(scope, method="arun")
        selected, truncated = self._selected(episodes)
        candidates: List[EntityWrite] = []
        for episode in selected:
            candidates.extend(self._candidates(episode, await self.extractor.aextract(episode)))
        embedded: List[EntityWrite] = []
        dim: Optional[int] = None
        for entity in self._merged(candidates):
            ready = self._embedded(entity, await self.embedder.async_get_embedding(embedding_text(entity)), dim)
            if ready.embedding is None:
                raise MemoryOperationError(
                    f"_embedded returned {ready.name!r} with no embedding; with_embedding "
                    "always sets one, so this names a bug in this method rather than bad input.",
                    field_name="embedding",
                )
            dim = len(ready.embedding)
            embedded.append(ready)
        for batch_scope, batch in self._batches(embedded):
            await self.graph.aupsert_entities(batch_scope, batch)
        return self._report(scope, len(selected), len(candidates), embedded, not truncated)
