"""K11 -- the feasibility executor. A set difference, wired as a workflow step.

**This exists to be boring.** Feasibility decides the ready, partial and blocked badges a
customer sees, and the moment a model decides one of them the badges stop meaning anything:
a customer cannot tell a computed answer from a plausible one, and neither can we. So the
executor reads the registry, reads the semantic layer, subtracts one set from the other, and
reports. There is no agent here, no prompt, and nothing to persuade.

What it does add over calling ``assess_registry`` directly is the wiring: it resolves both
stores from settings, it is registered by ``__name__`` under the executor convention, and it
returns the shape the discovery screen renders -- identifier, verdict, missing tables --
without transformation.

Registered as ``assess_kpi_feasibility``. One executor, one name: the registry resolves by
``__name__`` over a flat list and nothing warns on a collision, so this name is reserved for
this function (``contracts/EXECUTOR_REGISTRY.md``).
"""

from __future__ import annotations

import asyncio

from typing import Any, Dict, List, Mapping, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.db import psycopg_dsn
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import Feasibility, FeasibilityVerdict
from egpt_retail.contracts.semantic import SemanticRead
from egpt_retail.kpi.store import KpiRegistryStore
from egpt_retail.semantic.store import SemanticGraphStore

__all__ = ["assess_kpi_feasibility", "feasibility_summary", "verdict_rows"]


def verdict_rows(feasibility: Mapping[str, Feasibility]) -> List[Dict[str, Any]]:
    """The discovery screen's rows, in the order it renders them.

    Blocked first, then partial, then ready: a screen that leads with what works buries the
    thing the customer has to act on. Within a verdict, by identifier, so two runs over an
    unchanged registry produce byte-identical output and a diff between them means something.
    """
    order = {
        FeasibilityVerdict.BLOCKED: 0,
        FeasibilityVerdict.PARTIAL: 1,
        FeasibilityVerdict.READY: 2,
    }
    return [
        {
            "kpi_identifier": result.kpi_identifier,
            "verdict": result.verdict.value,
            "missing_tables": sorted(result.missing_tables),
            "rationale": result.rationale,
        }
        for result in sorted(
            feasibility.values(),
            key=lambda r: (order[r.verdict], r.kpi_identifier),
        )
    ]


def feasibility_summary(feasibility: Mapping[str, Feasibility]) -> Dict[str, int]:
    """Counts per verdict, with every verdict present even at zero.

    Present at zero on purpose: a caller rendering counts from a dict that omitted empty
    verdicts would show no ``blocked`` row at all rather than ``blocked: 0``, and "no blocked
    KPIs" and "the blocked count is missing" look identical on a screen.
    """
    counts = {verdict.value: 0 for verdict in FeasibilityVerdict}
    for result in feasibility.values():
        counts[result.verdict.value] += 1
    return counts


async def assess_kpi_feasibility(step_input: StepInput) -> StepOutput:
    """Feasibility for every registered KPI against the current semantic layer.

    Reads ``business_area`` from ``additional_data`` to narrow the registry when the caller
    wants one area; absent, it assesses everything.

    A blocked KPI is not a failed step. The step answered the question it was asked, and the
    answer is that some KPIs cannot be computed here -- which is the product working, not
    breaking. Nothing in this function raises on a blocked verdict.

    Every store call below is blocking I/O and runs on a worker thread. An ``async def``
    executor that calls a driver inline stalls the event loop for the whole call -- with a
    graph round trip per concept, that is the scheduler's loop held for seconds while nothing
    else in the process can make progress. Round 2 on the P3 PR found this in four executors
    at once, including the two in the file that documents the rule.
    """
    options: Mapping[str, Any] = step_input.additional_data or {}
    settings = RetailSettings()
    area: Optional[str] = options.get("business_area")

    import psycopg
    from neo4j import GraphDatabase

    def _assess() -> Any:
        connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
        driver = GraphDatabase.driver(
            settings.retail_neo4j_uri,
            auth=(settings.retail_neo4j_user, settings.retail_neo4j_password or ""),
        )
        try:
            store = KpiRegistryStore(connection)
            reader: SemanticRead = SemanticGraphStore(
                driver=driver, database=settings.retail_neo4j_database
            )
            definitions = store.list(business_area=area)
            from egpt_retail.contracts.kpi import assess_registry
            from egpt_retail.contracts.semantic import available_tables

            return assess_registry(
                definitions, available_tables(reader)
            ), reader.snapshot()
        finally:
            connection.close()
            driver.close()

    feasibility, snapshot = await asyncio.to_thread(_assess)

    return StepOutput(
        content={
            "snapshot_version": snapshot.version,
            "business_area": area,
            "summary": feasibility_summary(feasibility),
            "kpis": verdict_rows(feasibility),
        }
    )
