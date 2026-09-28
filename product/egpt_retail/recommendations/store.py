"""Reading and writing recommendations (K26).

A thin store over ``retail_recommendations``; sync and async variants, rows back as plain
dicts in the contract's field names. ``upsert_open`` is what the ``recommend`` executor
calls: one open record per (use case, subject), refreshed by the latest run rather than
duplicated -- a person who accepted or dismissed it keeps that decision until the horizon
ends, so a rerun never reopens a decided record.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.recommendations.ddl import RECOMMENDATIONS_TABLE

__all__ = ["RecommendationStore", "new_recommendation_id", "STATUSES"]

STATUSES = ("open", "accepted", "dismissed", "actioned", "expired")

_COLUMNS = (
    "id, use_case_id, run_id, kind, subject_type, subject_id, subject_label, account_id, "
    "account_label, owner, risk_score, risk_level, horizon_days, model_version, drivers, "
    "action, value_at_stake, value_unit, context, status, status_note, status_by, status_at, "
    "outcome, outcome_at, expires_at, created_at, updated_at"
)


def new_recommendation_id() -> str:
    return "rec_" + uuid.uuid4().hex[:8]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, default=str)


def _row(mapping: Any) -> Dict[str, Any]:
    row = dict(mapping)
    for key in ("drivers", "action", "context"):
        value = row.get(key)
        if isinstance(value, str):
            row[key] = json.loads(value)
    return row


class RecommendationStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # -- writes -----------------------------------------------------------------------

    def upsert_open(
        self,
        use_case_id: str,
        run_id: Optional[str],
        records: Sequence[Mapping[str, Any]],
    ) -> Dict[str, int]:
        """Write the run's records: a subject with an open record gets it refreshed (risk,
        drivers, action, value, context, run); one with a decided record is left alone;
        a new subject gets a new record. Returns how many were created, refreshed, kept."""
        created = refreshed = kept = 0
        with self._engine.begin() as connection:
            for record in records:
                existing = connection.execute(
                    text(
                        f"SELECT id, status FROM {RECOMMENDATIONS_TABLE} "
                        "WHERE use_case_id = :use_case_id AND subject_type = :subject_type "
                        "AND subject_id = :subject_id AND kind = :kind "
                        "AND (expires_at IS NULL OR expires_at > now()) "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {
                        "use_case_id": use_case_id,
                        "subject_type": record.get("subject_type", "quote"),
                        "subject_id": str(record["subject_id"]),
                        "kind": record.get("kind", "recommendation"),
                    },
                ).first()
                params = self._params(use_case_id, run_id, record)
                if existing is None:
                    params["id"] = record.get("id") or new_recommendation_id()
                    connection.execute(
                        text(
                            f"INSERT INTO {RECOMMENDATIONS_TABLE} (id, use_case_id, run_id, kind, subject_type, subject_id, subject_label, account_id, account_label, owner, risk_score, risk_level, horizon_days, model_version, drivers, action, value_at_stake, value_unit, context, expires_at) "
                            "VALUES (:id, :use_case_id, :run_id, :kind, :subject_type, :subject_id, :subject_label, :account_id, :account_label, :owner, :risk_score, :risk_level, :horizon_days, :model_version, CAST(:drivers AS jsonb), CAST(:action AS jsonb), :value_at_stake, :value_unit, CAST(:context AS jsonb), :expires_at)"
                        ),
                        params,
                    )
                    created += 1
                elif existing.status == "open":
                    params["id"] = existing.id
                    connection.execute(
                        text(
                            f"UPDATE {RECOMMENDATIONS_TABLE} SET run_id = :run_id, subject_label = :subject_label, account_id = :account_id, account_label = :account_label, owner = :owner, risk_score = :risk_score, risk_level = :risk_level, horizon_days = :horizon_days, model_version = :model_version, drivers = CAST(:drivers AS jsonb), action = CAST(:action AS jsonb), value_at_stake = :value_at_stake, value_unit = :value_unit, context = CAST(:context AS jsonb), expires_at = :expires_at, updated_at = now() "
                            "WHERE id = :id"
                        ),
                        params,
                    )
                    refreshed += 1
                else:
                    kept += 1
        return {"created": created, "refreshed": refreshed, "kept": kept}

    async def aupsert_open(self, *args: Any, **kwargs: Any) -> Dict[str, int]:
        return await asyncio.to_thread(lambda: self.upsert_open(*args, **kwargs))

    @staticmethod
    def _params(
        use_case_id: str, run_id: Optional[str], record: Mapping[str, Any]
    ) -> Dict[str, Any]:
        return {
            "use_case_id": use_case_id,
            "run_id": run_id,
            "kind": record.get("kind", "recommendation"),
            "subject_type": record.get("subject_type", "quote"),
            "subject_id": str(record["subject_id"]),
            "subject_label": record.get("subject_label", ""),
            "account_id": None
            if record.get("account_id") is None
            else str(record["account_id"]),
            "account_label": record.get("account_label", ""),
            "owner": record.get("owner", ""),
            "risk_score": float(record.get("risk_score") or 0),
            "risk_level": record.get("risk_level", "low"),
            "horizon_days": record.get("horizon_days"),
            "model_version": record.get("model_version"),
            "drivers": _json(list(record.get("drivers") or [])),
            "action": _json(record.get("action")),
            "value_at_stake": float(record.get("value_at_stake") or 0),
            "value_unit": record.get("value_unit", "USD"),
            "context": _json(dict(record.get("context") or {})),
            "expires_at": record.get("expires_at"),
        }

    def set_status(
        self,
        recommendation_id: str,
        status: str,
        *,
        note: str = "",
        by: str = "",
    ) -> Optional[Dict[str, Any]]:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; one of {', '.join(STATUSES)}")
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE {RECOMMENDATIONS_TABLE} SET status = :status, status_note = :note, "
                    "status_by = :by, status_at = now(), updated_at = now() WHERE id = :id"
                ),
                {"id": recommendation_id, "status": status, "note": note, "by": by},
            )
        return self.get(recommendation_id)

    async def aset_status(self, *args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.set_status(*args, **kwargs))

    def set_outcome(
        self, recommendation_id: str, outcome: str, *, at: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE {RECOMMENDATIONS_TABLE} SET outcome = :outcome, outcome_at = :at, "
                    "status = CASE WHEN status = 'open' THEN 'expired' ELSE status END, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": recommendation_id, "outcome": outcome, "at": at or _now()},
            )
        return self.get(recommendation_id)

    async def aset_outcome(self, *args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.set_outcome(*args, **kwargs))

    def delete_for_use_case(self, use_case_id: str) -> int:
        with self._engine.begin() as connection:
            result = connection.execute(
                text(f"DELETE FROM {RECOMMENDATIONS_TABLE} WHERE use_case_id = :id"),
                {"id": use_case_id},
            )
        return int(result.rowcount or 0)

    async def adelete_for_use_case(self, use_case_id: str) -> int:
        return await asyncio.to_thread(self.delete_for_use_case, use_case_id)

    # -- reads ------------------------------------------------------------------------

    def get(self, recommendation_id: str) -> Optional[Dict[str, Any]]:
        with self._engine.connect() as connection:
            found = (
                connection.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM {RECOMMENDATIONS_TABLE} WHERE id = :id"
                    ),
                    {"id": recommendation_id},
                )
                .mappings()
                .first()
            )
        return _row(found) if found else None

    async def aget(self, recommendation_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get, recommendation_id)

    def list(
        self,
        *,
        use_case_id: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        level: Optional[str] = None,
        owner: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: Dict[str, Any] = {"limit": max(1, min(int(limit), 2000))}
        for column, value in (
            ("use_case_id", use_case_id),
            ("status", status),
            ("kind", kind),
            ("risk_level", level),
            ("owner", owner),
        ):
            if value:
                clauses.append(f"{column} = :{column}")
                params[column] = value
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {_COLUMNS} FROM {RECOMMENDATIONS_TABLE} {where} "
                    # The report's order: still-in-range first (those a rep can still save), then
                    # by what is at stake weighted by how likely it is lost.
                    "ORDER BY COALESCE(CAST(context->>'stalled' AS boolean), false) ASC, "
                    "COALESCE(CAST(context->>'priority' AS double precision), "
                    "value_at_stake * risk_score) DESC, created_at DESC LIMIT :limit"
                ),
                params,
            ).mappings()
            return [_row(r) for r in rows]

    async def alist(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.list(**kwargs))

    def counts(self, use_case_id: Optional[str] = None) -> Dict[str, int]:
        where = "WHERE use_case_id = :id" if use_case_id else ""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT status, COUNT(*) AS n FROM {RECOMMENDATIONS_TABLE} {where} GROUP BY status"
                ),
                {"id": use_case_id} if use_case_id else {},
            ).all()
        out = {s: 0 for s in STATUSES}
        for status, n in rows:
            # A Row unpacks as `object` under the SQLAlchemy stubs CI resolves, so the count
            # is cast rather than left to int()'s overloads.
            out[str(status)] = int(cast(int, n))
        return out

    async def acounts(self, use_case_id: Optional[str] = None) -> Dict[str, int]:
        return await asyncio.to_thread(self.counts, use_case_id)

    def due(
        self, use_case_id: str, *, before: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Records whose horizon has ended and whose outcome is not yet written."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {_COLUMNS} FROM {RECOMMENDATIONS_TABLE} WHERE use_case_id = :id "
                    "AND outcome IS NULL AND expires_at IS NOT NULL AND expires_at <= :before "
                    "ORDER BY expires_at"
                ),
                {"id": use_case_id, "before": before or _now()},
            ).mappings()
            return [_row(r) for r in rows]

    async def adue(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.due(*args, **kwargs))
