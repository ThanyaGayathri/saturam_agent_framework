"""Persisted memory row models (SM-C3 #91, promoting SM-R12's shapes).

One shared spine with thin per-tier subclasses, per ``benchmarks/memory_datamodel/SPEC.md``
sections 4.2 and 4.4. Timestamps are epoch seconds, matching the ``BIGINT`` columns.

These are **rows addressed by primary key**, not the query envelope: ``egpt.memory.types``
carries ``MemoryRecord`` / ``MemorySlice`` at the retrieval altitude, and ADR 0003 section B.5
rules that both stand.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, TypeVar
from uuid import uuid4

from egpt.utils.dttm import now_epoch_s, to_epoch_s

RowT = TypeVar("RowT", bound="MemoryRow")

#: Tier names usable on a :class:`MemoryLink` endpoint (ADR 0003 section B.5). Not imported from
#: ``egpt.memory.tiers`` because this is the db layer; a test asserts the two stay equal.
MEMORY_TIER_VALUES = frozenset({"working", "episodic", "semantic", "procedural"})

#: Closed set from SM-R12 section 4.3.
PROCEDURAL_KINDS = frozenset({"rule", "skill", "prompt_fragment"})


def _is_epoch_field(name: str) -> bool:
    return name.endswith("_at") or name in ("t_valid", "t_invalid")


def _require_value(name: str, value: Optional[str]) -> str:
    """Guard a NOT NULL identifier or reference column.

    Free-text columns (``content``, ``title``) are deliberately not guarded: empty text
    satisfies NOT NULL, so refusing it would be a constraint the DDL does not carry.
    """
    if not value:
        raise ValueError(f"{name} is required and cannot be empty")
    return value


def _require_unit_interval(name: str, value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be within [0.0, 1.0], got {value}")
    return value


@dataclass
class MemoryRow:
    """Shared normalisation and serialisation for every persisted memory tier.

    Each rule lives here once and is asserted once, parametrised over every tier that carries
    it -- a guard applied per subclass drifts between neighbours.
    """

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if _is_epoch_field(f.name) and value is not None:
                setattr(self, f.name, to_epoch_s(value))
            elif isinstance(value, dict):
                setattr(self, f.name, dict(value))
            elif isinstance(value, list):
                setattr(self, f.name, list(value))

        if isinstance(self, TenantScoped):
            self._validate_identity()

        if isinstance(self, BiTemporal):
            self._validate_temporal()

        if isinstance(self, Embeddable):
            self._validate_embedding()

        if isinstance(self, Decaying):
            _require_unit_interval("importance", self.importance)

        self._validate_row()

    def _validate_row(self) -> None:
        """Per-tier guards. Overridden where a tier has its own closed vocabulary."""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize every field, preserving None so a DB update can clear a column."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls: Type[RowT], data: Dict[str, Any]) -> RowT:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class TenantScoped(MemoryRow):
    """Identity, ownership and audit. ``tenant_id`` is SM-R3's partition key and is mandatory."""

    id: str = field(default_factory=lambda: uuid4().hex)
    tenant_id: str = ""
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    team_id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def _validate_identity(self) -> None:
        self.id = _require_value("id", self.id)
        self.tenant_id = _require_value("tenant_id", self.tenant_id)
        if self.created_at is None:
            self.created_at = now_epoch_s()


@dataclass
class BiTemporal(MemoryRow):
    """SM-R10's four timestamps. ``created_at``/``expired_at`` are transaction time (when the
    system held this version); ``t_valid``/``t_invalid`` are valid time (when it was true in the
    world). The axes are independent -- collapsing them is what ADR 0003 section B.6 guards
    against. Rows are insert-only: a correction is :meth:`expire` plus an insert (SCD-2).
    """

    t_valid: Optional[int] = None
    t_invalid: Optional[int] = None
    expired_at: Optional[int] = None
    superseded_by: Optional[str] = None

    def _validate_temporal(self) -> None:
        """The two CHECK constraints from ``ddl/schema.sql``, enforced at the model boundary."""
        if self.t_valid is not None and self.t_invalid is not None and self.t_invalid < self.t_valid:
            raise ValueError(f"t_invalid ({self.t_invalid}) precedes t_valid ({self.t_valid})")
        created_at = getattr(self, "created_at", None)
        if self.expired_at is not None and created_at is not None and self.expired_at < created_at:
            raise ValueError(f"expired_at ({self.expired_at}) precedes created_at ({created_at})")

    def is_valid_at(self, at: int) -> bool:
        """SM-R10 point-in-time: ``t_valid <= at < t_invalid``, NULL bounds open-ended."""
        if self.t_valid is not None and at < self.t_valid:
            return False
        if self.t_invalid is not None and at >= self.t_invalid:
            return False
        return True

    def is_current(self) -> bool:
        """Whether this row is the version the system currently holds."""
        return self.expired_at is None and not self.superseded_by

    def expire(self, expired_at: Optional[int] = None, t_invalid: Optional[int] = None) -> bool:
        """Close this row's transaction time, and optionally bound its valid time.

        ``t_valid`` is never touched: when the fact *became* true does not change because the
        system stopped holding this version. Returns False when the row is already expired, so a
        replayed correction cannot overwrite the original bound.
        """
        if self.expired_at is not None:
            return False
        self.expired_at = to_epoch_s(expired_at) if expired_at is not None else now_epoch_s()
        if t_invalid is not None:
            self.t_invalid = to_epoch_s(t_invalid)
        self._validate_temporal()
        return True


@dataclass
class Embeddable(MemoryRow):
    """SM-R4's embedding triple. ``embedding_dim`` is a per-deployment constant (768 Mode A /
    1024 Mode B), validated here against the vector's length; validating it against the *column
    width* needs the typed vector column SM-V1 (#95) brings.
    """

    embedding: Optional[List[float]] = None
    embedding_dim: Optional[int] = None
    embedding_version: Optional[str] = None

    def _validate_embedding(self) -> None:
        if self.embedding is None:
            return
        if self.embedding_dim is None:
            self.embedding_dim = len(self.embedding)
        elif self.embedding_dim != len(self.embedding):
            raise ValueError(
                f"embedding_dim ({self.embedding_dim}) does not match the embedding length ({len(self.embedding)})"
            )


@dataclass
class Decaying(MemoryRow):
    """SM-R5's decay inputs. ``strength`` is deliberately absent -- SM-R5 section 5 defers it
    ("do NOT add now"), and ADR 0003 section 6 ratifies that. Decay is computed at read time.
    """

    importance: float = 0.5
    last_accessed_at: Optional[int] = None


@dataclass
class EpisodicRecord(TenantScoped, BiTemporal, Embeddable, Decaying):
    """A raw event -- non-lossy, written on the request path, distilled later.

    ``superseded_by`` widens to a list, overriding :class:`BiTemporal`: an SM-R6 re-cut can
    merge or split an episode, so one row can be replaced by several.
    """

    content: str = ""
    occurred_at: Optional[int] = None  # WHEN it happened
    location: Optional[str] = None  # WHERE
    cause: Optional[str] = None  # WHY
    entity_ids: List[str] = field(default_factory=list)
    graph_node_id: Optional[str] = None
    revision: int = 0
    supersedes: List[str] = field(default_factory=list)
    # Widened from BiTemporal's single id -- see the class docstring. The ignore is the
    # variance mypy flags on the override, not a defect.
    superseded_by: List[str] = field(default_factory=list)  # type: ignore[assignment]
    boundary_method: Optional[str] = None
    boundary_trigger: Optional[float] = None
    boundary_confidence: Optional[float] = None
    refined: bool = False
    segmenter_version: Optional[str] = None

    def _validate_row(self) -> None:
        if self.boundary_confidence is not None:
            _require_unit_interval("boundary_confidence", self.boundary_confidence)


@dataclass
class SemanticFact(TenantScoped, BiTemporal, Embeddable, Decaying):
    """A distilled durable fact. SPO is optional structure over the free-text statement in
    ``content`` -- a fact that resists triple extraction is still storable.
    """

    content: str = ""
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    confidence: float = 1.0
    source_episode_ids: List[str] = field(default_factory=list)
    derived_by: Optional[str] = None
    entity_ids: List[str] = field(default_factory=list)

    def _validate_row(self) -> None:
        _require_unit_interval("confidence", self.confidence)

    def is_triple(self) -> bool:
        """Whether this fact carries the full SPO structure a graph edge requires."""
        return bool(self.subject and self.predicate and self.object)


@dataclass
class SemanticEntity(TenantScoped, Embeddable):
    """A graph node. Embeddable, and deliberately not bi-temporal: identity is stable, so there
    is no valid-time window to bound (ADR 0003 section 1).
    """

    entity_type: str = ""
    name: Optional[str] = None
    description: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)

    def _validate_row(self) -> None:
        self.entity_type = _require_value("entity_type", self.entity_type)


@dataclass
class SemanticRelation(TenantScoped, BiTemporal):
    """A graph edge -- the primary SM-R10 bi-temporal surface. Not embedded."""

    subject_entity_id: str = ""
    predicate: str = ""
    object_entity_id: str = ""
    confidence: float = 1.0
    source_episode_ids: List[str] = field(default_factory=list)
    derived_by: Optional[str] = None

    def _validate_row(self) -> None:
        self.subject_entity_id = _require_value("subject_entity_id", self.subject_entity_id)
        self.predicate = _require_value("predicate", self.predicate)
        self.object_entity_id = _require_value("object_entity_id", self.object_entity_id)
        _require_unit_interval("confidence", self.confidence)


@dataclass
class ProceduralRecord(TenantScoped, Embeddable):
    """A learned rule, skill or prompt fragment. Retrieved by similarity, so embeddable."""

    kind: str = "rule"
    title: Optional[str] = None
    content: str = ""
    trigger: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    success_count: Optional[int] = None

    def _validate_row(self) -> None:
        if self.kind not in PROCEDURAL_KINDS:
            raise ValueError(f"kind must be one of {sorted(PROCEDURAL_KINDS)}, got {self.kind!r}")


@dataclass
class MemoryLink(TenantScoped, BiTemporal):
    """A typed cross-tier edge, first-classing the seed's inline ``EntityMemory.relationships``.

    Endpoints carry no FK (SM-R12 section 9), so readers must tolerate a missing target.
    """

    from_id: str = ""
    from_tier: str = ""
    to_id: str = ""
    to_tier: str = ""
    link_type: str = "relates_to"

    def _validate_row(self) -> None:
        self.from_id = _require_value("from_id", self.from_id)
        self.to_id = _require_value("to_id", self.to_id)
        self.link_type = _require_value("link_type", self.link_type)
        for name in ("from_tier", "to_tier"):
            value = _require_value(name, getattr(self, name))
            if value not in MEMORY_TIER_VALUES:
                raise ValueError(f"{name} must be one of {sorted(MEMORY_TIER_VALUES)}, got {value!r}")
            setattr(self, name, value)


@dataclass
class UserMemory:
    """Model for User Memories"""

    memory: str
    memory_id: Optional[str] = None
    topics: Optional[List[str]] = None
    user_id: Optional[str] = None
    input: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    feedback: Optional[str] = None

    agent_id: Optional[str] = None
    team_id: Optional[str] = None
    # Nullable, and not backfilled: rows written before multi-tenancy mean "unscoped". Nothing
    # reads it yet -- the manager still partitions on user_id alone, which is why
    # SeedMemoryProvider keeps refusing multi-tenant deployments (aicoe-tracker#175).
    tenant_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Automatically set/normalize created_at and updated_at."""
        self.created_at = now_epoch_s() if self.created_at is None else to_epoch_s(self.created_at)
        if self.updated_at is not None:
            self.updated_at = to_epoch_s(self.updated_at)

    def to_dict(self) -> Dict[str, Any]:
        created_at = datetime.fromtimestamp(self.created_at).isoformat() if self.created_at is not None else None
        updated_at = datetime.fromtimestamp(self.updated_at).isoformat() if self.updated_at is not None else created_at
        _dict = {
            "memory_id": self.memory_id,
            "memory": self.memory,
            "topics": self.topics,
            "created_at": created_at,
            "updated_at": updated_at,
            "input": self.input,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "team_id": self.team_id,
            "feedback": self.feedback,
            "tenant_id": self.tenant_id,
        }
        return {k: v for k, v in _dict.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserMemory":
        data = dict(data)

        # Preserve 0 and None explicitly; only process if key exists
        if "created_at" in data and data["created_at"] is not None:
            data["created_at"] = to_epoch_s(data["created_at"])
        if "updated_at" in data and data["updated_at"] is not None:
            data["updated_at"] = to_epoch_s(data["updated_at"])

        return cls(**data)
