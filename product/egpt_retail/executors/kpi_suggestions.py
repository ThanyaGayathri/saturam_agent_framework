"""``suggest_kpis`` (contract 1.2.9): the ``harvest-source`` workflow's second step -- the
KPIs a just-harvested source's data supports, proposed and kept.

A person connects a source to find out what they can measure with it. Until this step the
harvest ended at "the tables are in the semantic layer", and getting from there to a KPI meant
finding the KPIs page and starting a minutes-long run by hand -- with nothing kept afterwards
but a run id in a URL. This step closes that gap: when the harvest succeeds, one suggestion run
is started for that source, and its result is stored where ``GET /kpis/suggestions/current``
reads it, so the KPIs page has proposals waiting the next time it is opened and after every
reload.

**It is the same run the KPIs page's "Suggest KPIs" starts** -- the same row in
``semantic.kpi_suggestion_run``, the same agents, the same timeout and phase reporting, the
same failure recording -- run in-process rather than as a task behind an HTTP request, with two
differences: it is scoped to the source (the agents are shown, and feasibility is judged
against, that source's tables only), and it is recorded as that source's run
(``KpiSuggestionRun.source_id``). It calls the router's own executor rather than a copy of it,
so a manual run and this one cannot drift apart; the import runs the wrong way round for a
module under ``executors/`` (the router imports executors, not the reverse) and is done inside
the function for that reason. Splitting that pipeline out of the router is the follow-up.

**A failed suggestion run is not a failed harvest.** The harvest step has already moved the
source to ``modelled`` -- its tables are in the graph and the Data catalog lists them -- and
that stays true whatever happens here. Every failure in this step is recorded on the run row
(where ``GET /kpis/suggestions/current`` reports it as ``failed``) and the step returns
normally; nothing here raises. The one thing that is skipped is the work itself, when
``retail_suggest_after_harvest`` is off.

This step only runs after ``harvest_source`` succeeds: a harvest that fails, or is refused,
stops the workflow before it (``stop=True``), so there is never a suggestion run for a source
with no tables.
"""

from __future__ import annotations

import asyncio
import logging

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.settings import RetailSettings

__all__ = ["suggest_kpis"]

log = logging.getLogger("egpt_retail.executors.kpi_suggestions")


async def suggest_kpis(step_input: StepInput) -> StepOutput:
    """Run one KPI suggestion pass for ``additional_data["source_id"]`` and record it.

    Always returns a successful, non-stopping ``StepOutput`` -- see the module docstring. The
    content says what happened (``status``: ``skipped``, ``succeeded``, ``failed`` or
    ``not_started``) for the workflow run's own record."""
    additional_data = step_input.additional_data or {}
    source_id = additional_data.get("source_id")
    if not source_id:
        return StepOutput(
            content={
                "status": "skipped",
                "detail": "additional_data.source_id is required",
            }
        )

    settings = RetailSettings()
    if not settings.retail_suggest_after_harvest:
        return StepOutput(
            content={
                "source_id": source_id,
                "status": "skipped",
                "detail": "EGPT_RETAIL_SUGGEST_AFTER_HARVEST is off",
            }
        )

    # Inside the function on purpose: see the module docstring.
    from egpt_retail.api.routers import kpis as kpis_router

    try:
        snapshot = await asyncio.to_thread(kpis_router._suggestion_registry_snapshot)
        record = await asyncio.to_thread(
            kpis_router._with_run_store,
            settings,
            lambda store: store.create(None, (), None, source_id),
        )
    except Exception as exc:
        # No row exists to say so, so this is the one failure that is only in the log.
        log.exception("suggest_kpis(%s): could not start a suggestion run", source_id)
        return StepOutput(
            content={
                "source_id": source_id,
                "status": "not_started",
                "detail": f"Could not start a suggestion run ({type(exc).__name__}).",
            }
        )

    # Never raises: it records its own outcome, success or failure, on the row.
    await kpis_router._execute_suggestion_run(
        record.identifier, settings, snapshot, None, (), None, source_id=source_id
    )

    final = await asyncio.to_thread(
        kpis_router._with_run_store,
        settings,
        lambda store: store.get(record.identifier),
    )
    status = final.status.value if final is not None else "unknown"
    count = (
        len(final.result.get("suggestions", []))
        if final is not None and final.result is not None
        else 0
    )
    log.info(
        "suggest_kpis(%s): run %s %s (%d suggestion(s))",
        source_id,
        record.identifier,
        status,
        count,
    )
    return StepOutput(
        content={
            "source_id": source_id,
            "suggestion_run_id": record.identifier,
            "status": status,
            "suggestions": count,
        }
    )
