"""Fact extraction + merge decision, on the consolidation lane (SM-L3 #124).

Promotes the mem0-style strategy SM-R7's research spike (``benchmarks/memory_consolidation``)
recommended: extract atomic candidate facts from a scope's episodes, retrieve the most similar
*existing* semantic facts for each, and let the model choose ADD / UPDATE / DELETE / NOOP.

``CoLocatedMemoryProvider._distil`` (SM-C1 #89) is the zero-config default -- content-overlap
grouping, no comparison against what is already stored, and by construction unable to retire what
it subsumes (its own docstring: "a property of the current strategy, which SM-L3 replaces").
Retirement needs a strategy that knows what one logical fact's *membership* is, which only a
compare-and-decide pass can answer; :class:`FactExtractionPipeline` is that pass, wired in the same
opt-in shape as ``entity_pipeline`` / ``relation_pipeline`` (``consolidate`` runs it only when a
provider is handed one).

**Retirement is transaction time, never valid time.** A superseded fact did not become false; a
better-sourced row replaced it. UPDATE and DELETE therefore stamp ``expired_at`` +
``superseded_by`` on the row they retire and leave ``t_valid`` / ``t_invalid`` untouched --
:class:`~egpt.memory.substrates.GraphSubstrate`'s ``upsert_facts`` docstring states this is the
obligation a fact write carries, and the retirement copy here is its first producer.

Neighbours are referenced by index in the merge prompt, never by id: an id embedded in the prompt
makes the prompt -- and any record/replay cache keyed on it -- depend on a randomly generated
string, which is exactly what broke reproducibility in the research harness this promotes from.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)
from uuid import NAMESPACE_OID, uuid5

from pydantic import BaseModel, Field

from egpt.memory.graph.passes import select_current_episodes
from egpt.memory.substrates import VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    FactWrite,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    Scope,
    require_scope,
)
from egpt.utils.dttm import now_epoch_s

if TYPE_CHECKING:
    from egpt.models.base import Model
    from egpt.models.message import Message

__all__ = [
    "STRATEGY_ID",
    "ExtractedFacts",
    "FactCandidate",
    "FactExtractionPipeline",
    "FactExtractionReport",
    "FactExtractor",
    "LLMFactExtractor",
    "LLMMergeDecider",
    "MergeDecider",
    "MergeDecision",
    "fact_extraction_details",
]

#: Stamped on every fact this pipeline writes, so a row's producer is readable off it -- the
#: ``supersede`` value :class:`FactExtractionPipeline` reports, replacing
#: :data:`~egpt.memory.types.NO_SUPERSEDE`.
STRATEGY_ID = "sm_l3.mem0_merge"

#: How many episodes one pass extracts from. Mirrors ``EntityExtractionPipeline``'s own cap
#: (SM-G2 #100): a second, independent bound from the provider's ``max_episodes_per_pass``, so a
#: capped-but-not-drained extraction is distinguishable from one that ran clean.
DEFAULT_MAX_EPISODES_PER_PASS = 200

#: How many existing facts a candidate is compared against. Fixed rather than unbounded: every
#: neighbour is one more line in the merge prompt, and a candidate compared against the whole
#: store rather than its nearest matches is a token bill, not a better decision.
DEFAULT_TOP_K = 5

_FACT_NAMESPACE = uuid5(NAMESPACE_OID, "egpt.memory.consolidation.extract.fact")

_RESPONSE_INSTRUCTION = (
    "\n## How to answer\n\nReturn the facts you extracted as structured data. Return an empty "
    "list when the episodes state nothing durable -- that is a valid answer, not a failure.\n"
)

_EXTRACT_SYSTEM = (
    "You extract durable, atomic facts about a user from episodic memories (things the user did "
    "or said).\n\n"
    "Rules:\n"
    "- One fact per idea; keep them atomic. Split compound statements.\n"
    "- Only facts grounded in the episodes shown. Do not invent or generalize wildly.\n"
    "- Skip one-off trivia and generic world knowledge.\n"
    "- Capture preferences, traits, goals, relationships, stable circumstances, and explicit "
    'changes ("no longer ...", "switched to ...").\n'
    "- Cite the 0-based indices of every episode below that supports the fact.\n" + _RESPONSE_INSTRUCTION
)

_MERGE_SYSTEM = (
    "You maintain a user's semantic memory. Given a NEW candidate fact and the most similar "
    "EXISTING facts (each shown with a number), choose exactly one operation:\n\n"
    "- ADD: the candidate is net-new information.\n"
    "- UPDATE: the candidate refines, augments, or replaces an existing fact (a changed "
    "preference, a new job, a switched habit). Name the target number and give the final "
    "statement to store.\n"
    "- DELETE: the candidate says an existing fact is no longer true or should be forgotten. "
    "Name the target number.\n"
    "- NOOP: the candidate is already fully captured. Optionally name the target number of the "
    "fact that already covers it.\n\n"
    "A changed preference or circumstance is an UPDATE of the existing fact, not an ADD of a "
    "second, contradictory one. Prefer NOOP/UPDATE over ADD when an existing fact covers the "
    "same topic.\n"
)


class FactCandidate(BaseModel):
    """One candidate fact as the extractor reports it, grounded in the episodes it was handed."""

    statement: str = Field(description="A brief, atomic, third-person statement of the fact")
    source_indices: List[int] = Field(
        default_factory=list, description="0-based indices of the episodes (as shown) that support this fact"
    )
    topics: Optional[List[str]] = Field(default=None, description="Topic tags for the fact, if any apply")


class ExtractedFacts(BaseModel):
    """The response schema one extraction call returns."""

    facts: List[FactCandidate] = Field(default_factory=list)


class MergeDecision(BaseModel):
    """The response schema one merge-decision call returns.

    ``target_index`` indexes the numbered EXISTING facts shown in the prompt, resolved to a real
    id by :class:`FactExtractionPipeline` -- never asked of the model directly (module docstring).
    """

    op: Literal["ADD", "UPDATE", "DELETE", "NOOP"]
    target_index: Optional[int] = None
    statement: str


@runtime_checkable
class FactExtractor(Protocol):
    """Turns one scope's episodes into candidate facts.

    All episodes in one call, unlike :class:`~egpt.memory.graph.entities.EntityExtractor`'s
    one-episode-at-a-time shape: an atomic fact routinely draws on more than one episode (a
    preference stated once, confirmed later), and per-episode extraction has no way to cite both.
    """

    def extract(self, episodes: Sequence[MemoryRecord]) -> Sequence[FactCandidate]:
        """Candidates grounded in ``episodes``. Episodes stating nothing durable return empty."""
        ...

    async def aextract(self, episodes: Sequence[MemoryRecord]) -> Sequence[FactCandidate]:
        """Async variant of :meth:`extract`."""
        ...


@runtime_checkable
class MergeDecider(Protocol):
    """Chooses ADD / UPDATE / DELETE / NOOP for one candidate against its nearest existing facts."""

    def decide(self, statement: str, neighbors: Sequence[MemoryRecord]) -> MergeDecision:
        """``neighbors`` is already the top-k for ``statement`` -- retrieval is the pipeline's."""
        ...

    async def adecide(self, statement: str, neighbors: Sequence[MemoryRecord]) -> MergeDecision:
        """Async variant of :meth:`decide`."""
        ...


class LLMFactExtractor:
    """The default :class:`FactExtractor`: structured-output extraction, one call per pass.

    ``model`` is deep-copied per call, matching ``LLMEntityExtractor`` -- ``response`` mutates
    model state, and a pipeline call site is not the place to discover that by sharing one.
    """

    def __init__(self, model: "Model", instructions: Optional[str] = None) -> None:
        if model is None:
            raise MemoryOperationError(
                "LLMFactExtractor needs a model; fact extraction is an LLM pass on the "
                "consolidation lane. Inject a different FactExtractor to avoid one.",
                field_name="model",
            )
        self.model = model
        self.instructions = instructions

    def _messages(self, episodes: Sequence[MemoryRecord]) -> List["Message"]:
        from egpt.models.message import Message

        system = _EXTRACT_SYSTEM
        if self.instructions:
            system += f"\n## Additional Instructions\n\n{self.instructions}\n"
        rendered = "\n".join(f"[{i}] {episode.content}" for i, episode in enumerate(episodes))
        return [
            Message(role="system", content=system),
            Message(role="user", content=f"Episodes:\n{rendered}"),
        ]

    @staticmethod
    def _parse(response: Any) -> Sequence[FactCandidate]:
        """Read the structured answer, refusing anything else -- see ``LLMEntityExtractor._parse``
        for why an unreadable answer must not read the same as "nothing extracted"."""
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, ExtractedFacts):
            return parsed.facts
        content = parsed if isinstance(parsed, (str, bytes, bytearray)) else getattr(response, "content", None)
        if isinstance(content, (str, bytes, bytearray)):
            try:
                return ExtractedFacts.model_validate_json(content).facts
            except Exception as exc:
                raise MemoryOperationError(
                    f"The extraction model's answer is not the requested schema ({exc}). An "
                    "unreadable answer is not an episode set with no facts.",
                    field_name="response",
                ) from exc
        raise MemoryOperationError("The extraction model returned no content.", field_name="response")

    def extract(self, episodes: Sequence[MemoryRecord]) -> Sequence[FactCandidate]:
        response = deepcopy(self.model).response(messages=self._messages(episodes), response_format=ExtractedFacts)
        return self._parse(response)

    async def aextract(self, episodes: Sequence[MemoryRecord]) -> Sequence[FactCandidate]:
        response = await deepcopy(self.model).aresponse(
            messages=self._messages(episodes), response_format=ExtractedFacts
        )
        return self._parse(response)


class LLMMergeDecider:
    """The default :class:`MergeDecider`: structured-output ADD/UPDATE/DELETE/NOOP."""

    def __init__(self, model: "Model", instructions: Optional[str] = None) -> None:
        if model is None:
            raise MemoryOperationError(
                "LLMMergeDecider needs a model; the merge decision is an LLM pass on the "
                "consolidation lane. Inject a different MergeDecider to avoid one.",
                field_name="model",
            )
        self.model = model
        self.instructions = instructions

    def _messages(self, statement: str, neighbors: Sequence[MemoryRecord]) -> List["Message"]:
        from egpt.models.message import Message

        system = _MERGE_SYSTEM
        if self.instructions:
            system += f"\n## Additional Instructions\n\n{self.instructions}\n"
        lines = [f"NEW candidate fact:\n{statement}\n", "EXISTING similar facts:"]
        lines.extend(f"[{i}] {fact.content}" for i, fact in enumerate(neighbors))
        return [
            Message(role="system", content=system),
            Message(role="user", content="\n".join(lines)),
        ]

    @staticmethod
    def _parse(response: Any) -> MergeDecision:
        """Read the structured answer. A ``target_index`` the model invented (out of range, or an
        UPDATE/DELETE naming none) is resolved by ``FactExtractionPipeline._resolve_target``, not
        here -- one place owns the degrade rule, for every :class:`MergeDecider` alike."""
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, MergeDecision):
            return parsed
        content = parsed if isinstance(parsed, (str, bytes, bytearray)) else getattr(response, "content", None)
        if not isinstance(content, (str, bytes, bytearray)):
            raise MemoryOperationError("The merge model returned no content.", field_name="response")
        try:
            return MergeDecision.model_validate_json(content)
        except Exception as exc:
            raise MemoryOperationError(
                f"The merge model's answer is not the requested schema ({exc}).",
                field_name="response",
            ) from exc

    def decide(self, statement: str, neighbors: Sequence[MemoryRecord]) -> MergeDecision:
        if not neighbors:
            return MergeDecision(op="ADD", target_index=None, statement=statement)
        response = deepcopy(self.model).response(
            messages=self._messages(statement, neighbors), response_format=MergeDecision
        )
        return self._parse(response)

    async def adecide(self, statement: str, neighbors: Sequence[MemoryRecord]) -> MergeDecision:
        if not neighbors:
            return MergeDecision(op="ADD", target_index=None, statement=statement)
        response = await deepcopy(self.model).aresponse(
            messages=self._messages(statement, neighbors), response_format=MergeDecision
        )
        return self._parse(response)


def fact_extraction_details(complete: bool = True, **extra: Any) -> Dict[str, Any]:
    """Build a report payload naming this strategy, so a caller reading ``details["supersede"]``
    sees which pipeline produced it rather than a bare boolean."""
    details: Dict[str, Any] = {"strategy": STRATEGY_ID, "complete": complete}
    details.update(extra)
    return details


@dataclass
class FactExtractionReport:
    """What one pass read, decided, and wrote."""

    scope: Scope
    episodes_read: int = 0
    candidates: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    noop: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


class FactExtractionPipeline:
    """Episodes in, ``FactWrite`` batch out -- extract, retrieve, decide, per scope.

    Returns writes rather than performing them: ``CoLocatedMemoryProvider.consolidate`` owns
    fanning a batch out to both substrates (SM-C1 #89), and a pipeline that wrote its own half
    would let the two diverge on partial-write handling, exactly the split
    :class:`~egpt.memory.graph.entities.EntityExtractionPipeline` avoids by being the *only*
    writer of its own artifact -- facts, unlike entities, are already someone else's write path.

    ``vector`` is read-only here: existing facts are looked up through
    :meth:`~egpt.memory.substrates.VectorSubstrate.search`, which is where "the most similar
    existing facts" (module docstring) comes from. A graph-only deployment has no vector leg to
    search and cannot use this pipeline -- ``consolidate`` already requires a vector substrate to
    enumerate episodes at all (``_check_consolidatable``), so this is not a new constraint.
    """

    def __init__(
        self,
        vector: VectorSubstrate,
        extractor: FactExtractor,
        decider: MergeDecider,
        top_k: int = DEFAULT_TOP_K,
        max_episodes_per_pass: int = DEFAULT_MAX_EPISODES_PER_PASS,
    ) -> None:
        for name, candidate, verbs in (
            ("extractor", extractor, ("extract", "aextract")),
            ("decider", decider, ("decide", "adecide")),
        ):
            missing = [verb for verb in verbs if not callable(getattr(candidate, verb, None))]
            if missing:
                raise MemoryOperationError(
                    f"FactExtractionPipeline.{name} is a {type(candidate).__name__}, which is "
                    f"missing {missing}. Both halves are required: a strategy with only the sync "
                    "verb blocks the async consolidation lane's event loop.",
                    field_name=name,
                )
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise MemoryOperationError(f"top_k must be an integer >= 1, got {top_k!r}.", field_name="top_k")
        if (
            not isinstance(max_episodes_per_pass, int)
            or isinstance(max_episodes_per_pass, bool)
            or (max_episodes_per_pass < 1)
        ):
            raise MemoryOperationError(
                f"max_episodes_per_pass must be an integer >= 1, got {max_episodes_per_pass!r}.",
                field_name="max_episodes_per_pass",
            )
        self.vector = vector
        self.extractor = extractor
        self.decider = decider
        self.top_k = top_k
        self.max_episodes_per_pass = max_episodes_per_pass

    def _selected(self, episodes: Sequence[MemoryRecord]) -> Tuple[List[MemoryRecord], bool]:
        return select_current_episodes(episodes, max_episodes_per_pass=self.max_episodes_per_pass, purpose="Facts")

    @staticmethod
    def _buckets(episodes: Sequence[MemoryRecord]) -> List[Tuple[Scope, List[MemoryRecord]]]:
        """Group by scope before extraction, not after. A candidate's ``source_indices`` index
        into the bucket the model was shown, so grounding a fact in an episode outside its own
        scope is not a decision the model can make -- unlike ``_distil``, which must guard the
        same rule against a model it does not trust after the fact (its own docstring: "two
        episodes never group across scopes, even inside one tenant")."""
        batched: Dict[Scope, List[MemoryRecord]] = {}
        for episode in episodes:
            batched.setdefault(episode.scope, []).append(episode)
        return [(scope, batched[scope]) for scope in sorted(batched, key=lambda s: s.key())]

    @staticmethod
    def _resolve_target(decision: MergeDecision, neighbors: Sequence[MemoryRecord]) -> Optional[MemoryRecord]:
        """``target_index`` bounds-checked against ``neighbors`` -- a hand-rolled
        :class:`MergeDecider` is not required to have done this itself (``LLMMergeDecider``
        already does, defensively)."""
        index = decision.target_index
        if index is None or not (0 <= index < len(neighbors)):
            return None
        return neighbors[index]

    def _apply(
        self,
        decision: MergeDecision,
        neighbors: Sequence[MemoryRecord],
        source_ids: List[str],
        scope: Scope,
        topics: Optional[List[str]],
        ts: int,
        now: int,
    ) -> Tuple[List[FactWrite], str]:
        if decision.op == "NOOP":
            return [], "noop"
        if decision.op == "DELETE":
            target = self._resolve_target(decision, neighbors)
            if target is None:
                return [], "noop"
            retired = FactWrite(
                record=MemoryRecord(
                    id=target.id,
                    tier=MemoryTier.SEMANTIC,
                    content=target.content,
                    scope=target.scope,
                    topics=target.topics,
                    provenance=dict(target.provenance),
                    t_valid=target.t_valid,
                    t_invalid=target.t_invalid,
                    created_at=target.created_at,
                    expired_at=now,
                    superseded_by=None,
                ),
                source_episode_ids=list(target.provenance.get("source_episode_ids") or []),
                derived_by=STRATEGY_ID,
            )
            return [retired], "deleted"

        # ADD, and UPDATE whose target did not resolve, degrade to the same shape: a fresh fact
        # with no predecessor. `target` is looked up only for UPDATE -- an ADD naming a
        # `target_index` (a decider should never emit one, but the schema does not forbid it) must
        # not silently fold into that fact's provenance; ADD means new, regardless of what else the
        # model returned alongside it.
        target = self._resolve_target(decision, neighbors) if decision.op == "UPDATE" else None
        merged_sources = (
            list(dict.fromkeys([*(target.provenance.get("source_episode_ids") or []), *source_ids]))
            if target is not None
            else list(source_ids)
        )
        new_id = str(uuid5(_FACT_NAMESPACE, "|".join(sorted(merged_sources))))
        new_fact = FactWrite(
            record=MemoryRecord(
                id=new_id,
                tier=MemoryTier.SEMANTIC,
                content=decision.statement,
                scope=scope,
                topics=list(topics)
                if topics is not None
                else (list(target.topics) if target and target.topics else None),
                provenance={"source_episode_ids": merged_sources},
                t_valid=ts,
                t_invalid=None,
                created_at=now,
            ),
            source_episode_ids=merged_sources,
            derived_by=STRATEGY_ID,
        )
        if target is None:
            return [new_fact], "added"
        if target.id == new_id:
            # The merged source set is identical to what the target already carries (a re-run, or
            # a NOOP the model mis-labelled UPDATE) -- one upsert-by-id, no retirement needed.
            return [new_fact], "updated"
        retired = FactWrite(
            record=MemoryRecord(
                id=target.id,
                tier=MemoryTier.SEMANTIC,
                content=target.content,
                scope=target.scope,
                topics=target.topics,
                provenance=dict(target.provenance),
                t_valid=target.t_valid,
                t_invalid=target.t_invalid,
                created_at=target.created_at,
                expired_at=now,
                superseded_by=new_id,
            ),
            source_episode_ids=list(target.provenance.get("source_episode_ids") or []),
            derived_by=STRATEGY_ID,
        )
        return [retired, new_fact], "updated"

    def distil(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> Tuple[List[FactWrite], FactExtractionReport]:
        """Extract, retrieve, decide -- sync. Never called from a read; see the module docstring."""
        require_scope(scope, method="distil")
        selected, truncated = self._selected(episodes)
        facts: List[FactWrite] = []
        counts = {"added": 0, "updated": 0, "deleted": 0, "noop": 0}
        candidate_count = 0
        for bucket_scope, bucket in self._buckets(selected):
            if not bucket:
                continue
            candidates = self.extractor.extract(bucket)
            now = now_epoch_s()
            for candidate in candidates:
                candidate_count += 1
                source_ids = [bucket[i].id for i in candidate.source_indices if 0 <= i < len(bucket)]
                if not source_ids:
                    continue  # ungrounded candidate; not attributable to any episode this pass read
                ts = max(
                    (bucket[i].created_at or now for i in candidate.source_indices if 0 <= i < len(bucket)), default=now
                )
                query = MemoryQuery(text=candidate.statement, tiers=[MemoryTier.SEMANTIC], limit=self.top_k)
                neighbors = self.vector.search(bucket_scope, query, tier=MemoryTier.SEMANTIC)
                # Nothing to compare against is unambiguously new -- decided here, once, rather
                # than trusting every `MergeDecider` to shortcut it identically (`LLMMergeDecider`
                # does too, defensively, but a hand-rolled one is not required to).
                decision = (
                    MergeDecision(op="ADD", target_index=None, statement=candidate.statement)
                    if not neighbors
                    else self.decider.decide(candidate.statement, neighbors)
                )
                written, outcome = self._apply(decision, neighbors, source_ids, bucket_scope, candidate.topics, ts, now)
                facts.extend(written)
                counts[outcome] += 1
        report = FactExtractionReport(
            scope=scope,
            episodes_read=len(selected),
            candidates=candidate_count,
            added=counts["added"],
            updated=counts["updated"],
            deleted=counts["deleted"],
            noop=counts["noop"],
            details=fact_extraction_details(complete=not truncated, **counts),
        )
        return facts, report

    async def adistil(
        self, scope: Scope, episodes: Sequence[MemoryRecord]
    ) -> Tuple[List[FactWrite], FactExtractionReport]:
        """Async variant of :meth:`distil`, making the same decisions through the same helpers."""
        require_scope(scope, method="adistil")
        selected, truncated = self._selected(episodes)
        facts: List[FactWrite] = []
        counts = {"added": 0, "updated": 0, "deleted": 0, "noop": 0}
        candidate_count = 0
        for bucket_scope, bucket in self._buckets(selected):
            if not bucket:
                continue
            candidates = await self.extractor.aextract(bucket)
            now = now_epoch_s()
            for candidate in candidates:
                candidate_count += 1
                source_ids = [bucket[i].id for i in candidate.source_indices if 0 <= i < len(bucket)]
                if not source_ids:
                    continue
                ts = max(
                    (bucket[i].created_at or now for i in candidate.source_indices if 0 <= i < len(bucket)), default=now
                )
                query = MemoryQuery(text=candidate.statement, tiers=[MemoryTier.SEMANTIC], limit=self.top_k)
                neighbors = await self.vector.asearch(bucket_scope, query, tier=MemoryTier.SEMANTIC)
                decision = (
                    MergeDecision(op="ADD", target_index=None, statement=candidate.statement)
                    if not neighbors
                    else await self.decider.adecide(candidate.statement, neighbors)
                )
                written, outcome = self._apply(decision, neighbors, source_ids, bucket_scope, candidate.topics, ts, now)
                facts.extend(written)
                counts[outcome] += 1
        report = FactExtractionReport(
            scope=scope,
            episodes_read=len(selected),
            candidates=candidate_count,
            added=counts["added"],
            updated=counts["updated"],
            deleted=counts["deleted"],
            noop=counts["noop"],
            details=fact_extraction_details(complete=not truncated, **counts),
        )
        return facts, report
