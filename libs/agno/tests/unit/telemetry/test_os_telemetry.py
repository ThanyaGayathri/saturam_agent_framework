from unittest.mock import patch

from egpt.agent.agent import Agent
from egpt.os import EgptOS


def test_os_telemetry_default_off():
    """Telemetry is off by default; OS launch telemetry is not sent."""
    agent = Agent()

    with patch("egpt.api.os.log_os_telemetry") as mock_create:
        os = EgptOS(id="test", agents=[agent])
        assert os.telemetry is False
        mock_create.assert_not_called()


def test_log_os_telemetry_no_endpoint_no_post():
    """OS telemetry POST is skipped when no endpoint is configured."""
    with (
        patch("egpt.api.os.is_telemetry_endpoint_configured", return_value=False),
        patch("egpt.api.os.api.Client") as mock_client,
    ):
        from egpt.api.os import log_os_telemetry
        from egpt.api.schemas.os import OSLaunch

        log_os_telemetry(launch=OSLaunch(os_id="test", data={}))
        mock_client.assert_not_called()


def test_os_telemetry_opt_in():
    """When telemetry=True, OS launch telemetry is sent."""
    agent = Agent()

    with patch("egpt.api.os.log_os_telemetry") as mock_create:
        os = EgptOS(id="test", agents=[agent], telemetry=True)
        assert os.telemetry is True
        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]["launch"]
        assert call_args.os_id == "test"


def test_os_telemetry_env_opt_in(monkeypatch):
    """EGPT_TELEMETRY=true enables EgptOS telemetry via env override."""
    monkeypatch.setenv("EGPT_TELEMETRY", "true")
    agent = Agent()

    with patch("egpt.api.os.log_os_telemetry") as mock_create:
        os = EgptOS(id="test", agents=[agent])
        assert os.telemetry is True
        mock_create.assert_called_once()
