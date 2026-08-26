"""Unit tests for AccuracyEval"""

from unittest.mock import AsyncMock, MagicMock

from egpt.eval.accuracy import AccuracyAgentResponse, AccuracyEval
from egpt.run.agent import RunOutput
from tests.unit.providers.test_escalation import FakeModel


def _mock_evaluator(eval_instance: AccuracyEval, score: int = 8):
    """Helper to mock the evaluator agent to return a numeric accuracy score."""
    evaluator = eval_instance.get_evaluator_agent()
    evaluator.model = MagicMock()
    mock_response = AccuracyAgentResponse(accuracy_score=score, accuracy_reason="Mocked evaluation response.")
    mock_output = RunOutput(content=mock_response)
    evaluator.run = MagicMock(return_value=mock_output)
    evaluator.arun = AsyncMock(return_value=mock_output)
    eval_instance.evaluator_agent = evaluator
    return evaluator


def _mock_evaluator_failure(eval_instance: AccuracyEval):
    """Helper to mock the evaluator agent so every evaluation fails (e.g. judge model unavailable)."""
    evaluator = eval_instance.get_evaluator_agent()
    evaluator.model = MagicMock()
    evaluator.run = MagicMock(side_effect=Exception("Evaluator model unavailable"))
    evaluator.arun = AsyncMock(side_effect=Exception("Evaluator model unavailable"))
    eval_instance.evaluator_agent = evaluator
    return evaluator


def test_basic_evaluation_computes_score():
    """A successful evaluation populates results and computes the score stats."""
    eval = AccuracyEval(
        input="What is 2 + 2?",
        expected_output="4",
    )
    _mock_evaluator(eval, score=8)

    result = eval.run_with_output(output="4", print_results=False, print_summary=False)

    assert result is not None
    assert len(result.results) == 1
    assert result.avg_score == 8
    assert result.min_score == 8
    assert result.max_score == 8


def test_all_iterations_fail_returns_none_stats():
    """Regression for #7672: when every iteration fails, the stat fields default to None
    instead of being left unset (which raised AttributeError)."""
    eval = AccuracyEval(
        input="What is 2 + 2?",
        expected_output="4",
        num_iterations=2,
    )
    _mock_evaluator_failure(eval)

    result = eval.run_with_output(output="4", print_results=False, print_summary=False)

    assert result is not None
    assert len(result.results) == 0
    assert result.avg_score is None
    assert result.mean_score is None
    assert result.min_score is None
    assert result.max_score is None
    assert result.std_dev_score is None


def test_print_summary_does_not_raise_when_all_iterations_fail():
    """print_summary() must not raise AttributeError on an empty result (the bug's crash site)."""
    eval = AccuracyEval(
        input="What is 2 + 2?",
        expected_output="4",
    )
    _mock_evaluator_failure(eval)

    result = eval.run_with_output(output="4", print_results=False, print_summary=False)
    # Should complete without raising AttributeError.
    result.print_summary()


async def test_async_all_iterations_fail_returns_none_stats():
    """Async regression for #7672 via arun_with_output."""
    eval = AccuracyEval(
        input="What is 2 + 2?",
        expected_output="4",
    )
    _mock_evaluator_failure(eval)

    result = await eval.arun_with_output(output="4", print_results=False, print_summary=False)

    assert result is not None
    assert len(result.results) == 0
    assert result.avg_score is None


class TestGetTelemetryData:
    """aicoe-tracker#183 follow-up: _get_telemetry_data() must not silently disagree with the
    DB record `run()` writes a few lines above it from the same `run_response`, and must
    support a team eval the same way it supports an agent eval.
    """

    def test_uses_the_passed_model_over_the_configured_one(self):
        """A run happened: the caller must pass the model that actually served it."""
        eval = AccuracyEval(
            input="x",
            expected_output="y",
            agent=MagicMock(id="a-1", model=FakeModel(id="configured-id", provider="configured-provider")),
        )

        data = eval._get_telemetry_data(model_id="served-id", model_provider="served-provider")

        assert data["model_id"] == "served-id"
        assert data["model_provider"] == "served-provider"

    def test_falls_back_to_the_configured_agent_model_with_no_override(self):
        """No run happened (e.g. run_with_output()): the configured model is the only correct read."""
        eval = AccuracyEval(
            input="x",
            expected_output="y",
            agent=MagicMock(id="a-1", model=FakeModel(id="configured-id", provider="configured-provider")),
        )

        data = eval._get_telemetry_data()

        assert data["model_id"] == "configured-id"
        assert data["model_provider"] == "configured-provider"

    def test_falls_back_to_the_configured_team_model_with_no_override(self):
        """Regression: the old fallback read only self.agent.model, so a team eval's telemetry
        got model_id=None unconditionally, even outside any escalation scenario."""
        eval = AccuracyEval(
            input="x",
            expected_output="y",
            team=MagicMock(id="t-1", model=FakeModel(id="team-configured-id", provider="team-configured-provider")),
        )

        data = eval._get_telemetry_data()

        assert data["model_id"] == "team-configured-id"
        assert data["model_provider"] == "team-configured-provider"

    def test_falls_back_per_field_not_only_when_both_are_missing(self):
        """Regression (nandha-saturam review): a run can stamp model_id while leaving
        model_provider unset. Requiring both to be None before falling back skipped the
        provider fallback entirely in that case, silently recording model_provider=None even
        though the configured model's provider was known."""
        eval = AccuracyEval(
            input="x",
            expected_output="y",
            agent=MagicMock(id="a-1", model=FakeModel(id="configured-id", provider="configured-provider")),
        )

        data = eval._get_telemetry_data(model_id="served-id", model_provider=None)

        assert data["model_id"] == "served-id", "the served id must still win"
        assert data["model_provider"] == "configured-provider", (
            "the missing provider must fall back independently, not be skipped because model_id was set"
        )


def test_run_attributes_telemetry_to_the_same_model_as_the_db_record(monkeypatch):
    """End-to-end: run()'s DB write and its telemetry payload must name the same served model,
    not the agent's static configuration -- the exact disagreement aicoe-tracker#183's review
    flagged. `agent.run()` returns a `run_response.model` that deliberately differs from
    `agent.model.id`, standing in for what an escalated run's resolution would stamp -- so the
    two payloads can only agree by actually reading `run_response.model`/`model_provider` in
    both places, not `agent.model` in one of them.
    """
    # CI sets EGPT_TELEMETRY=false; clear so the constructor's telemetry=True opt-in is honored.
    monkeypatch.delenv("EGPT_TELEMETRY", raising=False)
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)

    agent = MagicMock(id="a-1", model=FakeModel(id="configured-id", provider="configured-provider"))
    agent.run = MagicMock(return_value=RunOutput(content="4", model="served-id", model_provider="served-provider"))

    eval = AccuracyEval(input="What is 2 + 2?", expected_output="4", agent=agent, telemetry=True)
    _mock_evaluator(eval, score=8)

    captured_db = {}
    captured_telemetry = {}

    def _fake_log_eval_run(**kwargs):
        captured_db["model_id"] = kwargs["model_id"]
        captured_db["model_provider"] = kwargs["model_provider"]

    class _FakeDb:
        pass

    eval.db = _FakeDb()

    import egpt.eval.accuracy as accuracy_module

    original_log_eval_run = accuracy_module.log_eval_run
    accuracy_module.log_eval_run = _fake_log_eval_run
    try:

        def _fake_create_eval_run_telemetry(eval_run):
            captured_telemetry["model_id"] = eval_run.data["model_id"]
            captured_telemetry["model_provider"] = eval_run.data["model_provider"]

        import egpt.api.evals as api_evals_module

        original_create = api_evals_module.create_eval_run_telemetry
        api_evals_module.create_eval_run_telemetry = _fake_create_eval_run_telemetry
        try:
            eval.run(print_results=False, print_summary=False)
        finally:
            api_evals_module.create_eval_run_telemetry = original_create
    finally:
        accuracy_module.log_eval_run = original_log_eval_run

    assert captured_db["model_id"] == "served-id"
    assert captured_telemetry["model_id"] == captured_db["model_id"], (
        "telemetry must attribute the same model as the DB record from the same run"
    )
    assert captured_telemetry["model_provider"] == captured_db["model_provider"]


async def test_arun_attributes_telemetry_to_the_same_model_as_the_db_record(monkeypatch):
    """Async counterpart (nandha-saturam review): arun()'s telemetry call omitted `data=`
    entirely, so it never attributed anything, correctly or otherwise -- the sync fix above did
    not carry over to the async path even though this PR's whole purpose is fixing exactly this
    disagreement for accuracy evals.
    """
    monkeypatch.delenv("EGPT_TELEMETRY", raising=False)
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)

    agent = MagicMock(id="a-1", model=FakeModel(id="configured-id", provider="configured-provider"))
    agent.arun = AsyncMock(return_value=RunOutput(content="4", model="served-id", model_provider="served-provider"))

    eval = AccuracyEval(input="What is 2 + 2?", expected_output="4", agent=agent, telemetry=True)
    _mock_evaluator(eval, score=8)

    captured_db = {}
    captured_telemetry = {}

    async def _fake_async_log_eval(**kwargs):
        captured_db["model_id"] = kwargs["model_id"]
        captured_db["model_provider"] = kwargs["model_provider"]

    class _FakeDb:
        pass

    eval.db = _FakeDb()

    import egpt.eval.accuracy as accuracy_module

    original_async_log_eval = accuracy_module.async_log_eval
    accuracy_module.async_log_eval = _fake_async_log_eval
    try:

        async def _fake_async_create_eval_run_telemetry(eval_run):
            captured_telemetry["model_id"] = eval_run.data["model_id"]
            captured_telemetry["model_provider"] = eval_run.data["model_provider"]

        import egpt.api.evals as api_evals_module

        original_create = api_evals_module.async_create_eval_run_telemetry
        api_evals_module.async_create_eval_run_telemetry = _fake_async_create_eval_run_telemetry
        try:
            await eval.arun(print_results=False, print_summary=False)
        finally:
            api_evals_module.async_create_eval_run_telemetry = original_create
    finally:
        accuracy_module.async_log_eval = original_async_log_eval

    assert captured_db["model_id"] == "served-id"
    assert captured_telemetry.get("model_id") == captured_db["model_id"], (
        "async telemetry must attribute the same model as the DB record from the same run"
    )
    assert captured_telemetry.get("model_provider") == captured_db["model_provider"]
