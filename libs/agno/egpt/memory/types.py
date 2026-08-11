"""The memory seam's envelope types (SM-C1, implementing the SM-R11 spec).

These are **interface DTOs, not stored rows**. The split is deliberate and load-bearing:

* **SM-R12 / SM-C3 (#91) own the persisted per-tier row schemas** -- ``EpisodicRecord``,
  ``SemanticFact`` / ``SemanticEntity`` / ``SemanticRelation``, ``ProceduralRecord``,
  ``WorkingMemory``, ``MemoryLink`` -- on a ``TenantScoped`` + ``BiTemporal`` + ``Embeddable`` +
  ``Decaying`` spine.
* **This module owns the seam envelope** -- ``Scope``, ``MemoryEvent``, ``MemoryQuery``,
  ``MemorySlice``, ``WriteReceipt``, ``ConsolidationReport`` -- which SM-R12 defines none of.

``MemoryRecord`` is the seam's uniform **read-projection** over those row types, not a competing
schema. Its field names and int epoch-seconds are aligned to SM-R12's spine so SM-C3 maps 1:1:
bi-temporal names are SM-R10 canonical (``t_valid`` / ``t_invalid`` / ``created_at`` /
``expired_at`` / ``superseded_by``). Nothing here imports those row types -- they do not exist in
``egpt`` yet, and the seam must not wait on them.

Spec: ``benchmarks/memory_provider_spec/SPEC.md`` section 5.2 (SM-R11 #86).
Ratified by ``docs/adr/0003-symbiotic-memory.md``.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from egpt.memory.tiers import PERSISTENT_TIERS, MemoryTier
from egpt.utils.dttm import to_epoch_s

__all__ = [
    "FUSION_KEYS",
    "ConsolidationReport",
    "FactWrite",
    "fusion_diagnostics",
    "MemoryEvent",
    "MemoryOperationError",
    "MemoryQuery",
    "MemoryRecord",
    "MemorySlice",
    "Scope",
    "Substrate",
    "WriteReceipt",
]


class MemoryOperationError(ValueError):
    """A memory call cannot be served as asked.

    Distinct from ``MemoryProviderConfigError``, which is scoped to construction time: this is a
    **per-request** refusal by a correctly configured provider -- writing a tier nothing is bound
    to, or asking for a point-in-time read the wired substrates cannot answer safely. Raising the
    config error for these made a routine bad request look like a deployment fault in every log
    and every ``except`` clause.

    ``ValueError`` for the same reason its sibling is one: callers already assert against it.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


class Substrate(str, Enum):
    """Where a memory is stored -- the axis orthogonal to :class:`~egpt.memory.tiers.MemoryTier`.

    The two are co-equal by decision D8 (ADR 0003): neither is a cache of the other. The graph is
    the authority for bi-temporal validity; the vector substrate is not (finding F2).
    """

    VECTOR = "vector"  # similarity + full-text; the episodic recall entry point
    GRAPH = "graph"  # temporal knowledge graph: multi-hop, provenance, bi-temporal


def _epoch_or_none(value: Any) -> Optional[int]:
    """Normalize a timestamp to int epoch seconds, passing ``None`` through.

    Accepting a ``datetime`` or ISO string here rather than only an int mirrors
    ``UserMemory.__post_init__`` (``db/schemas/memory.py``), and means a caller cannot
    accidentally store a float or a naive datetime where every consumer expects epoch seconds.

    ``bool`` is refused even though it is an ``int`` subclass, matching how
    ``MemoryProviderConfig`` rejects a boolean ``vector_weight``: ``t_valid=True`` would silently
    become epoch second 1, a timestamp in 1970 that no later filter would flag as wrong.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"Timestamp must be epoch seconds, a datetime, or an ISO string; got {value!r}.")
    return to_epoch_s(value)


@dataclass(frozen=True)
class Scope:
    """Isolation and addressing. Present on **every** read, write, and consolidate call.

    ``tenant_id`` is required and rejected when blank. That is a construction-time guard, not the
    data-layer enforcement SM-C4 (#92) owns -- but it exists because ``None`` or ``""`` means
    *unscoped*, and unscoped is the **widest** tenancy, not the narrowest (``docs/tenancy.md``
    section 2). A seam where the unscoped call is the easiest one to write is a seam that leaks.

    ``user_id`` is the identity namespace that survives sessions (research doc section 3);
    ``agent_id`` / ``team_id`` / ``session_id`` narrow further. Frozen so two concurrent requests
    can never share mutable scope, the same reason ``ModelResolveRequest`` is frozen.
    """

    tenant_id: str
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    session_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError(
                "Scope.tenant_id is required and must be a non-empty string. "
                "An unscoped memory call reads and writes every tenant's rows."
            )
        # Store what was validated. Validating `.strip()` and then keeping the raw value would
        # give "  acme  " and "acme" different partition keys, so the same tenant would read and
        # write two disjoint sets of rows depending on incidental whitespace upstream.
        # `object.__setattr__` because the dataclass is frozen.
        object.__setattr__(self, "tenant_id", self.tenant_id.strip())

    def key(self) -> str:
        """Stable string key for storage partitioning and logging.

        ``*`` stands for an unset slot so the key is fixed-arity: two scopes differing only in
        which slots are set can never collide.
        """
        return "/".join(
            [
                self.tenant_id,
                self.agent_id or "*",
                self.user_id or "*",
                self.team_id or "*",
                self.session_id or "*",
            ]
        )


@dataclass
class MemoryRecord:
    """One memory item as the seam returns it -- a read-projection, not a schema.

    Bi-temporal semantics (SM-R10, ratified): **valid time** is when the fact was true in the
    world (``t_valid`` .. ``t_invalid``); **transaction time** is when the system believed it
    (``created_at`` in, ``expired_at`` out). The two axes are independent and both load-bearing:
    a fact becoming false is not the same event as a row being superseded.

    Invalidate-not-delete: a contradiction sets ``t_invalid`` on a **new** row and expires the old
    one (SCD-2). Nothing here is ever mutated in place -- SM-R10 section 6 proved in-place wrong,
    because a later fact clobbers an already-set bound and belief evolution stops reconstructing.
    """

    id: str
    tier: MemoryTier
    content: str
    scope: Scope
    score: Optional[float] = None  # fused (weighted / RRF) or substrate-native
    substrate: Optional[Substrate] = None  # origin; None once fused across both
    topics: Optional[List[str]] = None
    provenance: Dict[str, Any] = field(default_factory=dict)  # source_episode_ids, edge ids
    t_valid: Optional[int] = None  # valid time in:  became true in the world
    t_invalid: Optional[int] = None  # valid time out: became false in the world
    created_at: Optional[int] = None  # transaction time in:  system recorded it
    expired_at: Optional[int] = None  # transaction time out: row superseded
    superseded_by: Optional[str] = None  # id of the replacement row
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.t_valid = _epoch_or_none(self.t_valid)
        self.t_invalid = _epoch_or_none(self.t_invalid)
        self.created_at = _epoch_or_none(self.created_at)
        self.expired_at = _epoch_or_none(self.expired_at)

    def is_current_belief(self) -> bool:
        """Whether this row is what the system believes *now* (``expired_at IS NULL``).

        Distinct from being *valid at a time* -- an expired row can still describe a fact that was
        true, and a current row can describe a fact that has since become false. Point-in-time
        reads need both predicates (SM-R10 section 7 5a).
        """
        return self.expired_at is None


@dataclass
class MemoryEvent:
    """A write-back unit: something that happened and should be remembered.

    Writes land in ``EPISODIC`` by default -- raw and non-lossy, no distillation on the request
    path. Consolidation later distills them into ``SEMANTIC`` (SM-L*).

    ``id`` is an optional idempotency key. When the provider writes an event it stamps **one** id
    and passes it to both substrates, because that id is the cross-substrate dedup key during
    fusion; two ids for one logical episode surface as two fused records (finding F1).

    ``occurred_at`` is **valid time** -- when it happened, not when it was recorded. It is left
    unstamped here on purpose: an event may be constructed well before it is written, and the
    write path is what knows the recording time.
    """

    content: str
    scope: Scope
    tier: MemoryTier = MemoryTier.EPISODIC
    id: Optional[str] = None
    role: Optional[str] = None  # for message-shaped events (user / assistant / ...)
    topics: Optional[List[str]] = None
    occurred_at: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.occurred_at = _epoch_or_none(self.occurred_at)


@dataclass
class FactWrite:
    """The argument shape for writing a distilled semantic fact to the graph substrate.

    Not a stored schema -- it composes :class:`MemoryRecord` (which carries content, id, scope,
    and the bi-temporal envelope) with the subject/predicate/object structure a graph write needs
    but an episode write does not.

    This exists because the port previously exposed only ``upsert_episode``, which forced
    consolidation to write a distilled fact as a flat, provenance-carrying pseudo-episode rather
    than the entity/relation shape SM-R7 intends (finding F9, assigned to SM-C1).

    Field names mirror SM-R12's ``SemanticFact`` (``subject`` / ``predicate`` / ``object`` /
    ``confidence`` / ``source_episode_ids`` / ``derived_by``) so SM-C3 maps this onto the persisted
    row 1:1. SPO is optional structure over the free-text statement in ``record.content``, exactly
    as it is there -- a fact that resists triple extraction is still storable.
    """

    record: MemoryRecord
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    confidence: float = 1.0
    #: Provenance: which episodes this fact was distilled from. First-class rather than a
    #: ``record.provenance`` key because SM-R7 section 7 makes it load-bearing -- it is populated at
    #: extraction and unioned forward on UPDATE/NOOP, so a consolidation pass that drops it
    #: silently breaks the fact -> episode audit trail.
    source_episode_ids: List[str] = field(default_factory=list)
    derived_by: Optional[str] = None  # the strategy/pass that produced it

    def is_triple(self) -> bool:
        """Whether this fact carries the full SPO structure a graph edge requires."""
        return bool(self.subject and self.predicate and self.object)


@dataclass
class MemoryQuery:
    """A read request.

    ``tiers`` is what issue #89 calls ``types``; ``None`` means
    :data:`~egpt.memory.tiers.PERSISTENT_TIERS`. ``substrates`` defaults to whichever the provider
    has wired. ``as_of`` requests a point-in-time read and is enforced on the **fused** result set,
    not per-substrate -- the vector substrate has no notion of validity, so filtering it there
    lets an invalidated fact leak back in through the other leg (finding F2).
    """

    text: str
    tiers: Optional[Sequence[MemoryTier]] = None
    substrates: Optional[Sequence[Substrate]] = None
    limit: int = 10
    as_of: Optional[int] = None  # point-in-time read (epoch s, SM-R10)
    filters: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.as_of = _epoch_or_none(self.as_of)
        # A negative limit is not a smaller result set -- as a bare slice it truncates from the
        # *end*, so `limit=-1` returns everything except the best-ranked record. A caller computing
        # `limit = budget - used` and going negative would get a nearly-full slice with the top hit
        # silently missing. Rejected here for the same reason `config.py` rejects an out-of-range
        # `vector_weight` rather than letting it skew a ranking.
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit < 0:
            raise ValueError(f"MemoryQuery.limit must be an integer >= 0, got {self.limit!r}.")

    def resolved_tiers(self) -> Sequence[MemoryTier]:
        """The tier set to search: the requested one, or every persistent tier.

        Every substrate must resolve the tier set **identically**. When one leg honored the filter
        and the other did not, cross-tier leakage appeared only after fusion and was hard to trace
        back to its source (finding F3) -- so the resolution lives here, once.

        ``None`` means "unspecified, use the default"; an **empty sequence means empty**. Those are
        different requests, and conflating them widens rather than narrows -- the direction that
        matters, and the same reasoning ``Scope.tenant_id`` rests on. ``_requested`` in the
        co-located adapter tests ``substrates is None`` for exactly this reason; these two adjacent
        optional-sequence filters now agree.
        """
        return PERSISTENT_TIERS if self.tiers is None else tuple(self.tiers)


#: Keys every provider must put in ``MemorySlice.fusion``, whatever it does internally.
#:
#: Without a contract the adapters diverged: the co-located one reported hit counts while the seed
#: bridge reported only ``method`` and ``reason``, so a consumer written against one raised
#: ``KeyError`` on the other -- including the cookbook's deliberately provider-agnostic consumer,
#: which is the swap property this seam exists to provide. A provider with nothing to fuse reports
#: zeros, which is honest: it has no legs, not an unknown number of them.
FUSION_KEYS = ("method", "vector_hits", "graph_hits", "fused", "returned")


def fusion_diagnostics(
    method: Optional[str],
    vector_hits: int = 0,
    graph_hits: int = 0,
    fused: int = 0,
    returned: int = 0,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a ``MemorySlice.fusion`` payload carrying every key in :data:`FUSION_KEYS`.

    ``method`` is ``None`` for a provider that does not fuse. Extra keys are free -- the contract is
    a floor, not a ceiling, so an adapter can add ``reason`` or ``deployment`` without breaking a
    consumer that reads only the common set.
    """
    diagnostics: Dict[str, Any] = {
        "method": method,
        "vector_hits": vector_hits,
        "graph_hits": graph_hits,
        "fused": fused,
        "returned": returned,
    }
    diagnostics.update(extra)
    return diagnostics


@dataclass
class MemorySlice:
    """The feed-forward result of ``read`` -- the memory handed to the agent.

    ``records`` is the fused, ranked list; ``by_tier`` groups the same objects for callers that
    inject per tier; ``fusion`` carries the diagnostics (method, weights, per-leg hit counts) that
    make a ranking explainable after the fact.
    """

    scope: Scope
    query: Optional[MemoryQuery] = None
    records: List[MemoryRecord] = field(default_factory=list)
    by_tier: Dict[MemoryTier, List[MemoryRecord]] = field(default_factory=dict)
    fusion: Dict[str, Any] = field(default_factory=dict)

    def as_context(self) -> str:
        """Render the slice as a prompt-injectable string.

        Parallels ``LearningStore.build_context`` (``learn/stores/protocol.py:78``) so a slice
        drops into the same place in an agent prompt that the seed's learn context does.

        Newlines inside a record's content are indented rather than emitted raw. The ``[tier]``
        prefix is line-oriented, so stored content containing ``"\\n[semantic] ..."`` would
        otherwise render as an additional, forged memory line -- and memory content is attacker-
        influenced by construction, since it is whatever a user said to the agent. Continuation
        lines are visibly indented, so one memory always reads as one entry.
        """
        if not self.records:
            return ""
        lines = []
        for record in self.records:
            body = record.content.replace("\r\n", "\n").replace("\r", "\n")
            head, *rest = body.split("\n")
            lines.append("[{tier}] {head}".format(tier=record.tier.value, head=head))
            lines.extend("    " + line for line in rest)
        return "\n".join(lines)


@dataclass
class WriteReceipt:
    """What a ``write`` accepted and where it landed.

    ``event_ids`` are the ids the provider stamped -- the caller needs them to correlate a later
    read, and consolidation needs them for ``source_episode_ids`` provenance.
    """

    accepted: int = 0
    event_ids: List[str] = field(default_factory=list)
    tiers_written: List[MemoryTier] = field(default_factory=list)


@dataclass
class ConsolidationReport:
    """The outcome of one (async) consolidate pass: episodic -> semantic.

    The write-side consolidation loop is the differentiator of this design and the least-solved
    part of the field (research doc section 5.3): extract -> dedup -> contradiction-resolve ->
    write to **both** substrates, on the sleep-time lane.

    **One write model.** Any contradiction consolidation produces on the graph resolves through
    the same SM-R10 expire-and-reinsert (SCD-2) primitive as ``GraphSubstrate.invalidate`` -- never
    a second, in-place path. Read that against ADR 0003 section B.6's row-vs-edge boundary: SCD-2
    governs semantic contradiction on graph **edges**, while episodic **row** lifecycle
    (SM-R6 re-segmentation, SM-R5 decay archival) is row-level ``expired_at``. Both are
    append-only and invalidate-not-delete; they are different objects, not one path.
    """

    scope: Scope
    episodic_scanned: int = 0
    semantic_written: int = 0
    duplicates_merged: int = 0
    contradictions_resolved: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
