from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from egpt.os.utils import to_utc_datetime


class DayAggregatedMetrics(BaseModel):
    """Aggregated metrics for a given day"""

    id: str = Field(..., description="Unique identifier for the metrics record")

    agent_runs_count: int = Field(..., description="Total number of agent runs", ge=0)
    agent_sessions_count: int = Field(..., description="Total number of agent sessions", ge=0)
    team_runs_count: int = Field(..., description="Total number of team runs", ge=0)
    team_sessions_count: int = Field(..., description="Total number of team sessions", ge=0)
    workflow_runs_count: int = Field(..., description="Total number of workflow runs", ge=0)
    workflow_sessions_count: int = Field(..., description="Total number of workflow sessions", ge=0)
    users_count: int = Field(..., description="Total number of unique users", ge=0)
    token_metrics: Dict[str, Any] = Field(..., description="Token usage metrics (input, output, cached, etc.)")
    model_metrics: List[Dict[str, Any]] = Field(..., description="Metrics grouped by model (model_id, provider, count)")

    date: datetime = Field(..., description="Date for which these metrics are aggregated")
    created_at: datetime = Field(..., description="Timestamp when metrics were created")
    updated_at: datetime = Field(..., description="Timestamp when metrics were last updated")

    @classmethod
    def from_dict(cls, metrics_dict: Dict[str, Any]) -> "DayAggregatedMetrics":
        created_at = to_utc_datetime(metrics_dict.get("created_at")) or datetime.now(timezone.utc)
        updated_at = to_utc_datetime(metrics_dict.get("updated_at", created_at)) or created_at
        return cls(
            agent_runs_count=metrics_dict.get("agent_runs_count", 0),
            agent_sessions_count=metrics_dict.get("agent_sessions_count", 0),
            date=metrics_dict.get("date", datetime.now(timezone.utc)),
            id=metrics_dict.get("id", ""),
            model_metrics=metrics_dict.get("model_metrics", {}),
            team_runs_count=metrics_dict.get("team_runs_count", 0),
            team_sessions_count=metrics_dict.get("team_sessions_count", 0),
            token_metrics=metrics_dict.get("token_metrics", {}),
            created_at=created_at,
            updated_at=updated_at,
            users_count=metrics_dict.get("users_count", 0),
            workflow_runs_count=metrics_dict.get("workflow_runs_count", 0),
            workflow_sessions_count=metrics_dict.get("workflow_sessions_count", 0),
        )


class MetricsResponse(BaseModel):
    metrics: List[DayAggregatedMetrics] = Field(..., description="List of daily aggregated metrics")
    updated_at: Optional[datetime] = Field(None, description="Timestamp of the most recent metrics update")


# ---------------------------------------------------------------------------
# Usage metrics (EGPT-4.5) — per-tenant/per-agent cost and token breakdown
# ---------------------------------------------------------------------------


class UsageBreakdown(BaseModel):
    """Token usage breakdown for a single tenant+agent+model combination."""

    tenant_id: Optional[str] = Field(None, description="Tenant ID (null for unscoped traces)")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    model_name: Optional[str] = Field(None, description="LLM model name (e.g. gpt-4o)")
    input_tokens: int = Field(0, description="Total input/prompt tokens", ge=0)
    output_tokens: int = Field(0, description="Total output/completion tokens", ge=0)
    total_tokens: int = Field(0, description="Sum of input + output tokens", ge=0)
    trace_count: int = Field(0, description="Number of traces contributing to this row", ge=0)
    span_count: int = Field(0, description="Number of LLM spans contributing to this row", ge=0)


class UsageMetricsResponse(BaseModel):
    """Per-tenant/per-agent token usage aggregated from traces+spans."""

    usage: List[UsageBreakdown] = Field(default_factory=list, description="Usage breakdown rows")
    start_date: Optional[datetime] = Field(None, description="Start of the query window")
    end_date: Optional[datetime] = Field(None, description="End of the query window")
