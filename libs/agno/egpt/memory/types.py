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

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from egpt.memory.errors import MemoryOperationError
from egpt.memory.tiers import DEFAULT_WRITE_TIER, PERSISTENT_TIERS, MemoryTier
from egpt.utils.dttm import to_epoch_s

__all__ = [
    "CONSOLIDATION_KEYS",
    "FUSION_KEYS",
    "MAX_LIMIT",
    "NO_SUPERSEDE",
    "consolidation_details",
    "ConsolidationReport",
    "FactWrite",
    "fusion_diagnostics",
    "MemoryEvent",
    "MemoryOperationError",
    "MemoryQuery",
    "MemoryRecord",
    "MemorySlice",
    "PartialWriteError",
    "Scope",
    "Substrate",
    "TemporalBounds",
    "WRITE_KEYS",
    "write_details",
    "WriteReceipt",
]


#: Ceiling on ``MemoryQuery.limit``. Not a tuning knob -- a guard against a page request that is
#: really a full scan. Enumerating a tier is ``VectorSubstrate.scan``'s job.
MAX_LIMIT = 10_000


class PartialWriteError(RuntimeError):
    """A multi-target write failed partway, carrying the ids a retry must reuse.

    Shared by both adapters because the seam's swap property requires them to fail the same way.
    Neither is atomic -- there is no distributed transaction -- so both are **at-least-once**, and
    two providers behind one interface with different failure semantics give a caller no portable
    retry.

    The ids matter more than the failure. A provider mints an id for any event lacking one, so a
    naive retry writes *fresh* rows for the same logical memories -- finding F1 arriving through the
    error path. Carrying the receipt lets a caller retry with ``MemoryEvent.id`` set, which is
    idempotent provided the substrate upserts by id (see ``GraphSubstrate.upsert_episode``).

    **``event_ids`` are the ids a retry must reuse**, and ``accepted`` is how many of them there
    are. That is the one reading true of both adapters and the one the retry advice needs. On
    ``co_located`` a provider stamps every id up front, so the list is the whole batch -- some rows
    may not have landed, and reusing their ids is exactly what makes the retry safe. On ``seed``
    the manager mints ids as it commits, so an un-attempted event has no id to reuse and the list
    is shorter.

    It is deliberately **not** "rows committed". A caller cannot compute that from here -- a
    partial fan-out may have written to one substrate and not the other for the same id -- and an
    adapter that tried would have to drop ids the caller still needs. Per-substrate commit
    accounting, if some caller needs it, is a separate field rather than a second meaning for this
    one.
    """

    def __init__(self, receipt: "WriteReceipt") -> None:
        super().__init__(
            f"Write partially applied: {receipt.accepted} event(s) may be persisted in some "
            f"targets but not others. Retry with these ids set on the events to stay idempotent: "
            f"{receipt.event_ids}."
        )
        self.receipt = receipt


class Substrate(str, Enum):
    """Where a memory is stored -- the axis orthogonal to :class:`~egpt.memory.tiers.MemoryTier`.

    The two are co-equal by decision D8 (ADR 0003): neither is a cache of the other. The graph is
    the authority for bi-temporal validity; the vector substrate is not (finding F2).
    """

    VECTOR = "vector"  # similarity + full-text; the episodic recall entry point
    GRAPH = "graph"  # temporal knowledge graph: multi-hop, provenance, bi-temporal


def _epoch_or_none(value: Any) -> Optional[int]:
    """Normalize a timestamp to int epoch seconds, passing ``None`` through.

    Accepting a ``datetime`` or ISO string mirrors ``UserMemory.__post_init__``
    (``db/schemas/memory.py``). Two inputs are refused rather than coerced, both because they
    produce a *silently* wrong timestamp: ``bool`` (an ``int`` subclass, so ``t_valid=True`` becomes
    epoch second 1) and a naive ``datetime``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"Timestamp must be epoch seconds, a datetime, or an ISO string; got {value!r}.")
    if isinstance(value, datetime) and value.tzinfo is None:
        # `to_epoch_s` reads a naive datetime as UTC, so `datetime.now()` in any zone ahead of UTC
        # lands in the *future* -- 5h30m in IST, this repo's own. Valid time is evaluated on every
        # read, so such a record is written, receipted as accepted, and then invisible until the
        # clock catches up, with no error anywhere.
        raise TypeError(
            "Timestamp must be timezone-aware. A naive datetime is interpreted as UTC, so a local "
            f"datetime.now() lands in the future and the record fails the valid-time predicate. "
            f"Use datetime.now(timezone.utc) or epoch seconds. Got {value!r}."
        )
    return to_epoch_s(value)


def _escape(value: Optional[str]) -> str:
    """One slot of :meth:`Scope.key`, with the separator made unambiguous.

    ``%`` first, or escaping ``/`` would itself be escapable into a collision.
    """
    if value is None:
        return "*"
    # `%` first, or escaping the others would itself be escapable into a collision. `*` is escaped
    # because it is the unset sentinel: without it `agent_id="*"` and an unset agent_id produce the
    # same key, which is the injectivity this function exists for.
    return value.replace("%", "%25").replace("/", "%2F").replace("*", "%2A")


@dataclass(frozen=True)
class Scope:
    """Isolation and addressing. Present on **every** read, write, and consolidate call.

    ``tenant_id`` is required and rejected when blank, because ``None`` or ``""`` means *unscoped*
    and unscoped is the **widest** tenancy, not the narrowest (``docs/tenancy.md`` section 2). A
    seam where the unscoped call is the easiest one to write is a seam that leaks. Data-layer
    enforcement is SM-C4's (#92); this is the construction-time guard.

    ``user_id`` is the identity namespace that survives sessions (research doc section 3);
    ``agent_id`` / ``team_id`` / ``session_id`` narrow further. Frozen so two concurrent requests
    can never share mutable scope.
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
        # Every slot is normalised, not just the tenant, because every slot is a partition key.
        # Keeping the raw value gives "  ada  " and "ada" different keys, so one user reads and
        # writes two disjoint sets of rows on incidental whitespace upstream -- and `user_id=""`
        # counts as *set* to the isolation guard, narrowing reads to records whose user_id is
        # literally empty. `object.__setattr__` because the dataclass is frozen.
        #
        # Note this is a different argument from requiring `tenant_id`: that one is about unscoped
        # being the *widest* tenancy. The others fail closed, so they are a partitioning bug.
        for slot in ("tenant_id", "agent_id", "user_id", "team_id", "session_id"):
            value = getattr(self, slot)
            if value is None:
                continue
            if not isinstance(value, str):
                raise TypeError(f"Scope.{slot} must be a string or None, got {value!r}.")
            stripped = value.strip()
            if not stripped:
                raise ValueError(
                    f"Scope.{slot} is blank. Pass None to leave it unset -- a blank string is "
                    "treated as a set slot and narrows every read to records that also have it blank."
                )
            object.__setattr__(self, slot, stripped)

    def key(self) -> str:
        """Stable string key for storage partitioning and logging.

        ``*`` stands for an unset slot so the key is fixed-arity: two scopes differing only in
        which slots are set can never collide.

        Slot values are percent-escaped, because fixed arity alone is not injectivity. An ordinary
        ``/`` inside a tenant-supplied identifier merged two adjacent slots --
        ``agent_id="b/c", user_id="d"`` and ``agent_id="b", user_id="c/d"`` produced one key -- and
        any code that groups by this string then files one scope's data under the other.
        """
        return "/".join(
            [
                _escape(self.tenant_id),
                _escape(self.agent_id),
                _escape(self.user_id),
                _escape(self.team_id),
                _escape(self.session_id),
            ]
        )


@dataclass(frozen=True)
class TemporalBounds:
    """What the graph believes about one record's validity, independent of any ranking.

    The answer shape of ``GraphSubstrate.bounds_for_ids``. Deliberately not a ``MemoryRecord``:
    the caller already has the content and only needs the four bi-temporal fields, and returning a
    full record would invite treating an authority lookup as a second read path.
    """

    t_valid: Optional[int] = None
    t_invalid: Optional[int] = None
    expired_at: Optional[int] = None
    superseded_by: Optional[str] = None


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
        # `id` is the cross-substrate dedup key, so an empty one silently collapses every
        # unidentified record into a single merged result. Cheap to reject, and the seed bridge's
        # `memory_id or ""` was one keystroke from producing exactly that.
        if not isinstance(self.id, str) or not self.id:
            raise ValueError(f"MemoryRecord.id must be a non-empty string; it is the dedup key. Got {self.id!r}.")
        # `tier` and `scope` are load-bearing far from here -- `tier` in routing and the ranking
        # key, `scope` in the isolation post-condition. A duck-typed `scope` slipped past that
        # post-condition entirely and then died inside a sort key, three frames from the mistake.
        if not isinstance(self.tier, MemoryTier):
            raise TypeError(f"MemoryRecord.tier must be a MemoryTier, got {self.tier!r}.")
        if not isinstance(self.scope, Scope):
            raise TypeError(f"MemoryRecord.scope must be a Scope; the isolation check reads it. Got {self.scope!r}.")
        self.t_valid = _epoch_or_none(self.t_valid)
        self.t_invalid = _epoch_or_none(self.t_invalid)
        self.created_at = _epoch_or_none(self.created_at)
        self.expired_at = _epoch_or_none(self.expired_at)
        # An inverted valid-time interval is empty at every instant, so the record is written,
        # receipted, and then invisible forever with no error anywhere -- the same silent-loss
        # shape `_epoch_or_none` rejects a naive datetime for.
        # `>=`, not `>`: the valid-time predicate is half-open (`t_valid <= at < t_invalid`), so an
        # interval whose ends are *equal* is empty at every instant too -- written, receipted, and
        # then invisible forever with no error anywhere.
        if self.t_valid is not None and self.t_invalid is not None and self.t_valid >= self.t_invalid:
            raise ValueError(
                f"MemoryRecord t_valid ({self.t_valid}) is not before t_invalid ({self.t_invalid}), so "
                "the record is valid at no instant and no read can ever return it. Valid time is "
                "half-open: t_valid <= at < t_invalid."
            )
        self.provenance = dict(self.provenance or {})
        self.metadata = dict(self.metadata or {})
        # `topics` is copied here, with the other two, so the class is closed rather than the
        # instance: the seed read path builds records straight from stored rows and never calls
        # `detached()`, so a consumer appending to `record.topics` wrote into the store.
        self.topics = list(self.topics) if self.topics is not None else None

    def is_current_belief(self) -> bool:
        """Whether this row is what the system believes *now* (``expired_at IS NULL``).

        Distinct from being *valid at a time* -- an expired row can still describe a fact that was
        true, and a current row can describe a fact that has since become false. Point-in-time
        reads need both predicates (SM-R10 section 7 5a).
        """
        return self.expired_at is None

    def detached(self) -> "MemoryRecord":
        """A copy safe to rank, annotate, and hand out.

        A substrate may return the row it is holding. Mutating that -- which merging scores does --
        would write into the store through the read path.

        ``deepcopy`` rather than ``dict()``: one level leaves nested values shared, and the
        realistic case is a consumer appending to ``provenance["source_episode_ids"]``, which is
        the exact shape consolidation writes.
        """
        return replace(
            self,
            score=self.score or 0.0,
            topics=list(self.topics) if self.topics is not None else None,
            provenance=deepcopy(self.provenance),
            metadata=deepcopy(self.metadata),
        )

    def apply_bounds(self, bounds: "TemporalBounds") -> None:
        """Adopt the graph's authoritative bounds, overwriting whatever the projection carried.

        An **authority** overwrite, not a merge: ``GraphSubstrate`` is the sole authority for
        validity (ADR 0003 D8), so where it has an answer the vector projection's stale one does
        not get a vote. ``t_valid`` is the exception -- the graph may not carry one for a record it
        only knows as an edge, so a missing lower bound leaves the projection's in place.
        """
        self.t_valid = bounds.t_valid if bounds.t_valid is not None else self.t_valid
        self.t_invalid = bounds.t_invalid
        self.expired_at = bounds.expired_at
        self.superseded_by = bounds.superseded_by


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
    tier: MemoryTier = DEFAULT_WRITE_TIER  # `tiers.TIER_POLICY` decides which one that is
    id: Optional[str] = None
    role: Optional[str] = None  # for message-shaped events (user / assistant / ...)
    topics: Optional[List[str]] = None
    occurred_at: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.occurred_at = _epoch_or_none(self.occurred_at)
        # `metadata=None` is the obvious way to say "no metadata" and used to crash inside an
        # adapter with a raw TypeError on one provider while the other accepted it. An envelope
        # that rejects a blank tenant, a bool timestamp and a negative limit should not leave this
        # one to explode three frames away.
        self.metadata = dict(self.metadata or {})


@dataclass
class FactWrite:
    """The argument shape for writing a distilled semantic fact to the graph substrate.

    Not a stored schema -- it composes :class:`MemoryRecord` (which carries content, id, scope,
    and the bi-temporal envelope) with the subject/predicate/object structure a graph write needs
    but an episode write does not.

    It is separate from an episode write because a fact written through the episode path becomes a
    flat, provenance-carrying pseudo-episode rather than the entity/relation shape SM-R7 intends
    (finding F9).

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
    #: Reserved. See ``__post_init__`` -- a non-empty value is refused rather than ignored.
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
        # An upper bound for the same reason `tenant_id` may not be blank: the widest possible
        # request should not be the easiest one to write by accident. `limit` is forwarded verbatim
        # to the substrates, where a nine-digit value is an unbounded scan behind an ANN index.
        if self.limit > MAX_LIMIT:
            raise ValueError(
                f"MemoryQuery.limit is {self.limit}, above the {MAX_LIMIT} ceiling. A limit this "
                "large is a full scan behind the substrate's index, not a page. Enumerate a tier "
                "with VectorSubstrate.scan instead."
            )
        # Range-checked like `limit`, and for the same reason: a negative instant is not a narrower
        # question, it is one no record can satisfy, so the read returns empty and looks like a miss.
        if self.as_of is not None and self.as_of < 0:
            raise ValueError(f"MemoryQuery.as_of must be a non-negative epoch second, got {self.as_of!r}.")

        # Element types, for the same reason `MemoryRecord` checks `tier` and `scope`: a bad member
        # otherwise dies three frames down, and the two collections failed *differently* --
        # `substrates=["banana"]` raised a raw KeyError inside the adapter while `tiers=["nope"]`
        # returned a clean empty slice, which is the silently-empty class this package refuses
        # everywhere else. Both are `str` enums, so coercing a valid value is a kindness; an
        # invalid one is named here.
        for field_name, raw, enum in (("tiers", self.tiers, MemoryTier), ("substrates", self.substrates, Substrate)):
            if raw is None:
                continue
            coerced = []
            for member in raw:
                try:
                    coerced.append(enum(member))
                except ValueError:
                    known = ", ".join(repr(m.value) for m in enum)
                    raise ValueError(
                        f"MemoryQuery.{field_name} contains {member!r}, which is not a {enum.__name__}. "
                        f"Expected one of: {known}."
                    ) from None
            setattr(self, field_name, tuple(coerced))

        # `filters` is declared but nothing reads it: no substrate port states a contract for it,
        # so even a future backend has nothing to conform to. In a seam whose threat model is
        # over-broad reads, a filter that silently does not apply returns an unfiltered slice that
        # *looks* filtered -- the failure class this package rejects at the boundary everywhere
        # else. Refused until SM-V1 (#95) defines the predicate semantics on the ports.
        if self.filters:
            raise ValueError(
                "MemoryQuery.filters is reserved and not yet applied by any substrate, so passing "
                "it would return an unfiltered result that looks filtered. Metadata filtering is "
                f"SM-V1's (#95). Got {self.filters!r}."
            )

    def resolved_tiers(self) -> Sequence[MemoryTier]:
        """The tier set to search: the requested one, or every persistent tier.

        Lives here, once, because every substrate must resolve it **identically** -- when one leg
        honoured the filter and the other did not, cross-tier leakage appeared only in the merged
        result and was hard to trace back (finding F3).

        ``None`` means "unspecified, use the default"; an **empty sequence means empty**. Conflating
        them widens rather than narrows, which is the direction that matters.

        ``substrates`` is *not* read the same way, and the asymmetry is deliberate: "search no
        tiers" is a coherent request with an empty answer, while "consult no substrates" is one
        nothing could ever answer, so a provider refuses it rather than returning an empty slice
        that looks like a miss.
        """
        return PERSISTENT_TIERS if self.tiers is None else tuple(self.tiers)


#: Keys every provider must put in ``MemorySlice.fusion``, whatever it does internally.
#:
#: Without a floor the adapters diverge on key names and a provider-agnostic consumer -- the swap
#: property this seam exists to provide -- raises ``KeyError`` on whichever one it was not written
#: against. A provider with nothing to merge reports zeros, which is honest: it has no legs, not an
#: unknown number of them.
FUSION_KEYS = (
    "method",
    "vector_hits",
    "graph_hits",
    "fused",
    "returned",
    "dropped_by_validity",
    "dropped_as_superseded",
    "bounds_reconciled",
)


def fusion_diagnostics(
    method: Optional[str],
    vector_hits: int = 0,
    graph_hits: int = 0,
    fused: int = 0,
    returned: int = 0,
    dropped_by_validity: int = 0,
    dropped_as_superseded: int = 0,
    bounds_reconciled: bool = False,
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
        # All three in the floor rather than adapter extras. Without `dropped_by_validity` a short
        # page is ambiguous between an empty corpus and one the filter emptied, and only one of
        # those is a bug. Its transaction-time sibling belongs beside it, or a caller cannot tell
        # "the world moved on" from "a newer revision exists". And `bounds_reconciled` is the one a
        # caller would actually branch on -- a slice no graph vouched for carries whatever the
        # vector projection had. A provider with no graph reports False and zeros, which is honest.
        "dropped_by_validity": dropped_by_validity,
        "dropped_as_superseded": dropped_as_superseded,
        "bounds_reconciled": bounds_reconciled,
    }
    diagnostics.update(extra)
    return diagnostics


#: Keys every provider must put in ``ConsolidationReport.details``.
#:
#: What a consolidation pass did, and did not do. ``supersede`` names the second in so many words:
#: no SM-C1 provider retires the rows it subsumes, and a scheduler must not have to infer that.
NO_SUPERSEDE = "not implemented in SM-C1 -- SM-L3 (#124)"

#: The same floor ``FUSION_KEYS`` provides, for the scheduler SM-L1 (#122) will write. A provider
#: with nothing to report says so rather than omitting the key.
CONSOLIDATION_KEYS = ("derived_by", "complete", "supersede")


def consolidation_details(
    derived_by: Optional[str],
    complete: bool = True,
    supersede: str = NO_SUPERSEDE,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a ``ConsolidationReport.details`` payload carrying every :data:`CONSOLIDATION_KEYS`.

    ``complete`` is what a scheduler actually needs: whether the tier was drained. ``supersede``
    is the other thing it cannot infer -- whether the pass retires the rows it subsumes. In the
    floor rather than an adapter extra, because one provider silently leaving duplicates while
    another claims a retirement is exactly the divergence the floor exists to prevent.

    Extra keys are free; the contract is a floor, not a ceiling.
    """
    details: Dict[str, Any] = {"derived_by": derived_by, "complete": complete, "supersede": supersede}
    details.update(extra)
    return details


@dataclass
class MemorySlice:
    """The feed-forward result of ``read`` -- the memory handed to the agent.

    ``records`` is the merged, ranked list; ``by_tier`` groups the same objects for callers that
    inject per tier; ``fusion`` carries the per-leg diagnostics (see :data:`FUSION_KEYS`) that make
    a ranking explainable after the fact.
    """

    scope: Scope
    query: MemoryQuery
    records: List[MemoryRecord] = field(default_factory=list)
    by_tier: Dict[MemoryTier, List[MemoryRecord]] = field(default_factory=dict)
    fusion: Dict[str, Any] = field(default_factory=dict)

    def as_context(self) -> str:
        """Render the slice as a prompt-injectable string.

        Parallels ``LearningStore.build_context`` (``learn/stores/protocol.py:78``) so a slice
        drops into the same place in an agent prompt that the seed's learn context does.

        Line breaks inside a record's content are indented rather than emitted raw. The ``[tier]``
        prefix is line-oriented, so stored content containing ``"\\n[semantic] ..."`` would
        otherwise render as an additional, forged memory line -- and memory content is attacker-
        influenced by construction, since it is whatever a user said to the agent. Continuation
        lines are visibly indented, so one memory always reads as one entry.

        Splitting uses ``str.splitlines()`` rather than ``split("\\n")`` because that is what the
        *consumer* treats as a line break. ``splitlines`` covers eleven separators; hand-rolling
        the common two leaves ``\\x0b``, ``\\x0c``, ``\\x1c``-``\\x1e``, ``\\x85``, ``U+2028`` and
        ``U+2029`` through, and each one forges a ``[tier]`` line. Matching the consumer's escape
        set closes the class rather than the instance.
        """
        if not self.records:
            return ""
        lines = []
        for record in self.records:
            head, *rest = record.content.splitlines() or [""]
            lines.append("[{tier}] {head}".format(tier=record.tier.value, head=head))
            lines.extend("    " + line for line in rest)
        return "\n".join(lines)


#: Keys every provider must put in ``WriteReceipt.details``, the same floor ``FUSION_KEYS`` and
#: ``CONSOLIDATION_KEYS`` provide for the other two result types.
#:
#: ``stores`` exists because ``accepted`` is one number and a tier can be served by several backends
#: that answer differently. A provider composing substrates has none, and reports ``{}``, which is
#: honest rather than empty-by-omission.
WRITE_KEYS = ("stores",)


def write_details(stores: Optional[Dict[str, bool]] = None, **extra: Any) -> Dict[str, Any]:
    """Build a ``WriteReceipt.details`` payload carrying every key in :data:`WRITE_KEYS`.

    ``stores`` maps a backend's own name to whether it took this batch. Extra keys are free; the
    contract is a floor, not a ceiling.
    """
    details: Dict[str, Any] = {"stores": dict(stores or {})}
    details.update(extra)
    return details


@dataclass
class WriteReceipt:
    """What a ``write`` accepted and where it landed.

    ``event_ids`` are the ids the provider stamped -- the caller needs them to correlate a later
    read, and consolidation needs them for ``source_episode_ids`` provenance.

    ``accepted`` is **tier-level**: how many events this write was taken by, not how many rows
    exist. A backend may fold a batch into one row, and a tier may be served by several backends.
    ``details`` (see :data:`WRITE_KEYS`) is where that breakdown lives, so "two of three stores took
    it" is readable rather than collapsed into the count.
    """

    accepted: int = 0
    event_ids: List[str] = field(default_factory=list)
    tiers_written: List[MemoryTier] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Filled rather than required, so a hand-built receipt in a test or a third-party adapter
        # still satisfies the floor a consumer reads.
        #
        # **Merged on key presence, not on truthiness.** A receipt built as
        # ``WriteReceipt(details={"rows": 3})`` -- the extra-keys-are-free usage the docstring
        # invites -- is truthy, so a `not self.details` test skipped the fill and left
        # ``details["stores"]`` raising `KeyError` at the call site. That is precisely the
        # divergence the floor exists to prevent, reachable only from out-of-tree adapters, which
        # are the ones with no test of ours to catch it.
        if not self.details.keys() >= set(WRITE_KEYS):
            self.details = write_details(**self.details)


@dataclass
class ConsolidationReport:
    """The outcome of one consolidate pass: episodic -> semantic.

    The write-side loop is the differentiator of this design and the least-solved part of the field
    (research doc section 5.3): extract -> dedup -> contradiction-resolve -> write to **both**
    substrates, on the sleep-time lane.

    **One write model.** Any contradiction this produces on the graph resolves through the same
    SM-R10 expire-and-reinsert (SCD-2) primitive as ``GraphSubstrate.invalidate``, never a second
    in-place path. Per ADR 0003 section B.6, SCD-2 governs semantic contradiction on graph
    **edges**; episodic **row** lifecycle (SM-R6 re-segmentation, SM-R5 decay archival) is row-level
    ``expired_at``. Both are append-only; they are different objects, not one path.
    """

    scope: Scope
    episodic_scanned: int = 0
    semantic_written: int = 0
    duplicates_merged: int = 0
    contradictions_resolved: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
