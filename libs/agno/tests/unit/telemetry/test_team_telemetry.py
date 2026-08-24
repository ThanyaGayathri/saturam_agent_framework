import weakref
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from egpt.agent.agent import Agent
from egpt.run.base import RunContext
from egpt.team._telemetry import get_telemetry_data
from egpt.team.team import Team
from tests.unit.providers.test_escalation import FakeModel


def test_team_telemetry_default_off():
    """Telemetry is off by default; no API POST on run."""
    agent = Agent()
    team = Team(members=[agent])
    assert team.telemetry is False

    with patch("egpt.api.team.create_team_run") as mock_create:
        agent.model = MagicMock()
        team.model = MagicMock()
        team.run("This is a test run")
        mock_create.assert_not_called()


def test_create_team_run_no_endpoint_no_post():
    """Team telemetry POST is skipped when no endpoint is configured."""
    with (
        patch("egpt.api.team.is_telemetry_endpoint_configured", return_value=False),
        patch("egpt.api.team.api.Client") as mock_client,
    ):
        from egpt.api.schemas.team import TeamRunCreate
        from egpt.api.team import create_team_run

        create_team_run(TeamRunCreate(session_id="s1", run_id="r1", data={}))
        mock_client.assert_not_called()


def test_team_telemetry_opt_in():
    """When telemetry=True, logging is called during sync team run."""
    agent = Agent()
    team = Team(members=[agent], telemetry=True)
    assert team.telemetry is True

    with patch("egpt.team._telemetry.log_team_telemetry") as mock_log:
        agent.model = MagicMock()
        team.model = MagicMock()
        run_context = RunContext(run_id="r-1", session_id="s-1")
        team.run("This is a test run", run_context=run_context)

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None
        assert call_args.kwargs["run_context"] is run_context, (
            "telemetry must log the run's own context, not a stand-in or a stale one"
        )


@pytest.mark.asyncio
async def test_team_telemetry_async_opt_in():
    """When telemetry=True, logging is called during async team run."""
    agent = Agent()
    team = Team(members=[agent], telemetry=True)
    assert team.telemetry is True

    with patch("egpt.team._telemetry.alog_team_telemetry") as mock_alog:
        mock_model = AsyncMock()
        mock_model.get_instructions_for_model = MagicMock(return_value=None)
        mock_model.get_system_message_for_model = MagicMock(return_value=None)
        agent.model = mock_model
        team.model = mock_model
        run_context = RunContext(run_id="r-1", session_id="s-1")
        await team.arun("This is a test run", run_context=run_context)

        mock_alog.assert_called_once()
        call_args = mock_alog.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None
        assert call_args.kwargs["run_context"] is run_context, (
            "telemetry must log the run's own context, not a stand-in or a stale one"
        )


def test_telemetry_data_reports_the_configured_model_with_no_run_context():
    """No run_context (or no escalation): telemetry reports what the team is configured with."""
    team = Team(members=[], model=FakeModel(id="primary-id", name="primary", provider="primary-provider"))

    data = get_telemetry_data(team)

    assert data["model_id"] == "primary-id"
    assert data["model_provider"] == "primary-provider"


def test_telemetry_data_reports_the_served_model_when_escalated():
    """aicoe-tracker#182: an escalated run must attribute usage to the model that served it.

    `run_context.resolved_model`, owned by this team, is what `apply_run_model` stamps mid-run
    when a provider escalates. Telemetry must read it through `run_model` rather than
    `team.model`, which stays the caller's original configuration for the object's lifetime.
    """
    team = Team(members=[], model=FakeModel(id="primary-id", name="primary", provider="primary-provider"))
    escalated = FakeModel(id="frontier-id", name="frontier", provider="frontier-provider")
    run_context = RunContext(run_id="r-1", session_id="s-1")
    run_context.resolved_model = escalated
    run_context.resolved_model_owner = weakref.ref(team)

    data = get_telemetry_data(team, run_context=run_context)

    assert data["model_id"] == "frontier-id"
    assert data["model_provider"] == "frontier-provider"
    # The team's own configuration is untouched -- only the run-scoped context carries the escalation.
    assert team.model.id == "primary-id"
