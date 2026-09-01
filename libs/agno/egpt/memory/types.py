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

import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from egpt.memory.errors import MemoryOperationError, MemoryScopeRequiredError
from egpt.memory.tiers import DEFAULT_WRITE_TIER, PERSISTENT_TIERS, MemoryTier
from egpt.utils.dttm import to_epoch_s

__all__ = [
    "CONSOLIDATION_KEYS",
    "FUSION_KEYS",
    "MAX_LIMIT",
    "NO_SUPERSEDE",
    "consolidation_details",
    "ConsolidationReport",
    "entity_extraction_complete",
    "entity_extraction_ran",
    "EntityMerge",
    "EntityWrite",
    "escape_key_slot",
    "FactWrite",
    "fusion_diagnostics",
    "MemoryEvent",
    "MemoryOperationError",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryScopeRequiredError",
    "MemorySlice",
    "NO_ENTITIES",
    "NO_PROFILE",
    "NO_REFLECTION",
    "NO_RELATIONS",
    "NO_SUMMARY",
    "normalize_entity_name",
    "PartialWriteError",
    "PROFILE_NO_USER",
    "render_context_lines",
    "require_scope",
    "RetrievalCandidates",
    "SCOPE_SLOTS",
    "ScanCursor",
    "Scope",
    "Substrate",
    "TemporalBounds",
    "ValidWindow",
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
    idempotent provided the substrate upserts by id (see ``GraphSubstrate.upsert_episodes``).

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

    # BM25/lexical retrieval is gated on this member, not a member of its own: a store
    # implementing `HybridSearchSubstrate` (SM-V3 #97) returns both the vector and lexical legs
    # from one `search_candidates` call, feature-detected by the orchestrator (SM-H1 #131) rather
    # than requested by name. There is no `Substrate.LEXICAL` -- adding one, and a way to ask for
    # vector without BM25, is deferred to SM-H2 (#132) if a caller ever needs it.
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


def _require_topics_list(value: Any, owner: str) -> None:
    """Refuse a non-list ``topics`` rather than coercing it.

    ``list("deploy")`` is six one-character topics and raises nothing -- a *well-formed* list, so
    no downstream guard can detect the corruption (issue #181, residual from SM-E1 #108's
    ``schemas/episodic.py::_require_list``, which caught this for the episode projection only). A
    non-iterable such as ``topics=42`` would otherwise raise a bare ``TypeError`` from inside
    ``list()`` instead of the named refusal every other boundary in this module raises.

    Guarded once, here, on both envelope types, rather than at each of the six call sites that
    copy ``topics`` downstream: PR #76 round 2 showed a fix applied to some sites and not others
    is worse than the original defect. Those six sites keep their ``list(...)`` copies -- with
    this guard upstream, each is copying a value already known to be a list.

    Construction-time only: neither envelope is frozen, so this does not stop a caller from
    setting ``event.topics = "deploy"`` after the fact. Nothing downstream re-checks the type --
    ``episode_from_event`` trusts it unconditionally once past ``__post_init__``. No caller in
    this codebase mutates ``topics`` post-construction today.
    """
    if value is not None and not isinstance(value, list):
        raise MemoryOperationError(
            f"{owner}.topics must be a list, got {type(value).__name__}: {value!r}. Copying it "
            "would risk splitting a string into one-character topics instead of failing.",
            field_name="topics",
        )


def escape_key_slot(value: Optional[str]) -> str:
    """One slot of :meth:`Scope.key`, with the separator made unambiguous.

    Three characters are escaped. ``%`` first, or escaping the others would itself be escapable
    into a collision; ``/`` because it separates slots; and ``*`` because it is the unset-slot
    sentinel -- ``agent_id="*"`` must not produce the same slot as an unset ``agent_id``, so it
    becomes ``%2A``.

    Public because :func:`egpt.memory.schemas.episodic.graph_node_id_for` extends a scope key with
    a sixth slot and must escape it the same way; two implementations of one escaping rule is how
    the two keys start disagreeing about which characters are separators.
    """
    if value is None:
        return "*"
    # `%` first, or escaping the others would itself be escapable into a collision. `*` is escaped
    # because it is the unset sentinel: without it `agent_id="*"` and an unset agent_id produce the
    # same key, which is the injectivity this function exists for.
    return value.replace("%", "%25").replace("/", "%2F").replace("*", "%2A")


#: Non-word, non-space characters, which :func:`normalize_entity_name` drops.
_ENTITY_PUNCTUATION = re.compile(r"[^\w\s]")
_ENTITY_WHITESPACE = re.compile(r"\s+")


def normalize_entity_name(name: str) -> str:
    """The comparison form of an entity's surface name -- lowercased, unpunctuated, single-spaced.

    This is :class:`EntityWrite`'s ``key``: the *exact-match* grouping SM-G8 (#106) starts from, not
    the resolution itself. "Acme Corp." and "acme corp" collapse here; "Acme" and "Acme Corporation"
    do not, and merging those is SM-G8's embedding match.

    The interior transform -- lowercase, drop punctuation, collapse whitespace -- is
    ``Curator._normalize``'s (``learn/curate.py``), which SM-G8 is assigned to reuse. Restated
    rather than imported because that one is a private method on a class needing a wired
    ``LearningMachine``. ``test_entity_normalization_matches_the_curator`` asserts the two agree,
    including the one point where this function goes further: it strips the surrounding whitespace
    a stripped-punctuation edge leaves (``"! hi !"`` -> ``"hi"``, not ``" hi "``), because this is a
    merge key and a key carrying whitespace it need not is a distinct key from one that does not --
    a second rule for one concept is how two entities that should have merged stop.
    """
    if not isinstance(name, str):
        raise MemoryOperationError(f"An entity name must be a string, got {name!r}.", field_name="name")
    collapsed = _ENTITY_WHITESPACE.sub(" ", _ENTITY_PUNCTUATION.sub("", name.lower().strip())).strip()
    if not collapsed:
        # A name that is entirely punctuation normalises to "", which would make every such entity
        # one candidate and file unrelated facts under it. Refused here rather than at the write,
        # so an extractor's junk output cannot reach the graph under a shared empty key.
        raise MemoryOperationError(
            f"Entity name {name!r} normalises to empty, so it cannot identify an entity.",
            field_name="name",
        )
    return collapsed


#: Every partition slot a memory row carries, in :meth:`Scope.key` order.
#:
#: One declaration because four sites had written the tuple out by hand -- the normalisation loop
#: below, the co-located provider's isolation guard, the Neo4j statement builders, and the seed's
#: narrowing check -- and a slot added to :class:`Scope` reaches a predicate only in the copies
#: someone remembered. ``tenant_id`` is not special-cased in any of them: a backend that honours
#: the tenant and drops ``user_id`` serves one user's memory to another, which is the same leak one
#: boundary in.
SCOPE_SLOTS = ("tenant_id", "agent_id", "user_id", "team_id", "session_id")


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
        for slot in SCOPE_SLOTS:
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

        Built from :data:`SCOPE_SLOTS` rather than a hand-written list, so a slot added to this
        dataclass widens the key instead of colliding with the scopes that lack it.
        """
        return "/".join(escape_key_slot(getattr(self, slot)) for slot in SCOPE_SLOTS)


def require_scope(scope: Any, *, method: str) -> Scope:
    """Refuse a memory data-access call that carries no usable tenant scope.

    ``Scope.__post_init__`` rejects a blank tenant, so a well-formed :class:`Scope` is already
    proof of scoping. What it cannot rule out is a caller that never constructed one: ``None``,
    an object that merely has the right attribute names, or a subclass whose ``__post_init__``
    does not run the tenant check. Each of those reaches a backend as whatever it is -- and the
    two that carry a ``tenant_id`` at all reach it **unnormalised**, so a stray ``"acme "``
    becomes a distinct tenant to any equality predicate, writing rows no trimmed caller can name
    again.

    Fails at the seam rather than in the backend so the refusal names the method the caller used.
    Without it the same mistake surfaced as an ``AttributeError`` from inside a driver's statement
    builder, several frames from anything the caller controls, and on a lenient backend it would
    surface as a wide answer instead.

    Args:
        scope: What the caller passed. Deliberately typed ``Any``: annotations are the thing being
            checked, so narrowing it here would state the assumption instead of testing it.
        method: The calling method's name, for the error message.

    Returns:
        The same :class:`Scope`. Callers use it as a bare statement rather than rebinding, because
        it validates in place and returns the identical object -- there is nothing to rebind. The
        return exists for a caller holding a loosely-typed value who wants the narrowing.

    Raises:
        MemoryScopeRequiredError: If ``scope`` is not a ``Scope``, carries a blank tenant, or
            carries any slot ``Scope.__post_init__`` would have rejected or normalised.
    """
    if not isinstance(scope, Scope):
        raise MemoryScopeRequiredError(
            f"{method}() requires a Scope and got {type(scope).__name__}. Memory is tenant-"
            "partitioned at the data layer, and an unscoped call reads and writes every tenant's "
            "rows. Construct egpt.memory.Scope(tenant_id=...), which normalises every slot; an "
            "object that merely looks like one reaches the backend untrimmed.",
            field_name="scope",
            verb=method,
        )
    # A subclass may define its own `__post_init__` and never reach `Scope`'s, so re-assert what
    # that method guarantees. **Every slot, not just the tenant**: the tenant is the one that
    # crosses a trust boundary, but `user_id="  u1  "` reaches a predicate unnormalised and
    # matches no row written under `"u1"` -- a partition split on incidental whitespace, which is
    # the hazard `Scope.__post_init__` normalises away and the reason it normalises all five.
    #
    # Validated, not normalised. `Scope` is frozen, and rewriting a caller's object here would
    # make the fix invisible at the call site while `Scope` itself stayed the only place that
    # normalises -- two places doing it differently is what this whole module exists to prevent.
    if not isinstance(scope.tenant_id, str) or not scope.tenant_id.strip():
        raise MemoryScopeRequiredError(
            f"{method}() was called with Scope.tenant_id={scope.tenant_id!r}. A blank tenant is "
            "unscoped, and unscoped is the widest tenancy rather than the narrowest.",
            field_name="tenant_id",
            verb=method,
        )
    for slot in SCOPE_SLOTS:
        value = getattr(scope, slot, None)
        if value is None or (isinstance(value, str) and value == value.strip() and value):
            continue
        raise MemoryScopeRequiredError(
            f"{method}() was called with Scope.{slot}={value!r}, which Scope.__post_init__ would "
            "have rejected or trimmed. Pass None to leave a slot unset; a blank or padded slot "
            "partitions on whitespace and matches no row written under the trimmed value.",
            field_name=slot,
            verb=method,
        )
    return scope


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


@dataclass(frozen=True)
class ValidWindow:
    """A half-open valid-time window ``[valid_from, valid_to)``, and the overlap rule for it.

    The rule lives here, once, because SM-V4 (#98) applies it in two encodings: the vector
    substrate pushes it into SQL so it narrows *before* ``LIMIT``, and the provider re-applies it
    to the merged set after the graph's authoritative bounds land. Two encodings of one rule is
    how a filter comes to mean different things per leg, so
    ``tests/unit/memory/test_valid_window.py`` drives both over the same case table.

    ``None`` is open at that end, on the window and on the record alike -- the same convention the
    point predicate already used, so a point read is the degenerate window ``[at, at + 1)`` rather
    than a second rule that happens to agree.
    """

    valid_from: Optional[int] = None
    valid_to: Optional[int] = None

    def __post_init__(self) -> None:
        # `int`, like every other epoch-seconds field in this module (`_epoch_or_none` enforces it
        # for `MemoryQuery`'s bounds and every `MemoryRecord` timestamp) -- and `bool` excluded for
        # the same reason it is there: `bool` is an `int` subclass, so `ValidWindow(True, ...)`
        # would silently become `ValidWindow(1, ...)`. Constructed directly rather than through
        # `MemoryQuery`, this type gets none of that module's coercion, so a caller assembling one
        # for SM-V3's legs from a float timestamp -- a `time.time()` result is the obvious source --
        # would bind a float into the SQL predicate. `t_invalid > 100.5` and `t_invalid > 100` agree
        # everywhere except a record stamped exactly `100`, which is precisely the boundary
        # `test_valid_window.py` exists to get right, and silently disagree only there.
        for name, value in (("valid_from", self.valid_from), ("valid_to", self.valid_to)):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise TypeError(f"ValidWindow.{name} must be an int epoch second, got {value!r}.")
        # `MemoryQuery` rejects the same shape, but this type is exported and constructed directly
        # -- by the tests here, and by whatever assembles a window for SM-V3's legs. An inverted
        # window does not merely match nothing, which would at least be uniform: `overlaps` checks
        # each end independently, so `[200, 50)` still admits a record with unset bounds and
        # rejects its neighbours. That is a wrong answer, not an empty one.
        if self.valid_from is not None and self.valid_to is not None and self.valid_from >= self.valid_to:
            raise ValueError(
                f"ValidWindow [{self.valid_from}, {self.valid_to}) is empty or inverted. The window "
                "is half-open, so valid_to must be strictly greater than valid_from."
            )

    @classmethod
    def at(cls, instant: int) -> "ValidWindow":
        """The window a point-in-time read is: ``[instant, instant + 1)``."""
        return cls(valid_from=instant, valid_to=instant + 1)

    def overlaps(self, t_valid: Optional[int], t_invalid: Optional[int]) -> bool:
        """Whether record interval ``[t_valid, t_invalid)`` intersects this window.

        Both ends are checked. Testing only ``t_invalid`` lets a future-dated record answer about
        the past, which is the defect the point predicate carried a comment about before this
        generalised it.
        """
        if self.valid_to is not None and t_valid is not None and t_valid >= self.valid_to:
            return False
        if self.valid_from is not None and t_invalid is not None and t_invalid <= self.valid_from:
            return False
        return True


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
        _require_topics_list(self.topics, "MemoryRecord")
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

    ``entities`` are the entity names this event mentions, and the graph substrate links the
    episode node to each of them (SM-G6 #104). They are **supplied, not derived**: deciding which
    entities a piece of content names is extraction, which is SM-G2's (#100), so this field is the
    carrier and nothing here reads ``content`` to fill it. ``None`` means the caller said nothing,
    which is not the same as an episode that mentions nobody.
    """

    content: str
    scope: Scope
    tier: MemoryTier = DEFAULT_WRITE_TIER  # `tiers.TIER_POLICY` decides which one that is
    id: Optional[str] = None
    role: Optional[str] = None  # for message-shaped events (user / assistant / ...)
    topics: Optional[List[str]] = None
    entities: Optional[List[str]] = None
    occurred_at: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.occurred_at = _epoch_or_none(self.occurred_at)
        # `metadata=None` is the obvious way to say "no metadata" and used to crash inside an
        # adapter with a raw TypeError on one provider while the other accepted it. An envelope
        # that rejects a blank tenant, a bool timestamp and a negative limit should not leave this
        # one to explode three frames away.
        self.metadata = dict(self.metadata or {})
        _require_topics_list(self.topics, "MemoryEvent")
        # Copied, not merely validated, for the same reason `MemoryRecord` copies it: a caller
        # batching several events from one shared `topics` list (`writeback.py`'s
        # `event_from_message` / `event_from_tool_execution`, built for `events_from_run`) must
        # not have every already-built event's topics mutate together if that list changes after
        # this call.
        self.topics = list(self.topics) if self.topics is not None else None


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
class EntityWrite:
    """One extracted entity, as :meth:`GraphSubstrate.upsert_entities` takes it (SM-G2 #100).

    **Flat, where :class:`FactWrite` composes a** :class:`MemoryRecord`. ADR 0003 section 1 makes
    ``SemanticEntity`` embeddable but **not** bi-temporal -- an entity's identity is stable, so it
    has no valid-time interval -- and wrapping a record here would hand every caller four temporal
    fields the graph must then refuse to store.

    Field names mirror SM-C3's ``SemanticEntity`` so the projection is 1:1. ``key`` is derived from
    ``name`` rather than accepted, because a caller-chosen grouping key is a caller-chosen
    duplicate: two writers spelling the same normalisation differently produce two candidates that
    SM-G8 (#106) then has to re-merge.

    ``source_episode_ids`` is the provenance SM-R7 section 7 makes load-bearing, and it is
    **unioned** rather than replaced when an entity is seen again -- see
    ``cypher.upsert_entities_statement``.
    """

    name: str
    entity_type: str
    scope: Scope
    key: str = field(init=False)
    description: Optional[str] = None
    #: Other surface forms seen for this entity. Retaining them across a merge is SM-G8's (#106)
    #: acceptance criterion; carrying them is this ticket's.
    aliases: List[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    source_episode_ids: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None
    embedding_version: Optional[str] = None
    confidence: float = 1.0
    derived_by: Optional[str] = None  # the extractor that produced it

    def __post_init__(self) -> None:
        for field_name in ("name", "entity_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MemoryOperationError(
                    f"EntityWrite.{field_name} must be a non-empty string; the graph node is "
                    f"addressed by it. Got {value!r}.",
                    field_name=field_name,
                )
            setattr(self, field_name, value.strip())
        if not isinstance(self.scope, Scope):
            raise MemoryOperationError(
                f"EntityWrite.scope must be a Scope; the isolation check reads it. Got {self.scope!r}.",
                field_name="scope",
            )
        # Derived after `name` is stripped, so trailing whitespace cannot produce a second key.
        self.key = normalize_entity_name(self.name)
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise MemoryOperationError(
                f"EntityWrite.confidence must be a number in [0.0, 1.0], got {self.confidence!r}.",
                field_name="confidence",
            )
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise MemoryOperationError(
                f"EntityWrite.confidence is {self.confidence}, outside [0.0, 1.0]. A ranking that "
                "mixes it with a similarity would be decided by whichever scale is larger.",
                field_name="confidence",
            )
        self.confidence = float(self.confidence)
        if self.embedding is not None:
            if not isinstance(self.embedding, list) or not self.embedding:
                raise MemoryOperationError(
                    "EntityWrite.embedding must be a non-empty list of floats, or None when the "
                    f"candidate is not embedded. Got {self.embedding!r}.",
                    field_name="embedding",
                )
            # An embedder returning strings stores a vector no similarity can read, and the failure
            # surfaces at SM-G8's match rather than at the write that caused it.
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in self.embedding):
                raise MemoryOperationError(
                    "EntityWrite.embedding contains a non-numeric component, so no similarity can "
                    "be computed against it.",
                    field_name="embedding",
                )
            self.embedding = [float(value) for value in self.embedding]
        # Sorted, not merely copied: these two are sets by meaning, and an extractor's ordering is
        # whatever the model emitted, so an unsorted copy makes two identical candidates compare
        # unequal and re-write the node for nothing.
        #
        # The entity's own surface form is never its own alias (review finding on PR #89, round 2):
        # it is already the merge key, and carrying it in both places makes a candidate look like it
        # was seen under two spellings when it was seen under one. Filtered here, not only at write
        # time, so code reading ``.aliases`` between construction and write (``_merged``'s
        # concatenation in ``memory/graph/entities.py``) never sees it either.
        self.aliases = sorted(
            {
                alias.strip()
                for alias in self.aliases
                if isinstance(alias, str) and alias.strip() and alias.strip() != self.name
            }
        )
        self.source_episode_ids = sorted(set(self.source_episode_ids))
        self.properties = dict(self.properties or {})

    def with_embedding(self, vector: Sequence[float], version: Optional[str]) -> "EntityWrite":
        """A copy carrying ``vector``, validated by the same ``__post_init__`` as any other write.

        A method rather than an assignment at the call site, because assigning the field directly
        skips every check above -- and an embedder returning strings then stores a vector no
        similarity can read, surfacing at SM-G8's (#106) match rather than at this write.
        """
        return replace(self, embedding=list(vector), embedding_version=version)


@dataclass
class EntityMerge:
    """One resolution decision, as :meth:`GraphSubstrate.merge_entities` takes it (SM-G8 #106).

    ``canonical`` is the surviving node's write -- name, type and the fields entity resolution
    folded together (aliases, ``source_episode_ids``). It is written through the same union rule
    :meth:`GraphSubstrate.upsert_entities` already uses, so a canonical name that happens to match
    an existing node merges into it rather than overwriting it.

    ``duplicate_names`` are the *other* surface forms being folded into ``canonical`` -- **not**
    ``canonical.aliases``, which records what a merge already decided; this is the instruction to
    decide it. Every edge into a duplicate node (``MENTIONS`` today; a relation edge once SM-G3
    #101 ships) is re-pointed at the canonical node, and the duplicate node is then removed -- so a
    name appearing here must never also be ``canonical.name``, or the canonical node would be asked
    to absorb itself.
    """

    canonical: EntityWrite
    duplicate_names: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.canonical, EntityWrite):
            raise MemoryOperationError(
                f"EntityMerge.canonical must be an EntityWrite; the merge writes it through the "
                f"same union rule as upsert_entities. Got {self.canonical!r}.",
                field_name="canonical",
            )
        if not self.duplicate_names:
            raise MemoryOperationError(
                "EntityMerge.duplicate_names is empty. A merge with nothing to fold in is not a "
                "merge -- omit it, or the canonical node is asked to absorb itself for nothing.",
                field_name="duplicate_names",
            )
        cleaned: List[str] = []
        seen = set()
        for name in self.duplicate_names:
            if not isinstance(name, str) or not name.strip():
                raise MemoryOperationError(
                    f"EntityMerge.duplicate_names contains {name!r}, which is not a non-empty "
                    "string. A blank name addresses no node.",
                    field_name="duplicate_names",
                )
            stripped = name.strip()
            if stripped == self.canonical.name:
                raise MemoryOperationError(
                    f"EntityMerge.duplicate_names names {stripped!r}, which is also "
                    "canonical.name -- the canonical node cannot be a duplicate of itself.",
                    field_name="duplicate_names",
                )
            if stripped not in seen:
                seen.add(stripped)
                cleaned.append(stripped)
        self.duplicate_names = cleaned


@dataclass
class MemoryQuery:
    """A read request.

    ``tiers`` is what issue #89 calls ``types``; ``None`` means
    :data:`~egpt.memory.tiers.PERSISTENT_TIERS`. ``substrates`` defaults to whichever the provider
    has wired. ``as_of`` requests a point-in-time read and is enforced on the **fused** result set,
    not per-substrate -- the vector substrate has no notion of validity, so filtering it there
    lets an invalidated fact leak back in through the other leg (finding F2).

    ``valid_from`` / ``valid_to`` narrow to records whose valid time **overlaps** the half-open
    window (SM-V4 #98), and are a different question from ``as_of``. ``as_of`` asks what was
    believed true at one instant and is answered on the merged set because only the graph is
    authoritative about it; a window asks which records are candidates at all, so a substrate
    applies it in its own ``WHERE`` clause. Applying it after ``limit`` would return a short page
    for the same reason over-fetching cannot fix the ``as_of`` residual -- SM-V4 decision D1.
    """

    text: str
    tiers: Optional[Sequence[MemoryTier]] = None
    substrates: Optional[Sequence[Substrate]] = None
    limit: int = 10
    as_of: Optional[int] = None  # point-in-time read (epoch s, SM-R10)
    valid_from: Optional[int] = None  # inclusive epoch s, SM-V4
    valid_to: Optional[int] = None  # exclusive epoch s, SM-V4
    #: Reserved. See ``__post_init__`` -- a non-empty value is refused rather than ignored.
    filters: Optional[Dict[str, Any]] = None

    def window(self) -> Optional["ValidWindow"]:
        """The requested valid-time window, or ``None`` when neither bound is set."""
        if self.valid_from is None and self.valid_to is None:
            return None
        return ValidWindow(valid_from=self.valid_from, valid_to=self.valid_to)

    def __post_init__(self) -> None:
        # The one field every read path touches, and the only one this envelope did not check.
        # `limit`, `as_of`, `tiers`, `substrates` and `filters` are all validated below, so a
        # non-string `text` was the single way to get past this constructor and die further in --
        # as `AttributeError: 'int' object has no attribute 'strip'` from inside a backend, which is
        # the shape every other check here exists to prevent. It also reaches a substrate as a bind
        # parameter, where the driver's own complaint names neither the field nor the caller.
        if not isinstance(self.text, str):
            raise TypeError(f"MemoryQuery.text must be a string, got {self.text!r}.")
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

        self.valid_from = _epoch_or_none(self.valid_from)
        self.valid_to = _epoch_or_none(self.valid_to)
        for bound_name, bound in (("valid_from", self.valid_from), ("valid_to", self.valid_to)):
            if bound is not None and bound < 0:
                raise ValueError(f"MemoryQuery.{bound_name} must be a non-negative epoch second, got {bound!r}.")
        # Half-open, so an empty window is `valid_from == valid_to` and not merely `>`. A caller
        # computing both ends from one timestamp lands on it, and an empty window overlaps nothing:
        # unrefused, the read *would* come back empty and read as "no such memories" rather than as
        # the bad request it is. Hence the raise below.
        if self.valid_from is not None and self.valid_to is not None and self.valid_from >= self.valid_to:
            raise ValueError(
                f"MemoryQuery valid-time window [{self.valid_from}, {self.valid_to}) is empty, so no "
                "record can overlap it and the read would return nothing rather than fail. The "
                "window is half-open: valid_to must be strictly greater than valid_from."
            )
        # One valid-time question per read. A provider applies whichever is set as *the* valid-time
        # filter, so accepting both would mean intersecting them -- and a caller who wanted the
        # intersection can write it as one window. Refused rather than intersected because the two
        # read as independent knobs: `as_of=<last year>` with a window around today looks like two
        # narrowings and is in fact empty, which would come back as a plain miss.
        if self.as_of is not None and (self.valid_from is not None or self.valid_to is not None):
            raise ValueError(
                f"MemoryQuery sets both as_of ({self.as_of}) and a valid-time window "
                f"[{self.valid_from}, {self.valid_to}). Both filter valid time, so a provider would "
                "have to intersect them and an empty intersection returns a plain miss. Ask one "
                "question: as_of for a point in time, valid_from/valid_to for a range."
            )

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

        # `filters` is declared but nothing reads it, and it stays that way after SM-V1 (#95) --
        # which stores metadata on every vector but does not filter on it. A read is merged from
        # two legs, so honouring a predicate on one alone returns a half-filtered page that looks
        # filtered; and the graph leg stores metadata as a JSON *string* property, so it could only
        # filter after `LIMIT`, which returns a short page for the same reason. Both are the
        # silently-wrong-answer class this package refuses at the boundary. Applying it needs a
        # predicate contract on both ports and a storage change on the graph side, which is its own
        # ticket rather than a line here.
        if self.filters:
            raise ValueError(
                "MemoryQuery.filters is reserved and not applied by any substrate, so passing it "
                "would return an unfiltered result that looks filtered. Metadata is stored on "
                "vectors (SM-V1 #95); filtering on it needs a predicate contract on the graph port "
                f"too, and has no owning ticket yet. Got {self.filters!r}."
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


@dataclass(frozen=True)
class RetrievalCandidates:
    """One store's ranked candidate lists, **one list per retrieval leg and deliberately unfused**
    (SM-V3 #97).

    Why the lists stay apart. Fusing them inside the store would hand the fusion stage a single
    ranking and no way to reconstruct the per-leg ranks it needs -- which is the whole complaint
    against ``PgVector.hybrid_search``, whose weighted blend is computed in SQL and returned as one
    ``similarity_score``. Rank-based fusion cannot be recovered from a blended score, and neither
    can a *re-weighted* blend, so a caller that wanted either had to re-run both searches.

    Which method fuses these is **not decided here and not decided by the store**. ADR 0003 section
    A.6 ratifies weighted / relative-score fusion for this two-leg case and holds the three-leg
    (+graph) choice open, gated on SM-G9 (#107); implementing either is SM-H2's (#132).

    Every record's ``score`` obeys the same contract as ``VectorSubstrate.search`` -- a similarity
    in ``[0.0, 1.0]``, higher is better. A leg whose native measure is a distance or an unbounded
    rank converts it, and must do so **monotonically**, so that the conversion cannot reorder its
    own list: rank-based fusion reads position and score-based fusion reads magnitude, and a
    non-monotone squash would quietly disagree with itself between the two.

    **``frozen=True`` stops the fields being rebound; it does not make the lists read-only.**
    Appending to ``candidates.vector`` works, and nothing here prevents it. That is left alone
    rather than defended: a store builds both lists fresh per call and keeps no reference, so the
    only object a caller can corrupt is its own -- and copying two ranked pages on every read to
    protect against that would tax the hot path for no reader's benefit. Stated because ``frozen``
    otherwise reads as a guarantee this class does not make. A fusion stage that wants to sort or
    truncate in place is welcome to; SM-H2 (#132) owns that call.
    """

    vector: List[MemoryRecord] = field(default_factory=list)
    lexical: List[MemoryRecord] = field(default_factory=list)

    def legs(self) -> Dict[str, List[MemoryRecord]]:
        """The lists keyed by leg name, so a **consumer** iterates legs rather than field names.

        SM-R8 requires the fusion implementations to be N-leg-capable rather than hardcoded to two
        (its ``RRFFusion`` / ``WeightedFusion`` already are), and a fusion function written against
        ``.vector`` and ``.lexical`` by name is hardcoded to two whatever it could otherwise take.

        **This does not make the container extensible, and should not be read as promising a third
        entry.** ``RetrievalCandidates`` describes what *one store* returned, and this store has two
        legs; the graph leg (SM-G9 #107) comes from a different substrate entirely, so it arrives as
        a separate result that the orchestrator (SM-H1 #131) fuses alongside this one -- not as a
        key that appears here later. What this method buys is that the fusion code consuming it does
        not have to change when that happens.
        """
        return {"vector": self.vector, "lexical": self.lexical}


#: Keys every provider must put in ``MemorySlice.fusion``, whatever it does internally.
#:
#: Without a floor the adapters diverge on key names and a provider-agnostic consumer -- the swap
#: property this seam exists to provide -- raises ``KeyError`` on whichever one it was not written
#: against. A provider with nothing to merge reports zeros, which is honest: it has no legs, not an
#: unknown number of them.
FUSION_KEYS = (
    "method",
    "vector_hits",
    "lexical_hits",
    "graph_hits",
    "fused",
    "returned",
    "dropped_by_validity",
    "dropped_as_superseded",
    "dropped_outside_window",
    "bounds_reconciled",
)


def fusion_diagnostics(
    method: Optional[str],
    vector_hits: int = 0,
    lexical_hits: int = 0,
    graph_hits: int = 0,
    fused: int = 0,
    returned: int = 0,
    dropped_by_validity: int = 0,
    dropped_as_superseded: int = 0,
    dropped_outside_window: int = 0,
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
        # A leg no wired `HybridSearchSubstrate` contributes reports 0 here -- the same "ran and
        # matched nothing" vs. "did not run" ambiguity `RetrievalLegs` names does not apply to a
        # count, so no separate marker is needed: 0 is honest whichever reason produced it.
        "lexical_hits": lexical_hits,
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
        # In the floor for the same reason as its two neighbours: a provider that cannot honour a
        # valid-time window refuses the read, so a zero here is a real zero and never "unknown".
        "dropped_outside_window": dropped_outside_window,
        "bounds_reconciled": bounds_reconciled,
    }
    diagnostics.update(extra)
    return diagnostics


#: The resumption cursor :meth:`~egpt.memory.substrates.VectorSubstrate.scan` accepts as ``after``:
#: an exclusive ``(created_at, id)`` bound in the same composite order ``scan`` sorts by. Composite
#: because ``created_at`` alone cannot page past a co-second batch (SM-L1 #122).
ScanCursor = Tuple[int, str]

#: Keys every provider must put in ``ConsolidationReport.details``.
#:
#: What a consolidation pass did, and did not do. ``supersede`` names the second in so many words:
#: no SM-C1 provider retires the rows it subsumes, and a scheduler must not have to infer that.
NO_SUPERSEDE = "not implemented in SM-C1 -- SM-L3 (#124)"

#: The honest default for ``entities``: a pass with no entity pipeline extracted none, which a
#: scheduler cannot tell from an episode set that mentioned none.
NO_ENTITIES = "not extracted -- no entity pipeline wired (SM-G2 #100)"

#: The ``relations`` counterpart to :data:`NO_ENTITIES` (SM-G3 #101): a pass with no relation
#: pipeline extracted none, which a scheduler cannot tell from an episode set that stated none.
#: ``details["relations"]`` is carried as a free extra key on ``consolidation_details``, not a
#: :data:`CONSOLIDATION_KEYS` floor member -- promoting it is a separate, deliberate decision this
#: ticket does not make (see ``co_located.RelationSummary``'s docstring).
NO_RELATIONS = "not extracted -- no relation pipeline wired (SM-G3 #101)"

#: The ``reflection`` counterpart to :data:`NO_ENTITIES` (SM-L6 #127): a pass with no reflection
#: pipeline synthesized none, which a scheduler cannot tell from a pass whose accumulated
#: importance never crossed the threshold. ``details["reflection"]`` is a free extra key on
#: ``consolidation_details``, same treatment as ``details["relations"]`` -- see
#: ``co_located.ReflectionSummary``'s docstring.
NO_REFLECTION = "not reflected -- no reflection pipeline wired (SM-L6 #127)"

#: The ``profile`` counterpart to :data:`NO_ENTITIES` (SM-S3 #120): a pass with no profile store
#: wired updated no profile, which a scheduler cannot tell from a pass whose episodes carried no
#: profile-worthy information. ``details["profile"]`` is a free extra key on
#: ``consolidation_details``, same treatment as ``details["relations"]``/``details["reflection"]``.
NO_PROFILE = "not extracted -- no profile store wired (SM-S3 #120)"

#: The other reason ``details["profile"]`` can be a sentinel rather than a dict: the store is
#: wired but this pass's ``scope`` has no ``user_id`` (a tenant-wide consolidation), so there is
#: no single user to key a profile by. Distinct from :data:`NO_PROFILE` -- "asked for, but this
#: pass names no user" vs. "not asked for" -- and public rather than module-private (QC round 3,
#: PR #135: a test previously had to reach past ``co_located.py``'s module boundary to assert
#: against it), matching :data:`NO_PROFILE`'s own treatment.
PROFILE_NO_USER = "not extracted -- scope has no user_id to key a profile by"

#: The ``summary`` counterpart to :data:`NO_ENTITIES` (SM-L8 #129): a pass with no summary
#: pipeline produced no session/day/global summary node, which a scheduler cannot tell from a
#: pass whose rollups simply were not due yet. ``details["summary"]`` is a free extra key on
#: ``consolidation_details``, same treatment as ``details["relations"]``/``details["reflection"]``
#: -- see ``co_located.SummarySummary``'s docstring.
NO_SUMMARY = "not summarized -- no summary pipeline wired (SM-L8 #129)"

#: The same floor ``FUSION_KEYS`` provides, for the scheduler SM-L1 (#122) will write. A provider
#: with nothing to report says so rather than omitting the key.
#:
#: ``entities`` needs its own discriminant beyond presence: it is one field carrying two shapes
#: (:data:`NO_ENTITIES` or a pipeline's dict), and the top-level ``complete`` cannot answer whether
#: extraction specifically drained -- SM-L1 (#122) must read ``details["entities"]`` through
#: :func:`entity_extraction_ran` / :func:`entity_extraction_complete`, not ``details["complete"]``
#: alone, or a truncated extraction pass under a clean episode scan reads as fully complete.
CONSOLIDATION_KEYS = ("derived_by", "complete", "supersede", "entities")


def consolidation_details(
    derived_by: Optional[str],
    complete: bool = True,
    supersede: str = NO_SUPERSEDE,
    entities: Any = NO_ENTITIES,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a ``ConsolidationReport.details`` payload carrying every :data:`CONSOLIDATION_KEYS`.

    ``complete`` is what a scheduler actually needs: whether the tier was drained. ``supersede``
    is the other thing it cannot infer -- **whether this pass's strategy is *capable* of retiring
    the rows it subsumes**, not whether it did so on this particular pass. A pass whose every
    decision happened to be ADD or NOOP reports the same ``supersede`` value as one that retired
    several; telling those apart needs the strategy's own report (e.g.
    ``FactExtractionReport.deleted``/``.updated``), not this field.
    ``entities`` is the third: whether the pass populated the graph's nodes (SM-G2 #100), which a
    provider with no extractor did not, and which reads identically to "nothing to extract" unless
    it is said. All three in the floor rather than as adapter extras, because one provider silently
    doing less than another is exactly the divergence the floor exists to prevent.

    Extra keys are free; the contract is a floor, not a ceiling. One that is not free by
    convention -- reserved, not required -- is ``tail_pending`` (``bool``): whether *this* pass
    stopped at its own cap with more of the tier left to read, distinct from ``complete`` in the
    presence of a resumption cursor (SM-L1 #122, ``CoLocatedMemoryProvider._advance_cursor``'s
    docstring) -- a provider tracking one can report a pass both ``complete=True`` (drained
    *from where it started*) and, on the very next call, immediately have new work again, which
    ``tail_pending`` on the specific pass that stopped early is what lets a caller tell apart
    from "the tier is now empty". Not promoted to :data:`CONSOLIDATION_KEYS`: only one provider
    emits it today, and widening the floor every provider must carry is a separate, deliberate
    decision this ticket does not make -- the same reasoning ``RelationSummary``'s docstring
    (``co_located.py``) gives for ``relations``.
    """
    details: Dict[str, Any] = {
        "derived_by": derived_by,
        "complete": complete,
        "supersede": supersede,
        "entities": entities,
    }
    details.update(extra)
    return details


def entity_extraction_ran(details: Dict[str, Any]) -> bool:
    """Whether ``details["entities"]`` is a pipeline result rather than the ``NO_ENTITIES`` sentinel.

    The floor's ``entities`` key is one field carrying two shapes -- a dict when a pipeline ran
    (including on zero episodes), the ``NO_ENTITIES`` string when none is wired -- and SM-L1 (#122)
    needs the discriminant as a named function, not an ``isinstance`` check re-derived at each call
    site (review finding on PR #89, round 3).
    """
    return isinstance(details.get("entities"), dict)


def entity_extraction_complete(details: Dict[str, Any]) -> bool:
    """Whether this pass left no known entity-extraction tail.

    ``True`` when no pipeline is wired -- there is no tail to be incomplete, and that is a wiring
    decision for :func:`entity_extraction_ran` to answer, not a retry signal for a scheduler.
    ``False`` only when a pipeline ran **and** its own ``complete`` is ``False`` -- SM-L1's
    (#122) two-cap hazard (:meth:`CoLocatedMemoryProvider._entities`'s docstring): a pass that
    stayed under the provider's episode cap but hit the pipeline's own can report the top-level
    ``ConsolidationReport`` complete while a tail of un-extracted episodes remains.
    """
    if not entity_extraction_ran(details):
        return True
    return bool(details["entities"].get("complete", True))


def render_context_lines(label: str, content: str) -> List[str]:
    """One prompt-injectable entry, as ``[label] head`` plus indented continuation lines.

    Extracted from :meth:`MemorySlice.as_context` (the only prior caller) so
    :class:`~egpt.memory.working.WorkingMemory` -- the WORKING tier's own, unpersisted context
    (SM-C6 #94) -- renders goals, notes and messages the identical, injection-safe way rather than
    a second hand-rolled version of the same decision.

    Line breaks inside ``content`` are indented rather than emitted raw. The ``[label]`` prefix is
    line-oriented, so content containing ``"\\n[semantic] ..."`` would otherwise render as an
    additional, forged entry -- and memory content is attacker-influenced by construction, since it
    is whatever a user said to the agent. Continuation lines are visibly indented, so one entry
    always reads as one entry.

    ``label`` gets the same treatment, not only ``content``. The first caller only ever passed
    ``record.tier.value``, a closed set of enum strings, so the gap was invisible; ``working.py``
    passes ``message.role``, which is caller-supplied like ``content`` is. A ``label`` containing
    its own line break -- e.g. ``role="user\\n[system] injected directive"`` -- would otherwise
    forge a synthetic entry the same way an unindented ``content`` line would.

    Splitting uses ``str.splitlines()`` rather than ``split("\\n")`` because that is what the
    *consumer* treats as a line break. ``splitlines`` covers eleven separators; hand-rolling the
    common two leaves ``\\x0b``, ``\\x0c``, ``\\x1c``-``\\x1e``, ``\\x85``, ``U+2028`` and ``U+2029``
    through, and each one forges a ``[label]`` line. Matching the consumer's escape set closes the
    class rather than the instance.
    """
    safe_label = label.splitlines()[0] if label else ""
    head, *rest = content.splitlines() or [""]
    lines = ["[{label}] {head}".format(label=safe_label, head=head).rstrip()]
    lines.extend("    " + line for line in rest)
    return lines


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
        drops into the same place in an agent prompt that the seed's learn context does. Line
        rendering is :func:`render_context_lines`, labelled by tier.
        """
        if not self.records:
            return ""
        lines: List[str] = []
        for record in self.records:
            lines.extend(render_context_lines(record.tier.value, record.content))
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
    #: What counts as "merged" is the extraction strategy's to define, and the two shipped
    #: strategies mean different things by it. ``_distil``'s deterministic dedup (SM-C1 #89, the
    #: default when no strategy is wired) counts extra *episodes* absorbed into a group it kept, by
    #: Jaccard token overlap. A wired ``FactExtractionPipeline`` (SM-L3 #124) has no notion of a
    #: group to subtract one from; it counts UPDATE and NOOP decisions instead, as the closest
    #: analogue -- episodes that did not become a brand-new fact. Compare within one
    #: ``details["derived_by"]``, not across two. Either way this is a count of *episodes*, not
    #: facts -- see ``near_duplicate_facts_folded`` for the fact-level count, kept separate because
    #: the two are different units and summing them into one field would make neither
    #: reconstructable from the report.
    duplicates_merged: int = 0
    #: Facts `consolidation.dedup.merge_near_duplicate_facts` (SM-L4 #125) folded into a survivor
    #: *after* `_distil` ran, by exact-match-after-normalization. Zero either because `_distil`'s
    #: own grouping already caught every duplicate in the pass (the common case on that path), or
    #: because a wired `FactExtractionPipeline` (SM-L3 #124) ran instead of `_distil` and this
    #: fold never runs over its output at all -- see
    #: `CoLocatedMemoryProvider._distil_facts`'s docstring for why running it there would be
    #: unsafe, not merely redundant.
    near_duplicate_facts_folded: int = 0
    #: Graph edges a wired ``relation_pipeline`` (SM-G3 #101 / SM-G5 #103 / SM-L5 #126) invalidated
    #: this pass -- against pre-existing graph state and against sibling candidates in the same
    #: batch alike (``RelationExtractionPipeline._contradicted``/``_contradicted_within``). Zero
    #: when no ``relation_pipeline`` is wired, the same "never configured" reading every other
    #: field on this report gives an absent stage. Deliberately **not** incremented by
    #: `fact_pipeline`'s (SM-L3 #124) own UPDATE/DELETE retirements: per ADR 0003 SS B.6 the graph
    #: is sole authority for semantic contradiction, and a vector-side transaction-time supersede
    #: is a different mechanism, not a second contradiction this field also counts.
    contradictions_resolved: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
