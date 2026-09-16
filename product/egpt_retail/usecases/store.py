"""Reading and writing use cases and their runs (K16).

A thin store over the two tables in ``ddl.py``. Every public method has a sync and an async
variant; the async ones run the sync ones off the event loop, the way ``egpt_retail.delivery``
does. Rows come back as plain dicts in the contract's own field names so the router can hand
them to the generated models without renaming anything.

Nothing here knows what a workflow is: ``compiled`` is stored and returned as the JSON the
compiler produced, and ``result`` as the JSON the last executor returned.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.usecases.ddl import CASES_TABLE, RUNS_TABLE, USE_CASES_TABLE

__all__ = ["UseCaseStore", "new_use_case_id", "new_run_id", "new_case_id"]

_USE_CASE_COLUMNS = (
    "id, title, description, business_area, status, source, level, autonomy, tags, version, "
    "schedule, workflow_component_id, draft, spec, compiled, created_at, updated_at"
)
_RUN_COLUMNS = (
    "id, use_case_id, use_case_version, status, trigger, mode, started_at, finished_at, "
    "framework_run_id, output_ref, provenance_ref, summary, blocked_reason, result, request, "
    "created_at"
)
_CASE_COLUMNS = (
    "id, use_case_id, label, source, sender, subject, filename, document_b64, expected, "
    "last_run_id, last_status, last_result, created_at, updated_at"
)


def new_use_case_id() -> str:
    return "uc_" + uuid.uuid4().hex[:8]


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:8]


def new_case_id() -> str:
    return "case_" + uuid.uuid4().hex[:8]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, default=str)


def _row(mapping: Any) -> Dict[str, Any]:
    row = dict(mapping)
    for key in (
        "tags",
        "draft",
        "spec",
        "compiled",
        "result",
        "request",
        "expected",
        "last_result",
    ):
        value = row.get(key)
        if isinstance(value, str):
            row[key] = json.loads(value)
    return row


class UseCaseStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # -- use cases -----------------------------------------------------------------------

    def create(
        self,
        *,
        title: str,
        description: str,
        business_area: str,
        autonomy: str = "copilot",
        level: int = 1,
        tags: Optional[List[str]] = None,
        source: str = "user",
        use_case_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        identifier = use_case_id or new_use_case_id()
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {USE_CASES_TABLE} (id, title, description, business_area, "
                    "status, source, level, autonomy, tags) VALUES (:id, :title, :description, "
                    ":business_area, 'draft', :source, :level, :autonomy, CAST(:tags AS jsonb))"
                ),
                {
                    "id": identifier,
                    "title": title,
                    "description": description,
                    "business_area": business_area,
                    "source": source,
                    "level": level,
                    "autonomy": autonomy,
                    "tags": _json(tags or []),
                },
            )
        found = self.get(identifier)
        assert found is not None
        return found

    async def acreate(self, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(lambda: self.create(**kwargs))

    def get(self, use_case_id: str) -> Optional[Dict[str, Any]]:
        with self._engine.connect() as connection:
            result = (
                connection.execute(
                    text(
                        f"SELECT {_USE_CASE_COLUMNS} FROM {USE_CASES_TABLE} WHERE id = :id"
                    ),
                    {"id": use_case_id},
                )
                .mappings()
                .first()
            )
        return _row(result) if result is not None else None

    async def aget(self, use_case_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get, use_case_id)

    def list(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        clause = " WHERE status = :status" if status else ""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {_USE_CASE_COLUMNS} FROM {USE_CASES_TABLE}{clause} ORDER BY updated_at DESC"
                ),
                {"status": status} if status else {},
            ).mappings()
            return [_row(r) for r in rows]

    async def alist(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.list(status=status))

    def update(self, use_case_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """Set any of the catalog or artifact columns. JSON columns are serialised here."""
        allowed = {
            "title",
            "description",
            "business_area",
            "status",
            "level",
            "autonomy",
            "tags",
            "version",
            "schedule",
            "workflow_component_id",
            "draft",
            "spec",
            "compiled",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if not fields:
            return self.get(use_case_id)
        assignments = []
        params: Dict[str, Any] = {"id": use_case_id, "updated_at": _now()}
        for key, value in fields.items():
            if key in {"tags", "draft", "spec", "compiled"}:
                assignments.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = _json(value)
            else:
                assignments.append(f"{key} = :{key}")
                params[key] = value
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE {USE_CASES_TABLE} SET {', '.join(assignments)}, updated_at = :updated_at WHERE id = :id"
                ),
                params,
            )
        return self.get(use_case_id)

    async def aupdate(
        self, use_case_id: str, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.update(use_case_id, **fields))

    def delete(self, use_case_id: str) -> bool:
        with self._engine.begin() as connection:
            deleted = connection.execute(
                text(f"DELETE FROM {USE_CASES_TABLE} WHERE id = :id"),
                {"id": use_case_id},
            ).rowcount
        return bool(deleted)

    async def adelete(self, use_case_id: str) -> bool:
        return await asyncio.to_thread(self.delete, use_case_id)

    # -- runs ----------------------------------------------------------------------------

    def create_run(
        self,
        *,
        use_case_id: str,
        use_case_version: int,
        trigger: str = "manual",
        mode: str = "live",
        run_id: Optional[str] = None,
        request: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        identifier = run_id or new_run_id()
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {RUNS_TABLE} (id, use_case_id, use_case_version, status, trigger, mode, request) "
                    "VALUES (:id, :use_case_id, :version, 'queued', :trigger, :mode, CAST(:request AS jsonb))"
                ),
                {
                    "id": identifier,
                    "use_case_id": use_case_id,
                    "version": use_case_version,
                    "trigger": trigger,
                    "mode": mode,
                    "request": _json(dict(request) if request else None),
                },
            )
        found = self.get_run(identifier)
        assert found is not None
        return found

    async def acreate_run(self, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(lambda: self.create_run(**kwargs))

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._engine.connect() as connection:
            result = (
                connection.execute(
                    text(f"SELECT {_RUN_COLUMNS} FROM {RUNS_TABLE} WHERE id = :id"),
                    {"id": run_id},
                )
                .mappings()
                .first()
            )
        return _row(result) if result is not None else None

    async def aget_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_run, run_id)

    def list_runs(
        self, use_case_id: str, *, status: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        clause = " AND status = :status" if status else ""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {_RUN_COLUMNS} FROM {RUNS_TABLE} WHERE use_case_id = :use_case_id{clause} "
                    "ORDER BY started_at DESC LIMIT :limit"
                ),
                {"use_case_id": use_case_id, "status": status, "limit": limit},
            ).mappings()
            return [_row(r) for r in rows]

    async def alist_runs(
        self, use_case_id: str, *, status: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            lambda: self.list_runs(use_case_id, status=status, limit=limit)
        )

    def update_run(self, run_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        allowed = {
            "status",
            "finished_at",
            "framework_run_id",
            "output_ref",
            "provenance_ref",
            "summary",
            "blocked_reason",
            "result",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        assignments = []
        params: Dict[str, Any] = {"id": run_id}
        for key, value in fields.items():
            if key == "result":
                assignments.append("result = CAST(:result AS jsonb)")
                params[key] = _json(value)
            else:
                assignments.append(f"{key} = :{key}")
                params[key] = value
        if assignments:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        f"UPDATE {RUNS_TABLE} SET {', '.join(assignments)} WHERE id = :id"
                    ),
                    params,
                )
        return self.get_run(run_id)

    async def aupdate_run(self, run_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.update_run(run_id, **fields))

    def fail_stale_runs(
        self, reason: str = "interrupted: the process running it stopped"
    ) -> int:
        """Mark every ``queued``/``running`` run as failed. Called once at process start: a
        run only ever executes inside the process that queued it, so anything still open when
        a process boots was cut off by the previous one stopping, and must not sit at
        ``running`` forever."""
        with self._engine.begin() as connection:
            count = connection.execute(
                text(
                    f"UPDATE {RUNS_TABLE} SET status = 'failed', finished_at = :now, blocked_reason = :reason "
                    "WHERE status IN ('queued', 'running')"
                ),
                {"now": _now(), "reason": reason},
            ).rowcount
        return int(count or 0)

    async def afail_stale_runs(
        self, reason: str = "interrupted: the process running it stopped"
    ) -> int:
        return await asyncio.to_thread(self.fail_stale_runs, reason)

    def processed_message_ids(self, use_case_id: str, *, limit: int = 200) -> List[str]:
        """Every message id a finished run of this use case carried -- created or held -- so
        the next poll carries none of them twice. The message id is the provenance key; the
        mailbox itself is never written to."""
        ids: List[str] = []
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT result FROM {RUNS_TABLE} WHERE use_case_id = :use_case_id "
                    "AND status = 'succeeded' AND mode = 'live' AND result IS NOT NULL "
                    "ORDER BY started_at DESC LIMIT :limit"
                ),
                {"use_case_id": use_case_id, "limit": limit},
            ).mappings()
            for row in rows:
                result = row["result"]
                if isinstance(result, str):
                    result = json.loads(result)
                for document in (result or {}).get("documents") or []:
                    message_id = (document.get("message") or {}).get("id")
                    if message_id:
                        ids.append(str(message_id))
                for exception in (result or {}).get("exceptions") or []:
                    if exception.get("message_id"):
                        ids.append(str(exception["message_id"]))
        return sorted(set(ids))

    async def aprocessed_message_ids(
        self, use_case_id: str, *, limit: int = 200
    ) -> List[str]:
        return await asyncio.to_thread(
            lambda: self.processed_message_ids(use_case_id, limit=limit)
        )

    # -- golden cases (K19) ---------------------------------------------------------------

    def create_case(
        self,
        *,
        use_case_id: str,
        label: str,
        document_b64: str,
        sender: str = "",
        subject: str = "",
        filename: str = "document.pdf",
        source: str = "uploaded",
        expected: Optional[Mapping[str, Any]] = None,
        case_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        identifier = case_id or new_case_id()
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {CASES_TABLE} (id, use_case_id, label, source, sender, subject, filename, document_b64, expected) "
                    "VALUES (:id, :use_case_id, :label, :source, :sender, :subject, :filename, :document_b64, CAST(:expected AS jsonb))"
                ),
                {
                    "id": identifier,
                    "use_case_id": use_case_id,
                    "label": label,
                    "source": source,
                    "sender": sender,
                    "subject": subject,
                    "filename": filename,
                    "document_b64": document_b64,
                    "expected": _json(dict(expected) if expected else None),
                },
            )
        found = self.get_case(identifier)
        assert found is not None
        return found

    async def acreate_case(self, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(lambda: self.create_case(**kwargs))

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        with self._engine.connect() as connection:
            result = (
                connection.execute(
                    text(f"SELECT {_CASE_COLUMNS} FROM {CASES_TABLE} WHERE id = :id"),
                    {"id": case_id},
                )
                .mappings()
                .first()
            )
        return _row(result) if result is not None else None

    async def aget_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_case, case_id)

    def list_cases(self, use_case_id: str) -> List[Dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {_CASE_COLUMNS} FROM {CASES_TABLE} WHERE use_case_id = :use_case_id ORDER BY created_at"
                ),
                {"use_case_id": use_case_id},
            ).mappings()
            return [_row(r) for r in rows]

    async def alist_cases(self, use_case_id: str) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.list_cases, use_case_id)

    def update_case(self, case_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        allowed = {
            "label",
            "sender",
            "subject",
            "expected",
            "last_run_id",
            "last_status",
            "last_result",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        assignments = []
        params: Dict[str, Any] = {"id": case_id, "updated_at": _now()}
        for key, value in fields.items():
            if key in {"expected", "last_result"}:
                assignments.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = _json(value)
            else:
                assignments.append(f"{key} = :{key}")
                params[key] = value
        if assignments:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        f"UPDATE {CASES_TABLE} SET {', '.join(assignments)}, updated_at = :updated_at WHERE id = :id"
                    ),
                    params,
                )
        return self.get_case(case_id)

    async def aupdate_case(
        self, case_id: str, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(lambda: self.update_case(case_id, **fields))

    def delete_case(self, case_id: str) -> bool:
        with self._engine.begin() as connection:
            deleted = connection.execute(
                text(f"DELETE FROM {CASES_TABLE} WHERE id = :id"), {"id": case_id}
            ).rowcount
        return bool(deleted)

    async def adelete_case(self, case_id: str) -> bool:
        return await asyncio.to_thread(self.delete_case, case_id)

    def last_run(self, use_case_id: str) -> Optional[Dict[str, Any]]:
        runs = self.list_runs(use_case_id, limit=1)
        return runs[0] if runs else None

    async def alast_run(self, use_case_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.last_run, use_case_id)
