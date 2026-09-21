"""``/monitoring`` (K20): execution health and silent schedules for the use cases the
interface created, from the product's own runs table.

The contract has described these since 1.0.0 and the stub served examples. This router
serves them from ``egpt_retail.usecases.health``; ``getQualityAssessment`` stays with the
stub until R15's golden pass rate and R14's drift score are joined here.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends

from egpt_retail.api import models as wire
from egpt_retail.api.routers.use_cases import use_case_service
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.settings import RetailSettings
from egpt_retail.usecases.health import aexecution_health, asilent_schedules
from egpt_retail.usecases.service import UseCaseService

__all__ = ["router", "schedule_trigger"]

router = APIRouter()


def schedule_trigger() -> Optional[str]:
    """What fires schedules where this platform runs: the in-process poller when switched
    on, the orchestrator in the cloud (EGPT_RETAIL_SCHEDULE_TRIGGER), else nothing."""
    settings = RetailSettings()
    if settings.retail_schedule_trigger:
        return settings.retail_schedule_trigger
    return "poller" if settings.retail_use_case_poller else None


@router.get("/monitoring/execution-health", response_model=wire.ExecutionHealth)
async def get_execution_health(
    service: UseCaseService = Depends(use_case_service),
    trigger: Optional[str] = Depends(schedule_trigger),
    _: None = Depends(require_scopes("monitoring:read")),
) -> wire.ExecutionHealth:
    return wire.ExecutionHealth.model_validate(
        await aexecution_health(service.store, trigger=trigger)
    )


@router.get("/monitoring/schedules/silent", response_model=List[wire.SilentSchedule])
async def list_silent_schedules(
    service: UseCaseService = Depends(use_case_service),
    trigger: Optional[str] = Depends(schedule_trigger),
    _: None = Depends(require_scopes("monitoring:read")),
) -> List[wire.SilentSchedule]:
    rows = await asilent_schedules(service.store, trigger=trigger)
    return [wire.SilentSchedule.model_validate(r) for r in rows]
