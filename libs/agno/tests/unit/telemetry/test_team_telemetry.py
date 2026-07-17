from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from egpt.agent.agent import Agent
from egpt.team.team import Team


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


def test_team_telemetry_opt_in():
    """When telemetry=True, logging is called during sync team run."""
    agent = Agent()
    team = Team(members=[agent], telemetry=True)
    assert team.telemetry is True

    with patch("egpt.team._telemetry.log_team_telemetry") as mock_log:
        agent.model = MagicMock()
        team.model = MagicMock()
        team.run("This is a test run")

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None


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
        await team.arun("This is a test run")

        mock_alog.assert_called_once()
        call_args = mock_alog.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None
