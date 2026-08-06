"""Apply a merge decision to a fact store, preserving provenance + history.

Shared by the mem0 and hybrid strategies so ADD/UPDATE/DELETE/NOOP behave
identically wherever they are used. This is where the anti-lossiness guarantees
live: UPDATE and DELETE never destroy the prior fact, they supersede/retire it
with bi-temporal stamps, and provenance is always unioned forward.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from harness.schemas import SemanticFact


def _new_id() -> str:
    return f"f_{uuid.uuid4().hex[:12]}"


def apply_merge(
    facts: List[SemanticFact],
    decision: Dict[str, Any],
    source_episode_ids: List[str],
    timestamp: int,
    strategy_name: str,
    importance: float = 0.5,
    topics: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Mutate ``facts`` in place according to ``decision``. Return an op record."""
    op = decision["op"]
    target_id = decision.get("target_id")
    statement = decision["statement"]

    by_id = {f.fact_id: f for f in facts}

    if op == "NOOP":
        target = by_id.get(target_id) if target_id else None
        if target is not None:
            # Reinforcement: record that another episode supports this fact.
            for sid in source_episode_ids:
                if sid not in target.source_episode_ids:
                    target.source_episode_ids.append(sid)
        return {"op": "NOOP", "target_id": target_id, "statement": statement}

    if op == "DELETE":
        target = by_id.get(target_id) if target_id else None
        if target is not None and target.is_active:
            target.valid_to = timestamp  # retire, keep history
        return {"op": "DELETE", "target_id": target_id, "statement": target.statement if target else statement}

    if op == "UPDATE" and target_id in by_id:
        old = by_id[target_id]
        new_id = _new_id()
        merged_sources = list(dict.fromkeys([*old.source_episode_ids, *source_episode_ids]))
        new_fact = SemanticFact(
            fact_id=new_id,
            statement=statement,
            source_episode_ids=merged_sources,
            topics=topics or old.topics,
            user_id=old.user_id,
            created_at=timestamp,
            valid_from=timestamp,
            importance=max(importance, old.importance),
            derived_by=strategy_name,
        )
        # Supersede the old fact (bi-temporal), do not delete it.
        old.valid_to = timestamp
        old.superseded_by = new_id
        facts.append(new_fact)
        return {"op": "UPDATE", "target_id": target_id, "new_id": new_id, "statement": statement}

    # Fall through: ADD (also when UPDATE target was missing)
    new_id = _new_id()
    facts.append(
        SemanticFact(
            fact_id=new_id,
            statement=statement,
            source_episode_ids=list(source_episode_ids),
            topics=topics,
            user_id=facts[0].user_id if facts else "default",
            created_at=timestamp,
            valid_from=timestamp,
            importance=importance,
            derived_by=strategy_name,
        )
    )
    return {"op": "ADD", "new_id": new_id, "statement": statement}
