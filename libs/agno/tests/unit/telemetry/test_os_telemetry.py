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
