"""SM-R12 - Memory data model (implementable record types for all four CoALA tiers + links).

This is the concrete artifact SM-C3 builds from. It is deliberately standalone (pydantic
only, no import from ``egpt``) so the decision folder stays self-contained and core is proven
untouched. SM-C3 promotes these shapes into ``libs/agno/egpt/db/schemas/`` and writes the
migrations; the field set, types, nullability and defaults here are the contract.

Design in one paragraph
-----------------------
One shared spine (:class:`TenantScoped` + :class:`BiTemporal`) carries the cross-cutting
fields the issue requires "where needed" -- tenancy (SM-R3 partition key), embedding
(SM-R4), bi-temporal time (SM-R10), decay (SM-R5). Each tier is a thin subclass that adds
only its tier-specific fields. This mirrors how the seed already specialises one base
(``UserProfile``/``EntityMemory``/... all share ``user_id``/``agent_id``/timestamps) rather
than inventing parallel unrelated schemas -- so the model *extends* the seed's shape instead
of forking it.

Tier -> model map
-----------------
- working    -> :class:`WorkingMemory`      (session-scoped, transient; extends seed SessionContext)
- episodic   -> :class:`EpisodicRecord`     (raw events; net-new, aligns SM-R7 ``Episode``)
- semantic   -> :class:`SemanticFact` (fact), :class:`SemanticEntity` (graph node),
                :class:`SemanticRelation` (graph edge -- the bi-temporal SM-R10 surface)
- procedural -> :class:`ProceduralRecord`   (rule / skill / prompt fragment; extends LearnedKnowledge)
- links      -> :class:`MemoryLink`         (typed cross-tier edge)

Time conventions
----------------
All row-level timestamps are Unix epoch **seconds** (ints), matching the seed's
``UserMemory``/``KnowledgeRow`` columns. This is deliberately different from the ISO-8601
strings the seed keeps *inside* ``learn`` content blobs; the migration notes (SPEC.md) call
this out. Bi-temporal field names follow **SM-R10 (#85)** as the authority
(``t_valid``/``t_invalid``/``created_at``/``expired_at``); the SM-R7 consolidation prototype's
``valid_from``/``valid_to``/``superseded_by`` map onto these (see ``TEMPORAL_FIELD_MAP``).
"""

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


def _now_epoch_s() -> int:
    """Current Unix time in whole seconds (row-column convention)."""
    return int(time.time())


def _new_id() -> str:
    """Opaque record id. uuid4 hex keeps it store-agnostic (pg text / graph id / vector id)."""
    return uuid.uuid4().hex


# Maps the SM-R7 consolidation prototype's temporal vocabulary onto the SM-R10 canonical
# names this spec standardises on. SM-C3 renames on promotion; documented so nothing is lost.
TEMPORAL_FIELD_MAP: Dict[str, str] = {
    "valid_from": "t_valid",  # when the fact/edge became true in the world
    "valid_to": "t_invalid",  # when it stopped being true (None => still valid)
    "created_at": "created_at",  # transaction time in (unchanged)
    "superseded_by": "superseded_by",  # retraction pointer (kept alongside expired_at)
}


# ---------------------------------------------------------------------------
# Enums (discriminators)
# ---------------------------------------------------------------------------
class MemoryTier(str, Enum):
    """CoALA memory tiers (SM-R1 ADR). ``working`` is transient; the other three persist."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class SemanticKind(str, Enum):
    """Sub-shape of the semantic tier."""

    FACT = "fact"
    ENTITY = "entity"
    RELATION = "relation"


class ProceduralKind(str, Enum):
    """How a procedural memory is expressed (issue Q3)."""

    RULE = "rule"
    SKILL = "skill"
    PROMPT_FRAGMENT = "prompt_fragment"


class LinkType(str, Enum):
    """Typed edges between memory records/nodes (the issue's "links" tier).

    Open by convention -- SM-C3 may add types; consumers must tolerate unknown strings.
    """

    MENTIONS = "mentions"  # episodic -> entity
    DERIVED_FROM = "derived_from"  # semantic fact -> source episode
    RELATES_TO = "relates_to"  # generic association
    SUPERSEDES = "supersedes"  # new record -> the one it replaces
    ABOUT = "about"  # any record -> entity it concerns


class BoundaryMethod(str, Enum):
    """Which rule opened an episode's boundary (SM-R6 event-segmentation, section 5)."""

    TIME_GAP = "time_gap"
    TURN_CAP = "turn_cap"
    EMBEDDING_DRIFT = "embedding_drift"
    EM_LLM = "em_llm"
    LLM = "llm"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Shared spine
# ---------------------------------------------------------------------------
class TenantScoped(BaseModel):
    """Identity + multi-tenancy + audit fields common to every persisted tier.

    ``tenant_id`` is required and is the **partition key** decided by SM-R3 (declarative
    partitioning by tenant); every physical table/collection/graph namespace is keyed on it,
    and cross-tenant reads are asserted to return nothing. The remaining scope ids mirror the
    seed (``user_id``/``agent_id``/``team_id``) so existing rows migrate without loss.
    """

    id: str = Field(default_factory=_new_id)
    tenant_id: str  # REQUIRED -- SM-R3 partition key
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    team_id: Optional[str] = None
    session_id: Optional[str] = None

    created_at: int = Field(
        default_factory=_now_epoch_s
    )  # transaction time IN (epoch s)
    updated_at: Optional[int] = None  # seed audit column; last mutation (epoch s)

    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=True, extra="forbid")


class BiTemporal(BaseModel):
    """Valid-time + transaction-time axes (SM-R10 four-timestamp model, invalidate-not-delete).

    - ``created_at`` (on :class:`TenantScoped`) is transaction time IN.
    - ``expired_at`` is transaction time OUT: when this *row* was superseded in the store
      (None => current row). Corrections never delete; they set ``expired_at`` + write a new row.
    - ``t_valid`` / ``t_invalid`` are the world-validity window. "true at T" == rows where
      ``t_valid <= T < t_invalid`` (t_invalid None => open-ended). Query semantics owned by SM-R10.
    - ``superseded_by`` points at the replacement record id (SM-R7 provenance continuity).
    """

    t_valid: Optional[int] = (
        None  # valid time start (epoch s); None => unknown / since-forever
    )
    t_invalid: Optional[int] = None  # valid time end (epoch s); None => still valid
    expired_at: Optional[int] = None  # transaction time out; None => current row
    superseded_by: Optional[str] = None  # id of the record that replaced this one

    def is_current(self) -> bool:
        """Row is the live version (not yet superseded in the store)."""
        return self.expired_at is None and self.superseded_by is None

    def is_valid_at(self, t: int) -> bool:
        """World-validity test used by point-in-time queries (SM-R10 semantics)."""
        if self.t_valid is not None and t < self.t_valid:
            return False
        if self.t_invalid is not None and t >= self.t_invalid:
            return False
        return True


class Embeddable(BaseModel):
    """Vector fields (SM-R4). Nullable: not every tier/row is embedded at write time.

    ``embedding_version`` is the SM-R4 tag ``model/dim/metric/rev`` (e.g.
    ``bge-base-en-v1.5/d768/cos/v1``); ``embedding_dim`` (768 Mode A / 1024 Mode B) is the
    coupling to the SM-R3 pgvector column width. Raw ``content`` is kept durable so vectors are
    always rebuildable on a re-embed (SM-R4 D8).
    """

    embedding: Optional[List[float]] = None
    embedding_dim: Optional[int] = None  # 768 | 1024 -- must match the store column
    embedding_version: Optional[str] = None  # "bge-base-en-v1.5/d768/cos/v1"


class Decaying(BaseModel):
    """Decay / reinforcement fields (SM-R5). Present on tiers that rank by recency (episodic).

    Per SM-R5's ratified decision (`memory_decay/DECISION.md` section 5), decay is computed at
    READ time from ``last_accessed_at`` and ``importance`` (importance-weighted exponential
    recency); only these two fields are stored. SM-R5 explicitly **defers** an Ebbinghaus
    ``strength`` counter -- it measured negligible benefit ("last_accessed_at + importance
    capture the behaviour with one fewer stateful field"), so this spec does NOT add it. Revisit
    only if SM-E4 shows a need.
    """

    importance: float = 0.5  # [0, 1]; modulates the decay half-life (SM-R5)
    last_accessed_at: Optional[int] = (
        None  # epoch s; recency clock, reset on confirmed access
    )


# ---------------------------------------------------------------------------
# Tier: episodic
# ---------------------------------------------------------------------------
class EpisodicContext(BaseModel):
    """The when/where/why of an episode (issue Q1). All optional -- extracted best-effort."""

    occurred_at: Optional[int] = (
        None  # WHEN it happened (epoch s); distinct from created_at
    )
    location: Optional[str] = None  # WHERE (physical/logical)
    cause: Optional[str] = None  # WHY / triggering context

    model_config = ConfigDict(extra="forbid")


class EpisodicRecord(TenantScoped, BiTemporal, Embeddable, Decaying):
    """A raw episodic memory: "something that happened". The input tier for consolidation.

    Aligns with the SM-R7 ``Episode`` (``text``->``content``, ``timestamp``->``context.occurred_at``)
    and adds the tenancy/embedding/temporal/decay spine. Net-new: the seed has no episodic tier.

    Episode-identity & stability contract (SM-R6 event-segmentation, section 4/5). Episodes are
    (re)segmented, so they carry a stable-id + supersede/revision contract for async re-cuts:
    inline write assigns ``revision=0``; the async re-segmenter writes NEW episodes with
    ``revision=n+1`` and ``supersedes=[old_ids]`` and marks the old rows ``superseded_by=[new_ids]``
    (invalidate-not-delete). Because a re-cut can merge/split, ``supersedes``/``superseded_by`` are
    uuid **arrays** here -- this deliberately overrides the single-id ``superseded_by`` inherited
    from :class:`BiTemporal` (which stays single-id for fact/relation/link). ``boundary_*`` records
    which rule cut the boundary and how confident it was; ``segmenter_version`` pins reproducibility.
    """

    tier: MemoryTier = MemoryTier.EPISODIC
    content: str  # REQUIRED -- the raw event text
    context: EpisodicContext = Field(default_factory=EpisodicContext)
    entity_ids: List[str] = Field(
        default_factory=list
    )  # graph nodes this episode mentions
    graph_node_id: Optional[str] = None  # optional 1:1 promotion link into the graph

    # SM-R6 episode-identity / segmentation metadata:
    revision: int = 0  # 0 inline; incremented on async re-cut
    supersedes: List[str] = Field(
        default_factory=list
    )  # episode ids this re-cut replaced
    # Deliberate SM-R6 override of BiTemporal's single-id superseded_by -> uuid[] identity graph
    # (a re-cut can merge/split, so it is many-valued). Intentional LSP divergence for this tier.
    superseded_by: List[str] = Field(default_factory=list)  # type: ignore[assignment]
    boundary_method: Optional[BoundaryMethod] = None  # which rule opened this episode
    boundary_trigger: Optional[float] = (
        None  # idle-gap s / turn count / sigma-units / model conf
    )
    boundary_confidence: Optional[float] = None  # [0, 1]; 1.0 for deterministic rules
    refined: bool = False  # True if the async lane re-segmented the inline boundary
    segmenter_version: Optional[str] = None  # version/param-hash of the segmenter


# ---------------------------------------------------------------------------
# Tier: semantic (fact / entity / relation)
# ---------------------------------------------------------------------------
class SemanticFact(TenantScoped, BiTemporal, Embeddable, Decaying):
    """A distilled, durable fact with provenance + bi-temporal validity (subject/predicate/object).

    Mirrors SM-R7 ``SemanticFact`` field-for-field (``statement``->``content``,
    ``source_episode_ids``, ``confidence``, ``superseded_by``) with the temporal names remapped
    to SM-R10. SPO is optional structure over the free-text statement.
    """

    tier: MemoryTier = MemoryTier.SEMANTIC
    kind: SemanticKind = SemanticKind.FACT
    content: str  # REQUIRED -- the statement text
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    confidence: float = 1.0
    source_episode_ids: List[str] = Field(
        default_factory=list
    )  # provenance: fact -> episodes
    derived_by: Optional[str] = (
        None  # strategy/pass that produced it (SM-R7 provenance)
    )
    entity_ids: List[str] = Field(default_factory=list)  # graph nodes referenced


class SemanticEntity(TenantScoped, Embeddable):
    """A graph node: a company/person/project/system the memory knows about.

    Extends the seed ``EntityMemory`` core (``entity_id``/``entity_type``/``name``/``properties``);
    the seed's embedded ``facts``/``events``/``relationships`` lists are normalised out into
    :class:`SemanticFact` / :class:`EpisodicRecord` / :class:`SemanticRelation` rows. Entities are
    not bi-temporal themselves (identity is stable); their *relations* carry validity time.
    """

    entity_type: str  # REQUIRED -- "company" | "person" | "project" | ...
    name: Optional[str] = None
    description: Optional[str] = None
    properties: Dict[str, str] = Field(default_factory=dict)


class SemanticRelation(TenantScoped, BiTemporal):
    """A graph EDGE: ``(subject_entity)-[predicate]->(object_entity)``.

    This is the primary SM-R10 bi-temporal surface: contradictions set ``t_invalid`` (and
    ``expired_at`` on correction) rather than deleting, so point-in-time reconstruction holds.
    Not embedded -- edges are retrieved by traversal/filter, not vector search.
    """

    subject_entity_id: str  # REQUIRED
    predicate: str  # REQUIRED -- "works_at" | "ceo_of" | ...
    object_entity_id: str  # REQUIRED
    confidence: float = 1.0
    source_episode_ids: List[str] = Field(
        default_factory=list
    )  # provenance for the edge
    derived_by: Optional[str] = (
        None  # strategy/pass that produced it (SM-R7 provenance)
    )


# ---------------------------------------------------------------------------
# Tier: procedural
# ---------------------------------------------------------------------------
class ProceduralRecord(TenantScoped, Embeddable):
    """A learned procedure: a rule, a skill, or a reusable prompt fragment (issue Q3).

    Extends the seed ``LearnedKnowledge`` (``title``/``learning``/``context``/``tags``); adds a
    ``kind`` discriminator + an optional ``trigger`` (when to apply). Not bi-temporal by default
    (procedures are versioned by supersession via :class:`MemoryLink`), but embeddable so skills
    are retrievable.
    """

    tier: MemoryTier = MemoryTier.PROCEDURAL
    kind: ProceduralKind = ProceduralKind.RULE
    title: Optional[str] = None
    content: str  # REQUIRED -- the rule/skill/prompt body (seed ``learning``)
    trigger: Optional[str] = None  # when/where this applies (seed ``context``)
    tags: Optional[List[str]] = None
    success_count: Optional[int] = None  # optional reinforcement signal


# ---------------------------------------------------------------------------
# Tier: working (transient)
# ---------------------------------------------------------------------------
class WorkingMemory(TenantScoped):
    """Session-scoped scratch state (CoALA working memory). Transient -- extends seed
    ``SessionContext``. Included for completeness; typically NOT durably stored (no vector, no
    bi-temporal). SM-C3 may keep it in the session store rather than the memory tables.
    """

    tier: MemoryTier = MemoryTier.WORKING
    summary: Optional[str] = None
    goal: Optional[str] = None
    plan: Optional[List[str]] = None
    progress: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
class MemoryLink(TenantScoped, BiTemporal):
    """A typed edge between any two memory records/nodes across tiers.

    Generalises the seed ``EntityMemory.relationships`` inline dicts into first-class,
    tenant-scoped, bi-temporal rows so links (episode->entity, fact->episode, record->supersedes)
    are queryable and correctable like graph edges.
    """

    from_id: str  # REQUIRED
    from_tier: MemoryTier  # REQUIRED
    to_id: str  # REQUIRED
    to_tier: MemoryTier  # REQUIRED
    link_type: LinkType = LinkType.RELATES_TO


# Convenience registry: tier/kind -> model (used by the round-trip test in phase 3).
TIER_MODELS = {
    "working": WorkingMemory,
    "episodic": EpisodicRecord,
    "semantic.fact": SemanticFact,
    "semantic.entity": SemanticEntity,
    "semantic.relation": SemanticRelation,
    "procedural": ProceduralRecord,
    "link": MemoryLink,
}
