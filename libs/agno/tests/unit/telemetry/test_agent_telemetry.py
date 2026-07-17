from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from egpt.agent.agent import Agent


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
        agent.run("This is a test run")

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None


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

        await agent.arun("This is a test run")

        mock_alog.assert_called_once()
        call_args = mock_alog.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None


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
