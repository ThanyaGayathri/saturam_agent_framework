import weakref
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from egpt.agent._telemetry import get_telemetry_data
from egpt.agent.agent import Agent
from egpt.run.base import RunContext
from tests.unit.providers.test_escalation import FakeModel


def test_agent_telemetry_default_off():
    """Telemetry is off by default; no API POST on run."""
    agent = Agent()
    assert agent.telemetry is False

    with patch("egpt.api.agent.create_agent_run") as mock_create:
        agent.model = MagicMock()
        agent.run("This is a test run")
        mock_create.assert_not_called()


def test_agent_telemetry_opt_in():
    """When telemetry=True, logging is called during sync agent run."""
    agent = Agent(telemetry=True)
    assert agent.telemetry is True

    with patch("egpt.agent._telemetry.log_agent_telemetry") as mock_log:
        agent.model = MagicMock()
        run_context = RunContext(run_id="r-1", session_id="s-1")
        agent.run("This is a test run", run_context=run_context)

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
async def test_agent_telemetry_async_opt_in():
    """When telemetry=True, logging is called during async agent run."""
    agent = Agent(telemetry=True)
    assert agent.telemetry is True

    with patch("egpt.agent._telemetry.alog_agent_telemetry") as mock_alog:
        mock_model = AsyncMock()
        mock_model.get_instructions_for_model = MagicMock(return_value=None)
        mock_model.get_system_message_for_model = MagicMock(return_value=None)
        agent.model = mock_model
        run_context = RunContext(run_id="r-1", session_id="s-1")

        await agent.arun("This is a test run", run_context=run_context)

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
    """No run_context (or no escalation): telemetry reports what the agent is configured with."""
    agent = Agent(model=FakeModel(id="primary-id", name="primary", provider="primary-provider"))

    data = get_telemetry_data(agent)

    assert data["model_id"] == "primary-id"
    assert data["model_provider"] == "primary-provider"


def test_telemetry_data_reports_the_served_model_when_escalated():
    """aicoe-tracker#182: an escalated run must attribute usage to the model that served it.

    `run_context.resolved_model`, owned by this agent, is what `apply_run_model` stamps mid-run
    when a provider escalates. Telemetry must read it through `run_model` rather than
    `agent.model`, which stays the caller's original configuration for the object's lifetime.
    """
    agent = Agent(model=FakeModel(id="primary-id", name="primary", provider="primary-provider"))
    escalated = FakeModel(id="frontier-id", name="frontier", provider="frontier-provider")
    run_context = RunContext(run_id="r-1", session_id="s-1")
    run_context.resolved_model = escalated
    run_context.resolved_model_owner = weakref.ref(agent)

    data = get_telemetry_data(agent, run_context=run_context)

    assert data["model_id"] == "frontier-id"
    assert data["model_provider"] == "frontier-provider"
    # The agent's own configuration is untouched -- only the run-scoped context carries the escalation.
    assert agent.model.id == "primary-id"


def test_agent_telemetry_env_opt_in(monkeypatch):
    """EGPT_TELEMETRY=true enables telemetry via env override."""
    monkeypatch.setenv("EGPT_TELEMETRY", "true")
    agent = Agent()
    from egpt.agent import _init

    _init.set_telemetry(agent)
    assert agent.telemetry is True


def test_agent_telemetry_enabled_without_endpoint_no_http(monkeypatch):
    """EGPT_TELEMETRY=true with empty api_url must not open an HTTP client on run."""
    monkeypatch.setenv("EGPT_TELEMETRY", "true")
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)

    with (
        patch("egpt.api.agent.is_telemetry_endpoint_configured", return_value=False),
        patch("egpt.api.agent.api.Client") as mock_client,
    ):
        agent = Agent()
        from egpt.agent import _init

        _init.set_telemetry(agent)
        assert agent.telemetry is True

        agent.model = MagicMock()
        agent.run("This is a test run")
        mock_client.assert_not_called()


def test_create_agent_run_no_endpoint_no_post():
    """Enabled telemetry with empty api_url must not open an HTTP client."""
    with (
        patch("egpt.api.agent.is_telemetry_endpoint_configured", return_value=False),
        patch("egpt.api.agent.api.Client") as mock_client,
    ):
        from egpt.api.agent import create_agent_run
        from egpt.api.schemas.agent import AgentRunCreate

        create_agent_run(AgentRunCreate(session_id="s1", run_id="r1", data={}))
        mock_client.assert_not_called()


def test_create_agent_run_posts_when_endpoint_configured():
    """Configured API_URL allows the telemetry POST."""
    with (
        patch("egpt.api.agent.is_telemetry_endpoint_configured", return_value=True),
        patch("egpt.api.agent.api.Client") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from egpt.api.agent import create_agent_run
        from egpt.api.schemas.agent import AgentRunCreate

        create_agent_run(AgentRunCreate(session_id="s1", run_id="r1", data={"agent_id": "a1"}))
        mock_client.post.assert_called_once()
