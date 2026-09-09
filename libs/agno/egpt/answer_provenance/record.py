"""The provenance record itself (R7, G4): what every answer writes.

Deliberately generic and JSON-shaped, not the retail product's generated pydantic model
(``egpt_retail.api.models._generated.Provenance``). The framework must not import product code
(the framework/product boundary in ``CLAUDE.md``), and re-deriving five more typed dataclasses
here (``DatasetRead``, ``SqlExecution``, ...) just to shape-match product's generated ones a
third time would buy nothing a plain ``Mapping`` doesn't -- the nested lists are opaque payload
as far as this module is concerned. ``product/tests`` pins that the two shapes agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

__all__ = ["MODEL_TIERS", "ProvenanceRecord"]

#: Mirrors the retail API contract's ``ProvenanceModelTier`` enum
#: (``product/egpt_retail/api/openapi/schemas/common.yaml``) and this record's own DB-level
#: ``CHECK`` constraint. ``"none"`` is the value for a run with no model step at all -- the
#: exact shape of R7's own "workflow completes without a model" evidence.
MODEL_TIERS = ("none", "small", "frontier")


@dataclass(frozen=True)
class ProvenanceRecord:
    """One provenance record per answer. Mirrors ``common.yaml#Provenance`` field-for-field.

    ``datasets_read``/``sql_executed`` default to empty rather than being required at
    construction: the API contract lists them as required *keys*, which an empty list still
    satisfies -- a run that read nothing and executed no SQL (R7's own throwaway
    executor-only verification workflow) is a real, valid record, not a malformed one.
    """

    run_id: str
    produced_at: datetime
    semantic_snapshot: str
    model_tier: str
    datasets_read: Tuple[Mapping[str, Any], ...] = ()
    sql_executed: Tuple[Mapping[str, Any], ...] = ()
    use_case_id: Optional[str] = None
    vocabulary_version: Optional[str] = None
    skill: Optional[Mapping[str, Any]] = None
    rows_sampled: Optional[Tuple[Mapping[str, Any], ...]] = None
    freshness: Optional[Tuple[Mapping[str, Any], ...]] = None
    retrieval_hits: Optional[Tuple[Mapping[str, Any], ...]] = field(default=None)

    def __post_init__(self) -> None:
        if self.model_tier not in MODEL_TIERS:
            raise ValueError(f"model_tier must be one of {MODEL_TIERS}, got {self.model_tier!r}")
        if not self.run_id:
            raise ValueError("run_id must not be empty -- every answer writes exactly one record")

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe payload, the shape ``common.yaml#Provenance`` describes."""
        return {
            "run_id": self.run_id,
            "use_case_id": self.use_case_id,
            "produced_at": self.produced_at.isoformat(),
            "semantic_snapshot": self.semantic_snapshot,
            "vocabulary_version": self.vocabulary_version,
            "skill": dict(self.skill) if self.skill is not None else None,
            "model_tier": self.model_tier,
            "datasets_read": [dict(d) for d in self.datasets_read],
            "sql_executed": [dict(s) for s in self.sql_executed],
            "rows_sampled": [dict(r) for r in self.rows_sampled] if self.rows_sampled is not None else None,
            "freshness": [dict(f) for f in self.freshness] if self.freshness is not None else None,
            "retrieval_hits": [dict(h) for h in self.retrieval_hits] if self.retrieval_hits is not None else None,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ProvenanceRecord":
        """Reconstruct from a DB row (``store.read_provenance``'s shape: JSONB columns already
        decoded to Python lists/dicts by the driver)."""

        def _tuple(value: Optional[Sequence[Any]]) -> Optional[Tuple[Mapping[str, Any], ...]]:
            return tuple(value) if value is not None else None

        produced_at = row["produced_at"]
        if isinstance(produced_at, str):
            produced_at = datetime.fromisoformat(produced_at)

        return cls(
            run_id=row["run_id"],
            use_case_id=row.get("use_case_id"),
            produced_at=produced_at,
            semantic_snapshot=row["semantic_snapshot"],
            vocabulary_version=row.get("vocabulary_version"),
            skill=row.get("skill"),
            model_tier=row["model_tier"],
            datasets_read=_tuple(row.get("datasets_read")) or (),
            sql_executed=_tuple(row.get("sql_executed")) or (),
            rows_sampled=_tuple(row.get("rows_sampled")),
            freshness=_tuple(row.get("freshness")),
            retrieval_hits=_tuple(row.get("retrieval_hits")),
        )
