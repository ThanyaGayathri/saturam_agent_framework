import logging
from datetime import date, datetime
from typing import List, Optional, Union, cast

from fastapi import Depends, HTTPException, Query, Request
from fastapi.routing import APIRouter

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.os.auth import get_auth_token_from_request, get_authentication_dependency
from egpt.os.routers.metrics.schemas import DayAggregatedMetrics, MetricsResponse, UsageBreakdown, UsageMetricsResponse
from egpt.os.schema import (
    BadRequestResponse,
    InternalServerErrorResponse,
    NotFoundResponse,
    UnauthenticatedResponse,
    ValidationErrorResponse,
)
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import current_tenant_id, require_tenant_id
from egpt.os.utils import get_db, to_utc_datetime
from egpt.remote.base import RemoteDb

logger = logging.getLogger(__name__)


def rows_to_usage_breakdowns(rows: List[dict]) -> List[UsageBreakdown]:
    """Convert raw DB rows to UsageBreakdown models."""
    return [
        UsageBreakdown(
            tenant_id=row.get("tenant_id"),
            agent_id=row.get("agent_id"),
            model_name=row.get("model_name"),
            input_tokens=int(row.get("input_tokens", 0)),
            output_tokens=int(row.get("output_tokens", 0)),
            total_tokens=int(row.get("total_tokens", 0)),
            trace_count=int(row.get("trace_count", 0)),
            span_count=int(row.get("span_count", 0)),
        )
        for row in rows
    ]


def get_metrics_router(
    dbs: dict[str, list[Union[BaseDb, AsyncBaseDb, RemoteDb]]],
    settings: EgptServingSettings = EgptServingSettings(),
    **kwargs,
) -> APIRouter:
    """Create metrics router with comprehensive OpenAPI documentation for system metrics and analytics endpoints."""
    router = APIRouter(
        dependencies=[Depends(get_authentication_dependency(settings))],
        tags=["Metrics"],
        responses={
            400: {"description": "Bad Request", "model": BadRequestResponse},
            401: {"description": "Unauthorized", "model": UnauthenticatedResponse},
            404: {"description": "Not Found", "model": NotFoundResponse},
            422: {"description": "Validation Error", "model": ValidationErrorResponse},
            500: {"description": "Internal Server Error", "model": InternalServerErrorResponse},
        },
    )
    return attach_routes(router=router, dbs=dbs)


def attach_routes(router: APIRouter, dbs: dict[str, list[Union[BaseDb, AsyncBaseDb, RemoteDb]]]) -> APIRouter:
    @router.get(
        "/metrics",
        response_model=MetricsResponse,
        status_code=200,
        operation_id="get_metrics",
        summary="Get EgptOS Metrics",
        description=(
            "Retrieve EgptOS metrics and analytics data for a specified date range. "
            "If no date range is specified, returns all available metrics."
        ),
        responses={
            200: {
                "description": "Metrics retrieved successfully",
                "content": {
                    "application/json": {
                        "example": {
                            "metrics": [
                                {
                                    "id": "7bf39658-a00a-484c-8a28-67fd8a9ddb2a",
                                    "agent_runs_count": 5,
                                    "agent_sessions_count": 5,
                                    "team_runs_count": 0,
                                    "team_sessions_count": 0,
                                    "workflow_runs_count": 0,
                                    "workflow_sessions_count": 0,
                                    "users_count": 1,
                                    "token_metrics": {
                                        "input_tokens": 448,
                                        "output_tokens": 148,
                                        "total_tokens": 596,
                                        "audio_tokens": 0,
                                        "input_audio_tokens": 0,
                                        "output_audio_tokens": 0,
                                        "cached_tokens": 0,
                                        "cache_write_tokens": 0,
                                        "reasoning_tokens": 0,
                                    },
                                    "model_metrics": [{"model_id": "gpt-4o", "model_provider": "OpenAI", "count": 5}],
                                    "date": "2025-07-31T00:00:00Z",
                                    "created_at": "2025-07-31T12:38:52Z",
                                    "updated_at": "2025-07-31T12:49:01Z",
                                }
                            ]
                        }
                    }
                },
            },
            400: {"description": "Invalid date range parameters", "model": BadRequestResponse},
            500: {"description": "Failed to retrieve metrics", "model": InternalServerErrorResponse},
        },
    )
    async def get_metrics(
        request: Request,
        starting_date: Optional[date] = Query(
            default=None, description="Starting date for metrics range (YYYY-MM-DD format)"
        ),
        ending_date: Optional[date] = Query(
            default=None, description="Ending date for metrics range (YYYY-MM-DD format)"
        ),
        db_id: Optional[str] = Query(default=None, description="Database ID to query metrics from"),
        table: Optional[str] = Query(default=None, description="The database table to use"),
    ) -> MetricsResponse:
        try:
            db = await get_db(dbs, db_id, table)

            if isinstance(db, RemoteDb):
                auth_token = get_auth_token_from_request(request)
                headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
                return await db.get_metrics(
                    starting_date=starting_date, ending_date=ending_date, db_id=db_id, table=table, headers=headers
                )

            if isinstance(db, AsyncBaseDb):
                db = cast(AsyncBaseDb, db)
                metrics, latest_updated_at = await db.get_metrics(starting_date=starting_date, ending_date=ending_date)
            else:
                metrics, latest_updated_at = db.get_metrics(starting_date=starting_date, ending_date=ending_date)

            return MetricsResponse(
                metrics=[DayAggregatedMetrics.from_dict(metric) for metric in metrics],
                updated_at=to_utc_datetime(latest_updated_at),
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting metrics: {str(e)}")

    @router.post(
        "/metrics/refresh",
        response_model=List[DayAggregatedMetrics],
        status_code=200,
        operation_id="refresh_metrics",
        summary="Refresh Metrics",
        description=(
            "Manually trigger recalculation of system metrics from raw data. "
            "This operation analyzes system activity logs and regenerates aggregated metrics. "
            "Useful for ensuring metrics are up-to-date or after system maintenance."
        ),
        responses={
            200: {
                "description": "Metrics refreshed successfully",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "id": "e77c9531-818b-47a5-99cd-59fed61e5403",
                                "agent_runs_count": 2,
                                "agent_sessions_count": 2,
                                "team_runs_count": 0,
                                "team_sessions_count": 0,
                                "workflow_runs_count": 0,
                                "workflow_sessions_count": 0,
                                "users_count": 1,
                                "token_metrics": {
                                    "input_tokens": 256,
                                    "output_tokens": 441,
                                    "total_tokens": 697,
                                    "audio_total_tokens": 0,
                                    "audio_input_tokens": 0,
                                    "audio_output_tokens": 0,
                                    "cache_read_tokens": 0,
                                    "cache_write_tokens": 0,
                                    "reasoning_tokens": 0,
                                },
                                "model_metrics": [{"model_id": "gpt-4o", "model_provider": "OpenAI", "count": 2}],
                                "date": "2025-08-12T00:00:00Z",
                                "created_at": "2025-08-12T08:01:47Z",
                                "updated_at": "2025-08-12T08:01:47Z",
                            }
                        ]
                    }
                },
            },
            500: {"description": "Failed to refresh metrics", "model": InternalServerErrorResponse},
        },
    )
    async def calculate_metrics(
        request: Request,
        db_id: Optional[str] = Query(default=None, description="Database ID to use for metrics calculation"),
        table: Optional[str] = Query(default=None, description="Table to use for metrics calculation"),
    ) -> List[DayAggregatedMetrics]:
        try:
            db = await get_db(dbs, db_id, table)

            if isinstance(db, RemoteDb):
                auth_token = get_auth_token_from_request(request)
                headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
                return await db.refresh_metrics(db_id=db_id, table=table, headers=headers)

            if isinstance(db, AsyncBaseDb):
                db = cast(AsyncBaseDb, db)
                result = await db.calculate_metrics()
            else:
                result = db.calculate_metrics()
            if result is None:
                return []

            return [DayAggregatedMetrics.from_dict(metric) for metric in result]

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error refreshing metrics: {str(e)}")

    # ------------------------------------------------------------------
    # EGPT-4.5: Per-tenant/per-agent usage metrics from traces + spans
    # ------------------------------------------------------------------

    @router.get(
        "/metrics/usage",
        response_model=UsageMetricsResponse,
        dependencies=[Depends(require_tenant_id)],
        status_code=200,
        operation_id="get_usage_metrics",
        summary="Get per-tenant/per-agent token usage",
        description=(
            "Aggregate token usage (input, output, total) from traces and spans, "
            "broken down by tenant, agent, and model. Scoped to the caller's tenant."
        ),
    )
    async def get_usage_metrics(
        request: Request,
        start_date: Optional[datetime] = Query(default=None, description="Start of query window (ISO 8601)"),
        end_date: Optional[datetime] = Query(default=None, description="End of query window (ISO 8601)"),
        agent_id: Optional[str] = Query(default=None, description="Filter by agent ID"),
        db_id: Optional[str] = Query(default=None, description="Database ID"),
        table: Optional[str] = Query(default=None, description="Database table"),
    ) -> UsageMetricsResponse:
        try:
            # Validate date range
            if start_date is not None and end_date is not None and start_date > end_date:
                raise HTTPException(status_code=400, detail="start_date must be <= end_date")

            # Default to last 30 days when no dates supplied
            if start_date is None and end_date is None:
                from datetime import timedelta
                from datetime import timezone as tz

                end_date = datetime.now(tz.utc)
                start_date = end_date - timedelta(days=30)

            tenant_id = current_tenant_id(request)

            db = await get_db(dbs, db_id, table)

            if isinstance(db, RemoteDb):
                raise HTTPException(status_code=501, detail="Usage metrics not supported on remote backends")

            try:
                if isinstance(db, AsyncBaseDb):
                    rows = await db.get_usage_metrics(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        start_date=start_date,
                        end_date=end_date,
                    )
                else:
                    rows = db.get_usage_metrics(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        start_date=start_date,
                        end_date=end_date,
                    )
            except NotImplementedError:
                raise HTTPException(status_code=501, detail="Usage metrics not supported by this backend")

            usage = rows_to_usage_breakdowns(rows)

            return UsageMetricsResponse(
                usage=usage,
                start_date=start_date,
                end_date=end_date,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting usage metrics: {e}")
            raise HTTPException(status_code=500, detail="Error getting usage metrics")

    return router
