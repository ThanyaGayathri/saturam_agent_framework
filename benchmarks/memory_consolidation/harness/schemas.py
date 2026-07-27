"""Core data model for the consolidation harness.

The important design decision surfaced here is the **provenance model** for the
episodic->semantic step (acceptance criterion #2 of SM-R7). A ``SemanticFact``
carries an explicit, verifiable link back to the ``Episode`` objects it was
distilled from, plus bi-temporal validity so conflicts ("keep newer") can be
resolved without destroying history.

These dataclasses mirror the shape a promoted core schema would take, but they
live in the harness only. The provisional core mapping is documented in
``RESEARCH.md`` (schema is core code -> not committed here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MergeOp(str, Enum):
    """The four merge decisions of a mem0-style consolidation step."""

    ADD = "ADD"  # net-new fact
    UPDATE = "UPDATE"  # supersede/augment an existing fact
    DELETE = "DELETE"  # retract a fact (no longer true / user asked to forget)
    NOOP = "NOOP"  # already captured; do nothing


@dataclass
class Episode:
    """A single raw episodic memory: 'something that happened'.

    Episodes are the input tier. Consolidation distils them into
    ``SemanticFact`` objects ('things learned').
    """

    episode_id: str
    text: str
    timestamp: int  # epoch seconds — drives 'keep newer' conflict resolution
    user_id: str = "default"
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "text": self.text,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Episode":
        return cls(
            episode_id=data["episode_id"],
            text=data["text"],
            timestamp=int(data["timestamp"]),
            user_id=data.get("user_id", "default"),
            session_id=data.get("session_id"),
            metadata=data.get("metadata", {}) or {},
        )


@dataclass
class SemanticFact:
    """A distilled, durable semantic memory with provenance + bi-temporal validity.

    Provenance model (AC #2):
      - ``source_episode_ids`` is the concrete link semantic-fact -> source
        episodes. It is populated at creation and extended on UPDATE, so a fact
        always cites every episode that supports it. This is what makes the
        step auditable and lets us *measure* provenance validity.

    Bi-temporal conflict handling (AC #3 anti-lossiness):
      - ``valid_from`` / ``valid_to`` mark when the fact was true in the world.
        A superseded fact is not deleted; it gets ``valid_to`` set and
        ``superseded_by`` pointed at its replacement. History is preserved.
    """

    fact_id: str
    statement: str
    source_episode_ids: List[str] = field(default_factory=list)
    topics: Optional[List[str]] = None
    user_id: str = "default"

    created_at: int = 0  # when we learned it
    valid_from: Optional[int] = None  # when it became true in the world
    valid_to: Optional[int] = None  # when it stopped being true (None = still valid)
    superseded_by: Optional[str] = None  # fact_id that replaced this one

    importance: float = 0.5  # Generative-Agents style [0, 1]
    confidence: float = 1.0
    derived_by: Optional[str] = None  # strategy name that produced it

    @property
    def is_active(self) -> bool:
        """A fact is active if it has not been retracted or superseded."""
        return self.valid_to is None and self.superseded_by is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "statement": self.statement,
            "source_episode_ids": self.source_episode_ids,
            "topics": self.topics,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "superseded_by": self.superseded_by,
            "importance": self.importance,
            "confidence": self.confidence,
            "derived_by": self.derived_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticFact":
        return cls(**data)


@dataclass
class TargetFact:
    """Ground-truth semantic fact the consolidator SHOULD end up holding.

    Used only by the synthetic dataset, where we plant facts, duplicates and
    conflicts on purpose so dedup / conflict-resolution / provenance can be
    scored against a known answer.
    """

    target_id: str
    statement: str
    paraphrases: List[str] = field(default_factory=list)  # accepted surface forms
    should_be_active: bool = True  # False = must be retracted/superseded by end
    supersedes: Optional[str] = None  # target_id this one overrides (a conflict)
    source_episode_ids: List[str] = field(default_factory=list)  # gold provenance
    topics: Optional[List[str]] = None

    def surface_forms(self) -> List[str]:
        return [self.statement, *self.paraphrases]


@dataclass
class UsageStats:
    """LLM cost/latency accounting, accumulated over a consolidation run."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_s: float = 0.0
    usd_cost: float = 0.0

    def add(
        self,
        input_tokens: int,
        output_tokens: int,
        latency_s: float,
        usd_cost: float,
    ) -> None:
        self.llm_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        self.latency_s += latency_s
        self.usd_cost += usd_cost

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "latency_s": round(self.latency_s, 3),
            "usd_cost": round(self.usd_cost, 6),
        }


@dataclass
class ConsolidationResult:
    """Output of running one strategy over one episode stream."""

    strategy: str
    facts: List[SemanticFact]
    usage: UsageStats
    op_trace: List[Dict[str, Any]] = field(default_factory=list)  # merge decisions

    def active_facts(self) -> List[SemanticFact]:
        return [f for f in self.facts if f.is_active]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "facts": [f.to_dict() for f in self.facts],
            "usage": self.usage.to_dict(),
            "op_trace": self.op_trace,
        }
