"""R12's alert executors (G7): a rule over a scalar KPI, evaluated after gold materialises,
delivered through R7's own path.

Three executors, matching the Alert shape K15 already reserved in
``orchestration/compiler.py`` (``SHAPE_STAGES[UseCaseShape.ALERT]``) before R12 existed to
build them -- the names below are not invented here, they were named there:

* ``evaluate_alert_condition`` (BRANCH) -- deterministic. Reads the metric straight out of
  ``read_gold``'s own content (no separate "compute metrics" step -- K15's own note is that
  gold already holds the computed value) and decides whether the steps after it run.
  ``stop=True`` when the condition is not met, exactly like ``decide_load_verdict``'s refusal
  breaks the nightly chain before gold -- but this function **always returns content**, fired
  or not, because "an alert that does not fire is still logged" (K15's own stage description)
  is the whole of R12's evidence bar: a quiet week must be visibly quiet, not indistinguishable
  from a step that never ran.
* ``write_alert`` (EXECUTOR) -- only meaningful once ``evaluate_alert_condition`` did not stop
  the chain. Writes one ``alert_events`` row in the contract's own ``AlertEvent`` shape.
* ``evaluate_alerts`` -- R12's actual deliverable, "an evaluation executor running after
  materialisation" (delivery-plan.md). A nightly pass over every enabled
  ``alert_definitions`` row: for each one, calls ``read_gold`` (reusing R5's guard,
  provenance notes and freshness stamps rather than re-deriving any of them), then this
  module's own ``evaluate_alert_condition`` and, when it fires, ``write_alert`` -- the same
  three functions a future single-alert compiled Alert-shape workflow would call, so nothing
  here is bespoke to the batch path. Delivers a fired event through R7's own
  ``retail_deliveries`` table (``egpt_retail.delivery.awrite_delivery`, the exact function
  ``deliver_in_app`` itself calls) -- "delivery through the same path as results"
  (delivery-plan.md) -- once per recipient. Updates ``last_evaluated_at`` on *every*
  definition it looks at, fired or not, which is the row a quiet week's silence is legible
  from.

None of the three calls a model. Refusing (or firing) is exactly the kind of deterministic
yes/no the framework's alignment rules reserve for an executor.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.alerts.condition import (
    ConditionUnavailable,
    condition_met,
    render_rule_sentence,
    requires_previous_period,
    shift_date,
)
from egpt_retail.alerts.store import AlertDefinitionStore, AlertEventStore
from egpt_retail.app.db import build_control_plane_engine, psycopg_dsn
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import (
    KpiDefinition,
    ResultShape,
    TimeWindow,
    TimeWindowUnit,
)
from egpt_retail.delivery import awrite_delivery
from egpt_retail.executors.gold import read_gold
from egpt_retail.kpi.store import KpiRegistryStore

__all__ = [
    "ALERT_EXECUTORS",
    "evaluate_alert_condition",
    "write_alert",
    "evaluate_alerts",
]

log = logging.getLogger(__name__)

#: Row columns ``read_gold``'s per-KPI rows always carry, alongside whatever grain-key
#: columns the view projects (``warehouse/gold.py``'s ``view_statements``).
_STANDARD_ROW_COLUMNS = frozenset(
    {"as_of", "value", "computed_at", "source_snapshot", "run_id"}
)


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


def _content_dict(output: StepOutput) -> Dict[str, Any]:
    """``StepOutput.content`` is typed as a broad union by the framework (``str | dict | list
    | BaseModel | Any | None``); every executor in this module always returns a dict, so
    narrow that once here rather than an ``isinstance`` check at every call site."""
    content = output.content
    assert isinstance(content, dict), f"expected dict content, got {type(content)!r}"
    return content


def _select_row(
    rows: Sequence[Mapping[str, Any]], scope: Optional[Mapping[str, Any]]
) -> Optional[Mapping[str, Any]]:
    """The one gold row this alert's scope names.

    A KPI at grain ``overall`` (or one materialised for only one dimension) has exactly one
    row and there is nothing to choose between. A KPI with several dimension rows needs
    ``scope.grain_value`` to say which -- returned only on an exact match against one of the
    row's non-standard (grain-key) columns, never guessed, because picking the wrong brand's
    row and calling it a fired alert is worse than refusing to evaluate at all.
    """
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    grain_value = (scope or {}).get("grain_value")
    if grain_value is None:
        return None
    target = str(grain_value)
    for row in rows:
        for key, value in row.items():
            if key not in _STANDARD_ROW_COLUMNS and str(value) == target:
                return row
    return None


def _time_window(raw: Optional[Mapping[str, Any]]) -> Optional[TimeWindow]:
    if not raw:
        return None
    return TimeWindow(length=int(raw["length"]), unit=TimeWindowUnit(raw["unit"]))


async def evaluate_alert_condition(step_input: StepInput) -> StepOutput:
    """Compare one KPI's current gold value to a threshold. Deterministic; never a model call.

    ``additional_data``: ``kpi_identifier``, ``operator``, ``threshold`` (required);
    ``scope`` (an ``AlertScope``-shaped dict, to pick a row when the KPI has more than one
    dimension); ``previous_value`` (required only for a ``delta_*`` operator -- this function
    does not compute one itself, since that needs a second ``read_gold`` call over a prior
    ``as_of`` its own caller is better placed to make, as :func:`evaluate_alerts` does).

    ``previous_step_content``: ``read_gold``'s own, unmodified content
    (``{"kpis": {...}, "missing": [...]}``) -- there is no separate "compute metrics" stage
    between them (K15's ``SHAPE_STAGES`` marks it ``satisfied_by="read_gold"``: gold already
    holds the computed value).

    Always returns content, whether or not the condition is met -- "an alert that does not
    fire is still logged" is this package's own evidence bar. ``stop=True`` exactly when it is
    *not* met, so a compiled Alert-shape workflow's ``write_alert``/``deliver_in_app`` steps
    after this one do not run for a quiet reading, the same mechanism
    ``decide_load_verdict`` uses to keep a blocked night out of gold.
    """
    options = _options(step_input)
    kpi_identifier = options.get("kpi_identifier")
    operator = options.get("operator")
    threshold = options.get("threshold")
    if not kpi_identifier or operator is None or threshold is None:
        raise ValueError(
            "evaluate_alert_condition requires kpi_identifier, operator and threshold in "
            "additional_data"
        )
    threshold = float(threshold)
    scope = options.get("scope")

    previous = step_input.previous_step_content
    kpis: Mapping[str, Any] = (
        (previous or {}).get("kpis", {}) if isinstance(previous, Mapping) else {}
    )
    missing: Sequence[Mapping[str, Any]] = (
        (previous or {}).get("missing", []) if isinstance(previous, Mapping) else []
    )
    kpi_content = kpis.get(kpi_identifier)
    rows = kpi_content.get("rows", []) if kpi_content else []
    row = _select_row(rows, scope)

    base = {
        "kpi_identifier": kpi_identifier,
        "operator": operator,
        "threshold": threshold,
    }
    if row is None:
        reason = next(
            (m["reason"] for m in missing if m.get("kpi_identifier") == kpi_identifier),
            "no gold row available for this alert's scope",
        )
        return StepOutput(
            content={**base, "fired": False, "value": None, "reason": reason},
            success=True,
            stop=True,
        )

    value = float(row["value"])
    previous_value = options.get("previous_value")
    previous_value = float(previous_value) if previous_value is not None else None

    try:
        result = condition_met(operator, threshold, value, previous_value)
    except ConditionUnavailable as exc:
        return StepOutput(
            content={**base, "fired": False, "value": value, "reason": str(exc)},
            success=True,
            stop=True,
        )

    content = {
        **base,
        "value": value,
        "previous_value": previous_value,
        "fired": result.met,
        "detail": result.detail,
        "as_of": row.get("as_of"),
        "computed_at": row.get("computed_at"),
    }
    return StepOutput(content=content, success=True, stop=not result.met)


async def write_alert(step_input: StepInput) -> StepOutput:
    """Write one ``alert_events`` row -- only meaningful once ``evaluate_alert_condition`` did
    not stop the chain (``previous_step_content["fired"]`` is true). Called with anything else
    is a no-op, defensively, since this function is callable directly and not only ever
    reached through a workflow's ``stop`` mechanism.

    ``additional_data``: ``alert_id``, ``severity``, ``recipients`` are the definition's own
    fields the event freezes; ``alert_name``, ``kpi_name``, ``unit``, ``window`` (an
    ``AlertDefinitionWrite``-shaped window dict), ``scope``, ``recommended_actions``,
    ``use_case_id``, ``process_id`` are optional. ``run_id`` -- pinned the same way R7's
    ``record_provenance`` expects -- becomes both ``run_id`` and ``provenance_ref`` on the
    event: this codebase's convention is that the two agree (``record_provenance``'s own
    return value is ``{"provenance_ref": record.run_id}``), and this step runs *before*
    ``record_provenance`` in K15's own Alert sequence, so it cannot wait for that step's
    output to learn its own run's id.
    """
    options = _options(step_input)
    previous = step_input.previous_step_content
    if not isinstance(previous, Mapping) or not previous.get("fired"):
        return StepOutput(content={"skipped": "condition did not fire"}, success=True)

    kpi_identifier = str(
        previous.get("kpi_identifier") or options.get("kpi_identifier")
    )
    operator = str(previous.get("operator") or options.get("operator"))
    threshold = float(previous.get("threshold", options.get("threshold")))
    value = previous["value"]
    previous_value = previous.get("previous_value")
    window = _time_window(options.get("window"))
    rule = render_rule_sentence(kpi_identifier, operator, threshold, window)

    kpi_name = options.get("kpi_name") or kpi_identifier
    unit = options.get("unit", "") or ""
    scope = options.get("scope") or {}
    run_id = options.get("run_id")

    title = f"{kpi_name} at {value}{unit}"
    summary = f"{kpi_identifier} is {value}{unit}; {rule}."

    event: Dict[str, Any] = {
        "id": options.get("event_id"),
        "alert_id": options["alert_id"],
        "alert_name": options.get("alert_name"),
        "severity": options["severity"],
        "status": "new",
        "title": title,
        "summary": summary,
        "business_area_id": scope.get("business_area"),
        "process_id": options.get("process_id"),
        "use_case_id": options.get("use_case_id"),
        "kpi": {
            "identifier": kpi_identifier,
            "name": kpi_name,
            "value": value,
            "delta": (value - previous_value) if previous_value is not None else None,
            "unit": unit,
            "as_of": previous.get("computed_at")
            or datetime.now(timezone.utc).isoformat(),
        },
        "rule": rule,
        "recommended_actions": list(options.get("recommended_actions") or []),
        "run_id": run_id,
        "provenance_ref": run_id,
    }

    settings = RetailSettings()
    engine = build_control_plane_engine(settings)
    try:
        saved = await AlertEventStore(engine).acreate(event)
    finally:
        engine.dispose()

    return StepOutput(content=saved, success=True)


def _run_id(options: Mapping[str, Any]) -> str:
    pinned = options.get("run_id")
    if pinned:
        return str(pinned)
    log.warning(
        "evaluate_alerts: no additional_data['run_id'] was pinned, so this pass's events are "
        "keyed by a fresh, unlookupable UUID with no relationship to WorkflowRunOutput.run_id"
    )
    return str(uuid.uuid4())


def _fetch_kpi_definitions(
    settings: RetailSettings, identifiers: Sequence[str]
) -> Dict[str, Optional[KpiDefinition]]:
    """Blocking; offloaded by :func:`evaluate_alerts` via ``asyncio.to_thread`` (the same
    lesson ``assess_kpi_feasibility``'s own docstring names: a store call inline in an
    ``async def`` executor stalls the event loop for the whole round trip)."""
    import psycopg

    connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
    try:
        store = KpiRegistryStore(connection)
        return {identifier: store.get(identifier) for identifier in identifiers}
    finally:
        connection.close()


async def evaluate_alerts(step_input: StepInput) -> StepOutput:
    """R12's own deliverable: every enabled ``alert_definitions`` row, evaluated against
    gold, once, after materialisation.

    Reuses ``read_gold`` for the metric read (its guard, provenance notes and freshness
    stamps come along for free -- into the *same* ``additional_data["prov"]`` bag this run's
    ``record_provenance`` step reads, since ``additional_data`` is mutated in place rather
    than copied per call), then this module's own ``evaluate_alert_condition`` and
    ``write_alert`` for each definition -- the same three functions a future single-alert
    compiled Alert-shape workflow would call, so the batch path is not a second
    implementation of the condition or the event shape.

    A ``delta_*`` operator's previous-period value is looked up here (a second ``read_gold``
    call at ``as_of`` shifted back by the definition's ``window``) because that decision
    needs two calls across one alert, which is a batch-level concern neither
    ``evaluate_alert_condition`` nor a single ``read_gold`` call can make on its own.

    A definition whose KPI no longer exists, or was edited to a ``rowset`` shape after the
    alert was created, is not re-validated at creation time here -- it is reported as
    "cannot evaluate" and its own ``last_evaluated_at`` still advances, which is the same
    "ran, and here is why nothing more happened" the Verify scenario's silent case needs.

    ``additional_data``: ``as_of`` (default: latest per KPI, via ``read_gold``'s own
    default), ``alert_ids`` (a subset; default every enabled definition), ``dry_run``
    (forwarded to delivery, same flag ``deliver_in_app`` reads), ``run_id``.
    """
    options = _options(step_input)
    settings = RetailSettings()
    run_id = _run_id(options)
    dry_run = bool(options.get("dry_run", False))
    now = datetime.now(timezone.utc)

    shared: Dict[str, Any]
    if step_input.additional_data is None:
        shared = {}
        step_input.additional_data = shared
    else:
        shared = step_input.additional_data

    control_plane_engine = build_control_plane_engine(settings)
    definition_store = AlertDefinitionStore(control_plane_engine)
    try:
        definitions = await definition_store.alist(enabled=True)
        wanted = options.get("alert_ids")
        if wanted:
            keep = {str(identifier) for identifier in wanted}
            definitions = [d for d in definitions if d["id"] in keep]

        kpi_definitions = await asyncio.to_thread(
            _fetch_kpi_definitions,
            settings,
            sorted({d["kpi_identifier"] for d in definitions}),
        )

        evaluated: List[Dict[str, Any]] = []
        fired_events: List[Dict[str, Any]] = []

        for definition in definitions:
            kpi_identifier = definition["kpi_identifier"]
            kpi = kpi_definitions.get(kpi_identifier)
            if kpi is None or kpi.result_shape != ResultShape.SCALAR:
                reason = (
                    "KPI no longer in the registry"
                    if kpi is None
                    else f"{kpi_identifier} is a rowset and cannot be thresholded"
                )
                evaluated.append(
                    {
                        "alert_id": definition["id"],
                        "kpi_identifier": kpi_identifier,
                        "fired": False,
                        "value": None,
                        "reason": reason,
                    }
                )
                await definition_store.amark_evaluated(
                    definition["id"], at=now, fired=False
                )
                continue

            shared["kpi_identifiers"] = [kpi_identifier]
            if options.get("as_of"):
                shared["as_of"] = options["as_of"]
            else:
                shared.pop("as_of", None)
            current_output = await read_gold(StepInput(additional_data=shared))

            previous_value: Optional[float] = None
            window = definition.get("window")
            time_window = _time_window(window)
            if (
                requires_previous_period(definition["operator"])
                and time_window is not None
            ):
                rows = (
                    _content_dict(current_output)["kpis"]
                    .get(kpi_identifier, {})
                    .get("rows", [])
                )
                row = _select_row(rows, definition.get("scope"))
                if row is not None:
                    prior_as_of = shift_date(
                        date.fromisoformat(row["as_of"]), time_window
                    )
                    shared["kpi_identifiers"] = [kpi_identifier]
                    shared["as_of"] = prior_as_of.isoformat()
                    previous_output = await read_gold(StepInput(additional_data=shared))
                    previous_rows = (
                        _content_dict(previous_output)["kpis"]
                        .get(kpi_identifier, {})
                        .get("rows", [])
                    )
                    previous_row = _select_row(previous_rows, definition.get("scope"))
                    if previous_row is not None:
                        previous_value = float(previous_row["value"])

            condition_input = StepInput(
                additional_data={
                    "kpi_identifier": kpi_identifier,
                    "operator": definition["operator"],
                    "threshold": definition["threshold"],
                    "scope": definition.get("scope"),
                    "previous_value": previous_value,
                },
                previous_step_content=current_output.content,
            )
            condition_output = await evaluate_alert_condition(condition_input)
            condition_content = _content_dict(condition_output)
            fired = bool(condition_content.get("fired"))
            evaluated.append({"alert_id": definition["id"], **condition_content})

            if fired:
                write_input = StepInput(
                    additional_data={
                        "alert_id": definition["id"],
                        "alert_name": definition["name"],
                        "kpi_name": kpi.name,
                        "unit": "",
                        "window": window,
                        "scope": definition.get("scope"),
                        "severity": definition["severity"],
                        "recommended_actions": definition.get("recommended_actions"),
                        "use_case_id": definition.get("use_case_id"),
                        "process_id": definition.get("process_id"),
                        "run_id": run_id,
                    },
                    previous_step_content=condition_content,
                )
                write_output = await write_alert(write_input)
                event = _content_dict(write_output)
                fired_events.append(event)

                delivery_engine = build_control_plane_engine(settings)
                try:
                    for recipient in definition.get("recipients") or []:
                        await awrite_delivery(
                            delivery_engine,
                            id=str(uuid.uuid4()),
                            run_id=run_id,
                            use_case_id=definition.get("use_case_id"),
                            channel=recipient["channel"],
                            dry_run=dry_run,
                            content={
                                "kind": "alert_event",
                                "alert_event": event,
                                "recipient": recipient,
                            },
                            delivered_at=None if dry_run else now,
                        )
                finally:
                    delivery_engine.dispose()

            await definition_store.amark_evaluated(
                definition["id"], at=now, fired=fired
            )
    finally:
        control_plane_engine.dispose()

    log.info(
        "evaluate_alerts run=%s: %d evaluated, %d fired",
        run_id,
        len(evaluated),
        len(fired_events),
    )
    return StepOutput(
        content={
            "run_id": run_id,
            "evaluated": evaluated,
            "fired": [event["id"] for event in fired_events],
            "events": fired_events,
        },
        success=True,
    )


#: Registered in ``app/components.py``; ``evaluate_alerts`` is the "alert-evaluation"
#: workflow's own step, run after ``materialise_gold``. ``evaluate_alert_condition`` and
#: ``write_alert`` are registered alongside it so a future compiled single-alert Alert-shape
#: workflow (K15) resolves both by the names already reserved for them.
ALERT_EXECUTORS = (evaluate_alert_condition, write_alert, evaluate_alerts)
