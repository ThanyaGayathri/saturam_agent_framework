"""Capture-style telemetry egress tests (httpx Client construction + MockTransport)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from egpt.agent.agent import Agent
from egpt.api.api import api
from egpt.api.routes import ApiRoutes


def _serializable_model() -> MagicMock:
    model = MagicMock()
    model.provider = "smoke"
    model.name = "smoke-model"
    model.id = "smoke-1"
    return model


def test_default_agent_run_does_not_construct_httpx_client(monkeypatch):
    """AC1: default run must not build a real httpx.Client (no telemetry socket)."""
    monkeypatch.delenv("EGPT_TELEMETRY", raising=False)
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)

    with patch("egpt.api.api.HttpxClient") as mock_httpx:
        agent = Agent()
        assert agent.telemetry is False
        agent.model = _serializable_model()
        agent.run("capture smoke")
        mock_httpx.assert_not_called()


def test_enabled_without_url_uses_noop_client_not_httpx(monkeypatch):
    """EGPT_TELEMETRY=true without API_URL must not construct httpx.Client."""
    monkeypatch.setenv("EGPT_TELEMETRY", "true")
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)

    with patch("egpt.api.api.HttpxClient") as mock_httpx:
        agent = Agent()
        from egpt.agent import _init

        _init.set_telemetry(agent)
        assert agent.telemetry is True
        agent.model = _serializable_model()
        agent.run("capture smoke")
        mock_httpx.assert_not_called()


def test_enabled_with_url_posts_only_to_configured_endpoint(monkeypatch):
    """AC4: when enabled + EGPT_API_URL set, POST goes only to that base URL."""
    monkeypatch.setenv("EGPT_TELEMETRY", "true")
    monkeypatch.setenv("EGPT_API_URL", "https://telemetry.example.com")

    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("http2", None)
        return real_client(*args, **kwargs)

    with patch("egpt.api.api.HttpxClient", side_effect=client_factory):
        agent = Agent()
        from egpt.agent import _init

        _init.set_telemetry(agent)
        assert agent.telemetry is True
        agent.model = _serializable_model()
        agent.run("capture smoke")

    assert len(recorded) >= 1
    for req in recorded:
        assert req.url.host == "telemetry.example.com"
        assert req.url.path == ApiRoutes.RUN_CREATE
        assert req.method == "POST"


def test_api_client_choke_point_returns_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)

    with patch("egpt.api.api.HttpxClient") as mock_httpx:
        client = api.Client()
        mock_httpx.assert_not_called()
        # No-op post must not raise
        with client as c:
            assert c.post("/telemetry/runs", json={}) is None


def test_runtime_api_url_reaches_client(monkeypatch):
    """EGPT_API_URL set after import must make Client() use a real httpx client."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)

    with patch("egpt.api.api.HttpxClient") as mock_httpx:
        mock_httpx.return_value = MagicMock()
        api.Client()
        mock_httpx.assert_not_called()

        monkeypatch.setenv("EGPT_API_URL", "https://telemetry.example.com")
        api.Client()
        mock_httpx.assert_called_once()
        assert mock_httpx.call_args.kwargs["base_url"] == "https://telemetry.example.com"
