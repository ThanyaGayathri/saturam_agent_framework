"""Eval results must be labelled with the model that served the run (aicoe-tracker#185).

`egpt/os/routers/evals/utils.py` read `agent.model.id`/`team.model.id` to label an eval's DB
record. Since aicoe-tracker#166 stopped mutating `component.model`, that reports the model the
caller *configured*, never the one that actually served -- so every result of an escalation
experiment was labelled with the primary's id, which is precisely the comparison such an
experiment exists to make.

These drive the real router helpers against a provider that always escalates, and assert on the
`EvalSchema` that would be persisted.
"""

from typing import Any, Optional

import pytest

from egpt.agent.agent import Agent
from egpt.os.routers.evals.utils import _served_model
from egpt.providers import set_default_model_provider
from egpt.metrics import ProviderMode
from tests.unit.providers.test_dual_mode_e2e import RecordingModel, _RecordingProvider


@pytest.fixture(autouse=True)
def _restore_provider():
    yield
    set_default_model_provider(None)


class _Response:
    """The two fields `_stamp_resolved_model` writes onto a `RunOutput`."""

    def __init__(self, model: Optional[str], model_provider: Optional[str]) -> None:
        self.model = model
        self.model_provider = model_provider


class _Component:
    def __init__(self, model: Any) -> None:
        self.model = model


class TestServedModelResolution:
    """The helper both eval paths share."""

    def test_it_reports_what_the_run_recorded_not_what_was_configured(self):
        """The bug: an escalated run labelled with the primary's id."""
        configured = RecordingModel("primary-cheap")
        response = _Response("frontier-expensive", "anthropic")

        assert _served_model(response, _Component(configured)) == ("frontier-expensive", "anthropic")

    def test_it_falls_back_to_the_configured_model_when_nothing_was_stamped(self):
        """No provider configured leaves the stamp empty; that answer was correct before and stays."""
        configured = RecordingModel("primary-cheap")
        configured.provider = "openai"

        assert _served_model(_Response(None, None), _Component(configured)) == ("primary-cheap", "openai")

    def test_a_missing_response_falls_back_rather_than_raising(self):
        """A failed run may never reach the stamp -- labelling must degrade, not explode."""
        configured = RecordingModel("primary-cheap")
        configured.provider = "openai"

        assert _served_model(None, _Component(configured)) == ("primary-cheap", "openai")

    def test_a_component_without_a_model_yields_nulls(self):
        assert _served_model(None, _Component(None)) == (None, None)


class TestALiveRunLabelsTheServedModel:
    """End-to-end through a real `Agent.arun()` with an escalating provider."""

    @pytest.mark.asyncio
    async def test_the_response_carries_the_escalated_model_the_label_reads(self):
        configured = RecordingModel("primary-cheap")
        served = RecordingModel("frontier-expensive")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        agent = Agent(name="sut", model=configured, telemetry=False)
        response = await agent.arun("hello")

        assert agent.model.id == "primary-cheap", "the component itself must not be mutated"
        assert response.model == "frontier-expensive", "precondition: the run stamped the served model"

        model_id, model_provider = _served_model(response, agent)
        assert model_id == "frontier-expensive", "the eval label must name the model that served"
        assert model_provider == response.model_provider


class TestThePerformanceRouteLabelsTheServedModel:
    """The closure is the novel mechanism in this change, so it gets driven directly.

    `PerformanceEval` throws away what `func` returns *and* writes `self.model_id` into its own
    DB record inside `arun()`, so the served model has to be stamped onto the eval object from
    within the run. Testing `_served_model` alone would leave that wiring uncovered -- a refactor
    could break it with every other test still green (PR #137 review).
    """

    @pytest.mark.asyncio
    async def test_the_returned_schema_names_the_escalated_model(self, monkeypatch):
        from egpt.os.routers.evals.schemas import EvalRunInput
        from egpt.os.routers.evals.utils import run_performance_eval

        configured = RecordingModel("primary-cheap")
        served = RecordingModel("frontier-expensive")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        agent = Agent(name="sut", model=configured, telemetry=False)
        eval_input = EvalRunInput(eval_type="performance", input="hello", num_iterations=2, warmup_runs=0, name="perf")

        # The eval writes its record through the db; a stub keeps this a unit test.
        class _Db:
            def __getattr__(self, _name):
                return lambda *a, **k: None

        # Grab the PerformanceEval the route builds, so the assertions below can check the object
        # whose `model_id` is what actually reaches the database.
        captured_eval: list = []
        import egpt.os.routers.evals.utils as _utils

        real_cls = _utils.PerformanceEval

        def _capturing(*args, **kwargs):
            instance = real_cls(*args, **kwargs)
            captured_eval.append(instance)
            return instance

        monkeypatch.setattr(_utils, "PerformanceEval", _capturing)

        schema = await run_performance_eval(eval_input, db=_Db(), agent=agent)

        assert agent.model.id == "primary-cheap", "the component itself must not be mutated"
        assert schema.model_id == "frontier-expensive", (
            "the eval record must name the model that served, not the configured one"
        )
        assert schema.model_provider == served.provider

        # `PerformanceEval` writes `self.model_id` into its own DB row *inside* `arun()`, so the
        # returned schema agreeing is not sufficient -- the stamp has to have landed on the eval
        # object before that write. Asserting only on the schema lets a stamp that runs after
        # `arun()` returns pass while the persisted row holds NULL, which is exactly the mistake
        # the first draft of this change made (PR #137 review).
        assert captured_eval, "the test must have observed the PerformanceEval instance"
        assert captured_eval[-1].model_id == "frontier-expensive", (
            "the eval object was stamped too late; its DB row would persist the wrong model"
        )
        assert captured_eval[-1].model_provider == served.provider

    @pytest.mark.asyncio
    async def test_it_falls_back_when_the_eval_never_invoked_the_component(self, monkeypatch):
        """`num_iterations` is `ge=1`, but the closure may still never fire if the harness
        changes; the label must then be the configured model rather than `None`."""
        from egpt.os.routers.evals.utils import _served_model

        configured = RecordingModel("primary-cheap")
        agent = Agent(name="sut", model=configured, telemetry=False)

        assert _served_model(None, agent) == ("primary-cheap", configured.provider)
