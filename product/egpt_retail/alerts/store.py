"""Alert definition and alert event storage, in the control-plane database (R12, G7).

Two stores, matching the two tables ``alerts/ddl.py`` creates and the contract's own split
(``AlertDefinition`` vs. ``AlertEvent``): a definition is a rule a person authors and edits; an
event is one firing of it, frozen at ``fired_at``. Mutating an event never touches its
definition, and editing a definition never rewrites an event it already fired -- the same
promise ``updateAlertDefinition``'s contract summary makes, kept here by simply never letting
one table's write touch the other's row.

Every public method is both sync and async, per the standing rule -- these are library storage
code, not workflow executors, so the executor registry's async-only exemption
(``contracts/EXECUTOR_REGISTRY.md``) does not apply here the way it does to
``egpt_retail.executors.alerts``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.alerts.ddl import ALERT_DEFINITIONS_TABLE, ALERT_EVENTS_TABLE
from egpt_retail.contracts.kpi import KpiDefinition, ResultShape

__all__ = [
    "AlertDefinitionRejected",
    "AlertDefinitionStore",
    "AlertEventStore",
    "validate_alert_target",
]


class AlertDefinitionRejected(ValueError):
    """A definition may not be created or updated as given. Carries ``code``, the contract's
    own machine-readable field (``common.yaml#/Error.code``) -- e.g. ``kpi_not_scalar``, the
    422 the API contract's own worked example names."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def validate_alert_target(kpi: Optional[KpiDefinition]) -> None:
    """Refuse a definition that targets a KPI that cannot be alerted on.

    Deterministic, called at creation (and at update, since a definition's ``kpi_identifier``
    may be edited) -- never a model's call to make. Mirrors the contract's own worked example
    verbatim: ``"stocklevel__reorder_urgency_list is a rowset and cannot be thresholded.
    Alerts need a scalar KPI."``, ``code: kpi_not_scalar``.
    """
    if kpi is None:
        raise AlertDefinitionRejected("kpi_not_found", "No such KPI in the registry.")
    if kpi.result_shape != ResultShape.SCALAR:
        raise AlertDefinitionRejected(
            "kpi_not_scalar",
            f"{kpi.identifier} is a rowset and cannot be thresholded. Alerts need a "
            "scalar KPI.",
        )


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _parsed(value: Any) -> Any:
    """A jsonb column's value, whichever shape the driver handed back.

    psycopg3 auto-adapts ``jsonb`` to a native Python object, but ``text()`` over a bare
    ``Engine`` is not guaranteed to route through that codec on every driver/pool
    combination -- so a plain JSON string is parsed rather than assumed away, the same
    defensiveness ``kpi/store.py``'s own ``row_to_definition`` applies to its own jsonb
    columns (there via a raw psycopg cursor, which never auto-adapts).
    """
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_to_definition(row: Any) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kpi_identifier": row["kpi_identifier"],
        "operator": row["operator"],
        "threshold": float(row["threshold"]),
        "window": _parsed(row["time_window"]),
        "scope": _parsed(row["scope"]),
        "severity": row["severity"],
        "recipients": _parsed(row["recipients"]) or [],
        "recommended_actions": _parsed(row["recommended_actions"]) or [],
        "enabled": row["enabled"],
        "use_case_id": row["use_case_id"],
        "process_id": row["process_id"],
        "created_at": row["created_at"],
        "last_evaluated_at": row["last_evaluated_at"],
        "last_fired_at": row["last_fired_at"],
    }


def _row_to_event(row: Any) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "alert_id": row["alert_id"],
        "alert_name": row["alert_name"],
        "severity": row["severity"],
        "status": row["status"],
        "title": row["title"],
        "summary": row["summary"],
        "business_area_id": row["business_area_id"],
        "process_id": row["process_id"],
        "use_case_id": row["use_case_id"],
        "kpi": _parsed(row["kpi"]),
        "rule": row["rule"],
        "recommended_actions": _parsed(row["recommended_actions"]) or [],
        "fired_at": row["fired_at"],
        "acknowledged_at": row["acknowledged_at"],
        "resolved_at": row["resolved_at"],
        "snooze_until": row["snooze_until"],
        "run_id": row["run_id"],
        "provenance_ref": row["provenance_ref"],
    }


class AlertDefinitionStore:
    """CRUD over ``alert_definitions``. The connection is injected; the caller owns its
    lifetime, matching ``KpiRegistryStore`` and ``egpt_retail.delivery.store``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- writing -------------------------------------------------------------------------

    def create(
        self,
        *,
        kpi: KpiDefinition,
        name: str,
        kpi_identifier: str,
        operator: str,
        threshold: float,
        severity: str,
        window: Optional[Dict[str, Any]] = None,
        scope: Optional[Dict[str, Any]] = None,
        recipients: Optional[List[Dict[str, Any]]] = None,
        recommended_actions: Optional[List[str]] = None,
        enabled: bool = True,
        use_case_id: Optional[str] = None,
        process_id: Optional[str] = None,
        id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Insert one definition. Refuses (``AlertDefinitionRejected``) when ``kpi`` is not a
        ``scalar`` KPI -- the caller looks it up (``KpiRegistryStore.get(kpi_identifier)``)
        and passes it in, rather than this store reaching into the KPI registry itself, so a
        store that only ever touches its own two tables stays true."""
        validate_alert_target(kpi)
        row_id = id or f"alr_{uuid.uuid4().hex[:12]}"
        created = created_at or datetime.now(timezone.utc)
        params = {
            "id": row_id,
            "name": name,
            "kpi_identifier": kpi_identifier,
            "operator": operator,
            "threshold": threshold,
            "time_window": _dumps(window) if window is not None else None,
            "scope": _dumps(scope) if scope is not None else None,
            "severity": severity,
            "recipients": _dumps(recipients or []),
            "recommended_actions": _dumps(recommended_actions or []),
            "enabled": enabled,
            "use_case_id": use_case_id,
            "process_id": process_id,
            "created_at": created,
        }
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {ALERT_DEFINITIONS_TABLE} "
                    "(id, name, kpi_identifier, operator, threshold, time_window, scope, "
                    "severity, recipients, recommended_actions, enabled, use_case_id, "
                    "process_id, created_at) "
                    "VALUES (:id, :name, :kpi_identifier, :operator, :threshold, "
                    "CAST(:time_window AS jsonb), CAST(:scope AS jsonb), :severity, "
                    "CAST(:recipients AS jsonb), CAST(:recommended_actions AS jsonb), "
                    ":enabled, :use_case_id, :process_id, :created_at)"
                ),
                params,
            )
        result = self.get(row_id)
        assert result is not None  # just inserted, in the same connection pool
        return result

    async def acreate(self, **kwargs: Any) -> Dict[str, Any]:
        """Async variant."""
        return await asyncio.to_thread(lambda: self.create(**kwargs))

    def update(
        self,
        id: str,
        *,
        kpi: Optional[KpiDefinition] = None,
        kpi_identifier: Optional[str] = None,
        name: Optional[str] = None,
        operator: Optional[str] = None,
        threshold: Optional[float] = None,
        window: Optional[Dict[str, Any]] = None,
        scope: Optional[Dict[str, Any]] = None,
        severity: Optional[str] = None,
        recipients: Optional[List[Dict[str, Any]]] = None,
        recommended_actions: Optional[List[str]] = None,
        enabled: Optional[bool] = None,
        use_case_id: Optional[str] = None,
        process_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Edit an existing definition -- the contract's own ``updateAlertDefinition``
        (P5's HTTP path; this is the store method it will call). Every field is optional and
        only a field actually passed overwrites the stored one, the same partial-update shape
        the contract's ``AlertDefinitionWrite`` implies for a PATCH-like edit.

        Re-runs :func:`validate_alert_target` exactly when ``kpi_identifier`` is being
        changed -- refusing a retarget to a KPI that cannot be alerted on
        (``AlertDefinitionRejected``, ``code=kpi_not_scalar``/``kpi_not_found``) the same way
        :meth:`create` refuses one at authoring time. The caller looks the new KPI up and
        passes it as ``kpi``, matching :meth:`create`'s own division of labour (this store
        never reaches into the KPI registry itself); passing ``kpi_identifier`` without ``kpi``
        is a caller error and raises ``ValueError``, since skipping the lookup would silently
        skip the one check this method exists to enforce.

        A threshold-only edit -- this package's own worked example, a person tightening a
        rule after a quiet week -- passes neither ``kpi`` nor ``kpi_identifier`` and is never
        re-validated, since the KPI did not change.
        """
        if kpi_identifier is not None and kpi is None:
            raise ValueError(
                "update(kpi_identifier=...) requires kpi=<the looked-up KpiDefinition>, so "
                "validate_alert_target can run against it -- the same way create() does"
            )
        if kpi is not None:
            validate_alert_target(kpi)

        fields: Dict[str, Any] = {
            "name": name,
            "kpi_identifier": kpi_identifier,
            "operator": operator,
            "threshold": threshold,
            "window": window,
            "scope": scope,
            "severity": severity,
            "recipients": recipients,
            "recommended_actions": recommended_actions,
            "enabled": enabled,
            "use_case_id": use_case_id,
            "process_id": process_id,
        }
        given = {key: value for key, value in fields.items() if value is not None}
        if not given:
            return self.get(id)

        set_clauses = []
        params: Dict[str, Any] = {"id": id}
        for key, value in given.items():
            if key in ("window", "scope", "recipients", "recommended_actions"):
                set_clauses.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = _dumps(value)
            else:
                set_clauses.append(f"{key} = :{key}")
                params[key] = value

        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE {ALERT_DEFINITIONS_TABLE} SET {', '.join(set_clauses)} "
                    "WHERE id = :id"
                ),
                params,
            )
        return self.get(id)

    async def aupdate(self, id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(lambda: self.update(id, **kwargs))

    def mark_evaluated(
        self, id: str, *, at: datetime, fired: bool
    ) -> Optional[Dict[str, Any]]:
        """Bump ``last_evaluated_at`` on every run, and ``last_fired_at`` only when it fired.

        This is the row a quiet week's silence is legible from: ``last_evaluated_at``
        advances whether or not the condition was met, so "never ran" and "ran and did not
        fire" are never the same reading (R12's own evidence line).
        """
        with self._engine.begin() as connection:
            if fired:
                connection.execute(
                    text(
                        f"UPDATE {ALERT_DEFINITIONS_TABLE} "
                        "SET last_evaluated_at = :at, last_fired_at = :at WHERE id = :id"
                    ),
                    {"at": at, "id": id},
                )
            else:
                connection.execute(
                    text(
                        f"UPDATE {ALERT_DEFINITIONS_TABLE} "
                        "SET last_evaluated_at = :at WHERE id = :id"
                    ),
                    {"at": at, "id": id},
                )
        return self.get(id)

    async def amark_evaluated(
        self, id: str, *, at: datetime, fired: bool
    ) -> Optional[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(self.mark_evaluated, id, at=at, fired=fired)

    def delete(self, id: str) -> bool:
        """Remove one definition. Its past events are kept -- the contract's own
        ``deleteAlertDefinition`` summary -- because nothing here touches ``alert_events``."""
        with self._engine.begin() as connection:
            result = connection.execute(
                text(f"DELETE FROM {ALERT_DEFINITIONS_TABLE} WHERE id = :id"),
                {"id": id},
            )
        return bool(result.rowcount)

    async def adelete(self, id: str) -> bool:
        """Async variant."""
        return await asyncio.to_thread(self.delete, id)

    # --- reading ---------------------------------------------------------------------------

    def get(self, id: str) -> Optional[Dict[str, Any]]:
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(f"SELECT * FROM {ALERT_DEFINITIONS_TABLE} WHERE id = :id"),
                    {"id": id},
                )
                .mappings()
                .first()
            )
        return _row_to_definition(row) if row is not None else None

    async def aget(self, id: str) -> Optional[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(self.get, id)

    def list(
        self,
        *,
        kpi_identifier: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: Dict[str, Any] = {}
        if kpi_identifier is not None:
            clauses.append("kpi_identifier = :kpi_identifier")
            params["kpi_identifier"] = kpi_identifier
        if enabled is not None:
            clauses.append("enabled = :enabled")
            params["enabled"] = enabled
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._engine.begin() as connection:
            rows = (
                connection.execute(
                    text(
                        f"SELECT * FROM {ALERT_DEFINITIONS_TABLE} {where} ORDER BY created_at"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return [_row_to_definition(row) for row in rows]

    async def alist(
        self,
        *,
        kpi_identifier: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(
            lambda: self.list(kpi_identifier=kpi_identifier, enabled=enabled)
        )


class AlertEventStore:
    """CRUD over ``alert_events``. One row per firing; mutating status here never touches
    ``alert_definitions``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- writing -------------------------------------------------------------------------

    def create(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Insert one event. ``event`` carries the contract's own ``AlertEvent`` field names
        (``id``, ``alert_id``, ``kpi``, ``fired_at``, ...); a missing ``id`` or ``fired_at``
        is filled in here so a caller building the dict inline need not invent either."""
        row_id = event.get("id") or f"aev_{uuid.uuid4().hex[:12]}"
        fired_at = event.get("fired_at") or datetime.now(timezone.utc)
        params = {
            "id": row_id,
            "alert_id": event["alert_id"],
            "alert_name": event.get("alert_name"),
            "severity": event["severity"],
            "status": event.get("status", "new"),
            "title": event["title"],
            "summary": event["summary"],
            "business_area_id": event.get("business_area_id"),
            "process_id": event.get("process_id"),
            "use_case_id": event.get("use_case_id"),
            "kpi": _dumps(event["kpi"]),
            "rule": event.get("rule"),
            "recommended_actions": _dumps(event.get("recommended_actions") or []),
            "fired_at": fired_at,
            "run_id": event.get("run_id"),
            "provenance_ref": event.get("provenance_ref"),
        }
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {ALERT_EVENTS_TABLE} "
                    "(id, alert_id, alert_name, severity, status, title, summary, "
                    "business_area_id, process_id, use_case_id, kpi, rule, "
                    "recommended_actions, fired_at, run_id, provenance_ref) "
                    "VALUES (:id, :alert_id, :alert_name, :severity, :status, :title, "
                    ":summary, :business_area_id, :process_id, :use_case_id, "
                    "CAST(:kpi AS jsonb), :rule, CAST(:recommended_actions AS jsonb), "
                    ":fired_at, :run_id, :provenance_ref)"
                ),
                params,
            )
        result = self.get(row_id)
        assert result is not None
        return result

    async def acreate(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Async variant."""
        return await asyncio.to_thread(self.create, event)

    def create_or_get(self, event: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Insert one event, unless ``(alert_id, as_of)`` already has one -- the same
        evaluation window already recorded a firing. Returns ``(row, True)`` when this call
        inserted the row, or ``(row, False)`` when an existing row was returned instead.

        Race-safe under concurrent callers: this relies on the database's own
        ``alert_events_alert_id_as_of_uidx`` UNIQUE index (``alerts/ddl.py``) via
        ``INSERT ... ON CONFLICT ... DO NOTHING``, not a check-then-insert -- two concurrent
        ``evaluate_alerts`` runs (or one retried after a partial failure, or a manual re-run
        while the KPI is still over threshold) racing on the same definition and the same
        ``as_of`` can never both win the insert, so recipients are never paged twice for one
        logical firing. ``event["as_of"]`` absent (``None``) never conflicts with anything --
        Postgres never treats two NULLs as equal -- so an event with no evaluation-window date
        to key on is simply never deduplicated, the same "no signal, no guess" the index's own
        comment describes.
        """
        row_id = event.get("id") or f"aev_{uuid.uuid4().hex[:12]}"
        fired_at = event.get("fired_at") or datetime.now(timezone.utc)
        as_of = event.get("as_of")
        params = {
            "id": row_id,
            "alert_id": event["alert_id"],
            "alert_name": event.get("alert_name"),
            "severity": event["severity"],
            "status": event.get("status", "new"),
            "title": event["title"],
            "summary": event["summary"],
            "business_area_id": event.get("business_area_id"),
            "process_id": event.get("process_id"),
            "use_case_id": event.get("use_case_id"),
            "kpi": _dumps(event["kpi"]),
            "rule": event.get("rule"),
            "recommended_actions": _dumps(event.get("recommended_actions") or []),
            "fired_at": fired_at,
            "run_id": event.get("run_id"),
            "provenance_ref": event.get("provenance_ref"),
            "as_of": as_of,
        }
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        f"INSERT INTO {ALERT_EVENTS_TABLE} "
                        "(id, alert_id, alert_name, severity, status, title, summary, "
                        "business_area_id, process_id, use_case_id, kpi, rule, "
                        "recommended_actions, fired_at, run_id, provenance_ref, as_of) "
                        "VALUES (:id, :alert_id, :alert_name, :severity, :status, :title, "
                        ":summary, :business_area_id, :process_id, :use_case_id, "
                        "CAST(:kpi AS jsonb), :rule, CAST(:recommended_actions AS jsonb), "
                        ":fired_at, :run_id, :provenance_ref, :as_of) "
                        "ON CONFLICT (alert_id, as_of) DO NOTHING RETURNING *"
                    ),
                    params,
                )
                .mappings()
                .first()
            )
            if row is not None:
                return _row_to_event(row), True
        existing = self.get_by_alert_and_as_of(event["alert_id"], as_of)
        assert existing is not None, (
            "create_or_get: the insert conflicted, but no existing row was found for the "
            "same (alert_id, as_of) -- the unique index and this lookup have fallen out of "
            "agreement"
        )
        return existing, False

    async def acreate_or_get(
        self, event: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        """Async variant."""
        return await asyncio.to_thread(self.create_or_get, event)

    def update_status(
        self,
        id: str,
        status: str,
        *,
        acknowledged_at: Optional[datetime] = None,
        resolved_at: Optional[datetime] = None,
        snooze_until: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Move one event between ``new``/``acknowledged``/``snoozed``/``resolved``. Not
        exercised by R12's own Verify scenario (which only ever creates ``new`` events) but
        needed for the store to be a usable seam for R11's inbox screen later -- the four
        ``/alert-events/{id}/{acknowledge,snooze,resolve,reopen}`` operations the contract
        already names all reduce to this one update."""
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE {ALERT_EVENTS_TABLE} SET status = :status, "
                    "acknowledged_at = COALESCE(:acknowledged_at, acknowledged_at), "
                    "resolved_at = COALESCE(:resolved_at, resolved_at), "
                    "snooze_until = COALESCE(:snooze_until, snooze_until) "
                    "WHERE id = :id"
                ),
                {
                    "id": id,
                    "status": status,
                    "acknowledged_at": acknowledged_at,
                    "resolved_at": resolved_at,
                    "snooze_until": snooze_until,
                },
            )
        return self.get(id)

    async def aupdate_status(
        self,
        id: str,
        status: str,
        *,
        acknowledged_at: Optional[datetime] = None,
        resolved_at: Optional[datetime] = None,
        snooze_until: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(
            lambda: self.update_status(
                id,
                status,
                acknowledged_at=acknowledged_at,
                resolved_at=resolved_at,
                snooze_until=snooze_until,
            )
        )

    # --- reading ---------------------------------------------------------------------------

    def get(self, id: str) -> Optional[Dict[str, Any]]:
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(f"SELECT * FROM {ALERT_EVENTS_TABLE} WHERE id = :id"),
                    {"id": id},
                )
                .mappings()
                .first()
            )
        return _row_to_event(row) if row is not None else None

    async def aget(self, id: str) -> Optional[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(self.get, id)

    def get_by_alert_and_as_of(
        self, alert_id: str, as_of: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """The event (if any) already recorded for this definition's evaluation window --
        :meth:`create_or_get`'s own lookup after an ``ON CONFLICT`` no-op, exposed separately
        since a caller may want to check without also being prepared to insert."""
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        f"SELECT * FROM {ALERT_EVENTS_TABLE} "
                        "WHERE alert_id = :alert_id AND as_of IS NOT DISTINCT FROM :as_of"
                    ),
                    {"alert_id": alert_id, "as_of": as_of},
                )
                .mappings()
                .first()
            )
        return _row_to_event(row) if row is not None else None

    async def aget_by_alert_and_as_of(
        self, alert_id: str, as_of: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(self.get_by_alert_and_as_of, alert_id, as_of)

    def list(
        self,
        *,
        alert_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: Dict[str, Any] = {}
        if alert_id is not None:
            clauses.append("alert_id = :alert_id")
            params["alert_id"] = alert_id
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._engine.begin() as connection:
            rows = (
                connection.execute(
                    text(
                        f"SELECT * FROM {ALERT_EVENTS_TABLE} {where} ORDER BY fired_at DESC"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return [_row_to_event(row) for row in rows]

    async def alist(
        self,
        *,
        alert_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Async variant."""
        return await asyncio.to_thread(
            lambda: self.list(alert_id=alert_id, status=status)
        )
