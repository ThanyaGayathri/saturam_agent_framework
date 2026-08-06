"""Bi-temporal edge model (SM-R10).

Python reference model for the four-timestamp edge. Mirrors the seed's epoch-seconds
convention (see ``UserMemory`` at ``libs/agno/egpt/db/schemas/memory.py`` and
``MEMORY_TABLE_SCHEMA`` in ``libs/agno/egpt/db/postgres/schemas.py``) and REUSES
``egpt.utils.dttm`` for normalization. Postgres uses ``timestamptz`` and Neo4j uses native
``datetime``; the Python model is epoch-seconds ``int`` (all three are order-equivalent).

Two independent time axes (Graphiti model):

* valid time     -- ``t_valid`` / ``t_invalid``: when the fact was true *in the world*.
* transaction time -- ``created_at`` / ``expired_at``: when the *system* held the row as belief.

Open intervals are represented by ``None`` (``t_invalid``/``expired_at``), treated as
``+infinity`` by the query predicates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from egpt.utils.dttm import now_epoch_s, to_epoch_s

# Sentinel used by query predicates for an open (NULL) upper bound.
POS_INF = float("inf")


def upper(bound: Optional[int]) -> float:
    """Coalesce an open interval bound to +infinity (mirrors ``COALESCE(col, 'infinity')``)."""
    return POS_INF if bound is None else float(bound)


class EdgeOutcome(str, Enum):
    """Result of an ``upsert_edge`` call."""

    INSERT = "insert"  # first assertion of this (subject, predicate) fact
    SUPERSEDE = "supersede"  # real-world change: a prior fact's t_invalid was set
    RETRACT = "retract"  # transaction-time correction: a prior row was expired
    NOOP = "noop"  # identical fact already believed for this validity window


@dataclass
class BiTemporalEdge:
    """A single bi-temporal graph edge (subject -[predicate]-> object).

    ``t_valid`` / ``created_at`` default to now when omitted; all four timestamps are
    normalized to epoch seconds on construction.
    """

    subject_id: str
    predicate: str
    object_id: str
    user_id: str
    t_valid: Optional[int] = None
    t_invalid: Optional[int] = None
    created_at: Optional[int] = None
    expired_at: Optional[int] = None
    fact: Optional[str] = None
    embedding: Optional[List[float]] = None
    edge_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        self.t_valid = now_epoch_s() if self.t_valid is None else to_epoch_s(self.t_valid)
        if self.t_invalid is not None:
            self.t_invalid = to_epoch_s(self.t_invalid)
        self.created_at = now_epoch_s() if self.created_at is None else to_epoch_s(self.created_at)
        if self.expired_at is not None:
            self.expired_at = to_epoch_s(self.expired_at)

    @property
    def is_current_belief(self) -> bool:
        """True when the row is still the system's current belief (transaction axis open)."""
        return self.expired_at is None

    def valid_at(self, t: int) -> bool:
        """True when this edge's world-validity interval contains ``t`` (``t_valid <= t < t_invalid``)."""
        assert self.t_valid is not None  # normalized in __post_init__
        return self.t_valid <= t < upper(self.t_invalid)

    def believed_at(self, tt: int) -> bool:
        """True when the system held this row at transaction-time ``tt`` (``created_at <= tt < expired_at``)."""
        assert self.created_at is not None  # normalized in __post_init__
        return self.created_at <= tt < upper(self.expired_at)

    def bounded_version(self, t_invalid: int, created_at: int) -> "BiTemporalEdge":
        """A new transaction-time version of this fact with its valid-time end set.

        Used by the invalidate-not-delete rule: the old version is expired (transaction axis)
        and this bounded version is inserted, so past *beliefs about validity* stay
        reconstructable (5b). Same identity + valid-from; new ``edge_id``.
        """
        return BiTemporalEdge(
            subject_id=self.subject_id,
            predicate=self.predicate,
            object_id=self.object_id,
            user_id=self.user_id,
            fact=self.fact,
            t_valid=self.t_valid,
            t_invalid=t_invalid,
            created_at=created_at,
            expired_at=None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "subject_id": self.subject_id,
            "predicate": self.predicate,
            "object_id": self.object_id,
            "user_id": self.user_id,
            "t_valid": self.t_valid,
            "t_invalid": self.t_invalid,
            "created_at": self.created_at,
            "expired_at": self.expired_at,
            "fact": self.fact,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BiTemporalEdge":
        return cls(
            subject_id=data["subject_id"],
            predicate=data["predicate"],
            object_id=data["object_id"],
            user_id=data["user_id"],
            t_valid=data.get("t_valid"),
            t_invalid=data.get("t_invalid"),
            created_at=data.get("created_at"),
            expired_at=data.get("expired_at"),
            fact=data.get("fact"),
            edge_id=data.get("edge_id", uuid.uuid4().hex),
        )
