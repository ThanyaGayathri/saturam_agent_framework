from unittest.mock import patch

import pytest

from egpt.workflow.step import Step
from egpt.workflow.workflow import Workflow


def dummy_step(step_input):
    """Simple dummy step for testing"""
    from egpt.workflow.types import StepOutput

    return StepOutput(content="Test step executed")


def test_workflow_telemetry_default_off():
    """Telemetry is off by default; _log_workflow_telemetry is not called on run."""
    workflow = Workflow(steps=[Step(executor=dummy_step)])
    assert workflow.telemetry is False

    with patch.object(workflow, "_log_workflow_telemetry") as mock_log:
        workflow.run("This is a test run")
        mock_log.assert_not_called()


def test_create_workflow_run_no_endpoint_no_post():
    """Workflow telemetry POST is skipped when no endpoint is configured."""
    with (
        patch("egpt.api.workflow.is_telemetry_endpoint_configured", return_value=False),
        patch("egpt.api.workflow.api.Client") as mock_client,
    ):
        from egpt.api.schemas.workflows import WorkflowRunCreate
        from egpt.api.workflow import create_workflow_run

        create_workflow_run(WorkflowRunCreate(session_id="s1", run_id="r1", data={}))
        mock_client.assert_not_called()


def test_workflow_telemetry_opt_in():
    """When telemetry=True, logging is called during sync workflow run."""
    workflow = Workflow(steps=[Step(executor=dummy_step)], telemetry=True)
    assert workflow.telemetry is True

    with patch.object(workflow, "_log_workflow_telemetry") as mock_log:
        workflow.run("This is a test run")

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None


@pytest.mark.asyncio
async def test_workflow_telemetry_async_opt_in():
    """When telemetry=True, logging is called during async workflow run."""
    workflow = Workflow(steps=[Step(executor=dummy_step)], telemetry=True)
    assert workflow.telemetry is True

    with patch.object(workflow, "_alog_workflow_telemetry") as mock_alog:
        await workflow.arun("This is a test run")

        mock_alog.assert_called_once()
        call_args = mock_alog.call_args
        assert "session_id" in call_args.kwargs
        assert call_args.kwargs["session_id"] is not None
        assert "run_id" in call_args.kwargs
        assert call_args.kwargs["run_id"] is not None
