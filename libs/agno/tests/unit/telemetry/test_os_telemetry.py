from unittest.mock import patch

from egpt.agent.agent import Agent
from egpt.os import EgptOS


def test_accuracy_evals_telemetry():
    """Test that telemetry logging is called when initializing an EgptOS instance."""
    agent = Agent()

    # Mock the API call that gets made when telemetry is enabled
    with patch("egpt.api.os.log_os_telemetry") as mock_create:
        os = EgptOS(id="test", agents=[agent])

        # Assert telemetry is active by default
        assert os.telemetry

        # Verify API was called with correct parameters
        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]["launch"]
        assert call_args.os_id == "test"
