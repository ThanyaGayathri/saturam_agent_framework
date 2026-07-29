from unittest.mock import MagicMock, patch

from egpt.agent.agent import Agent
from egpt.eval.accuracy import AccuracyEval
from egpt.eval.agent_as_judge import AgentAsJudgeEval
from egpt.eval.performance import PerformanceEval
from egpt.eval.reliability import ReliabilityEval


def _clear_telemetry_env(monkeypatch) -> None:
    """Clear CI/default env so constructor telemetry=True is not overridden."""
    monkeypatch.delenv("EGPT_TELEMETRY", raising=False)
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)


def test_accuracy_evals_telemetry_default_off():
    """Telemetry is off by default for accuracy evals."""
    agent = Agent()
    accuracy_eval = AccuracyEval(agent=agent, input="What is the capital of France?", expected_output="Paris")
    assert accuracy_eval.telemetry is False

    with patch("egpt.api.evals.create_eval_run_telemetry") as mock_create:
        agent.model = MagicMock()
        accuracy_eval.run(print_summary=False, print_results=False)
        mock_create.assert_not_called()


def test_accuracy_evals_telemetry_env_opt_in(monkeypatch):
    """EGPT_TELEMETRY=true enables eval telemetry via env override."""
    monkeypatch.setenv("EGPT_TELEMETRY", "true")
    agent = Agent()
    accuracy_eval = AccuracyEval(agent=agent, input="What is the capital of France?", expected_output="Paris")
    assert accuracy_eval.telemetry is True


def test_accuracy_evals_telemetry_opt_in(monkeypatch):
    """When telemetry=True, logging is called during sync accuracy eval run."""
    _clear_telemetry_env(monkeypatch)
    agent = Agent()
    accuracy_eval = AccuracyEval(
        agent=agent, input="What is the capital of France?", expected_output="Paris", telemetry=True
    )
    assert accuracy_eval.telemetry is True

    with patch("egpt.api.evals.create_eval_run_telemetry") as mock_create:
        agent.model = MagicMock()
        accuracy_eval.run(print_summary=False, print_results=False)

        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]["eval_run"]
        assert call_args.run_id == accuracy_eval.eval_id
        assert call_args.eval_type.value == "accuracy"


def test_create_eval_run_no_endpoint_no_post():
    """Eval telemetry POST is skipped when no endpoint is configured."""
    with (
        patch("egpt.api.evals.is_telemetry_endpoint_configured", return_value=False),
        patch("egpt.api.evals.api.Client") as mock_client,
    ):
        from egpt.api.evals import create_eval_run_telemetry
        from egpt.api.schemas.evals import EvalRunCreate
        from egpt.db.schemas.evals import EvalType

        create_eval_run_telemetry(
            EvalRunCreate(run_id="r1", eval_type=EvalType.ACCURACY, data={}),
        )
        mock_client.assert_not_called()


def test_performance_evals_telemetry_opt_in(monkeypatch):
    """When telemetry=True, telemetry works for performance evaluations."""
    _clear_telemetry_env(monkeypatch)

    def sample_func():
        return "test result"

    # This test only checks that telemetry fires. PerformanceEval defaults to 10 warm-up runs plus
    # 50 measured iterations for each of runtime and memory, which cost ~63s here for no added
    # coverage; one iteration exercises the same telemetry path.
    performance_eval = PerformanceEval(func=sample_func, telemetry=True, warmup_runs=0, num_iterations=1)
    assert performance_eval.telemetry is True

    with patch("egpt.api.evals.create_eval_run_telemetry") as mock_create:
        performance_eval.run()

        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]["eval_run"]
        assert call_args.run_id == performance_eval.eval_id
        assert call_args.eval_type.value == "performance"


def test_reliability_evals_telemetry_opt_in(monkeypatch):
    """When telemetry=True, telemetry works for reliability evaluations."""
    _clear_telemetry_env(monkeypatch)
    from egpt.models.message import Message
    from egpt.run.agent import RunOutput

    mock_message = Message(
        role="assistant",
        content="Test response",
        tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}],
    )
    mock_response = RunOutput(content="Test response", messages=[mock_message])
    reliability_eval = ReliabilityEval(agent_response=mock_response, expected_tool_calls=["test_tool"], telemetry=True)
    assert reliability_eval.telemetry is True

    with patch("egpt.api.evals.create_eval_run_telemetry") as mock_create:
        reliability_eval.run(print_results=False)

        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]["eval_run"]
        assert call_args.run_id == reliability_eval.eval_id
        assert call_args.eval_type.value == "reliability"


def test_agent_as_judge_numeric_telemetry_opt_in(monkeypatch):
    """When telemetry=True, telemetry works for agent-as-judge evaluations (numeric mode)."""
    _clear_telemetry_env(monkeypatch)
    eval = AgentAsJudgeEval(
        criteria="Response must be helpful",
        scoring_strategy="numeric",
        threshold=7,
        telemetry=True,
    )
    assert eval.telemetry is True

    evaluator = eval.get_evaluator_agent()
    evaluator.model = MagicMock()

    with patch("egpt.api.evals.create_eval_run_telemetry") as mock_create:
        eval.run(input="What is Python?", output="Python is a programming language.", print_results=False)

        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]["eval_run"]
        assert call_args.eval_type.value == "agent_as_judge"


def test_agent_as_judge_binary_telemetry_opt_in(monkeypatch):
    """When telemetry=True, telemetry works for agent-as-judge evaluations (binary mode)."""
    _clear_telemetry_env(monkeypatch)
    eval = AgentAsJudgeEval(
        criteria="Response must not contain personal info",
        scoring_strategy="binary",
        telemetry=True,
    )
    assert eval.telemetry is True

    evaluator = eval.get_evaluator_agent()
    evaluator.model = MagicMock()

    with patch("egpt.api.evals.create_eval_run_telemetry") as mock_create:
        eval.run(input="Tell me about privacy", output="Privacy is important.", print_results=False)

        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]["eval_run"]
        assert call_args.eval_type.value == "agent_as_judge"
