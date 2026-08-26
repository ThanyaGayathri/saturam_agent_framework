"""EvalSchema.from_accuracy_eval() must name the same model as the DB record for the same eval
(nandha-saturam review on aicoe-tracker#183).

`run_accuracy_eval` (os/routers/evals/utils.py) calls `accuracy_eval.arun()`, which writes the
DB row using `run_response.model` -- the model that actually served the run. The route then
builds the HTTP response via `EvalSchema.from_accuracy_eval()`, which used to re-derive
`model_id`/`model_provider` from `accuracy_eval.agent.model`/`accuracy_eval.team.model` -- the
static configuration -- independently of what was just persisted. For an escalated run, the two
disagreed.
"""

from unittest.mock import MagicMock

from egpt.eval.accuracy import AccuracyEval, AccuracyResult
from egpt.os.routers.evals.schemas import EvalSchema
from tests.unit.providers.test_escalation import FakeModel


def test_uses_the_served_model_when_a_run_happened():
    """The bug: an escalated run's API response named the primary, not the model that served it."""
    accuracy_eval = AccuracyEval(
        input="x",
        expected_output="y",
        agent=MagicMock(id="a-1", model=FakeModel(id="configured-id", provider="configured-provider")),
    )
    accuracy_eval.served_model_id = "served-id"
    accuracy_eval.served_model_provider = "served-provider"

    schema = EvalSchema.from_accuracy_eval(accuracy_eval=accuracy_eval, result=AccuracyResult())

    assert schema.model_id == "served-id"
    assert schema.model_provider == "served-provider"


def test_falls_back_to_the_configured_model_when_no_run_happened():
    """run_with_output()/arun_with_output() never set served_model_id/served_model_provider --
    the configured model is the only correct answer on that path."""
    accuracy_eval = AccuracyEval(
        input="x",
        expected_output="y",
        agent=MagicMock(id="a-1", model=FakeModel(id="configured-id", provider="configured-provider")),
    )

    schema = EvalSchema.from_accuracy_eval(accuracy_eval=accuracy_eval, result=AccuracyResult())

    assert schema.model_id == "configured-id"
    assert schema.model_provider == "configured-provider"


def test_falls_back_to_the_configured_team_model_when_no_run_happened():
    accuracy_eval = AccuracyEval(
        input="x",
        expected_output="y",
        team=MagicMock(id="t-1", model=FakeModel(id="team-configured-id", provider="team-configured-provider")),
    )

    schema = EvalSchema.from_accuracy_eval(accuracy_eval=accuracy_eval, result=AccuracyResult())

    assert schema.model_id == "team-configured-id"
    assert schema.model_provider == "team-configured-provider"


def test_a_team_without_a_model_does_not_raise():
    """Regression: the old code read accuracy_eval.team.model.id unconditionally, with no
    None-guard (unlike the model_provider derivation next to it) -- a team eval whose model was
    None crashed with AttributeError building the response, after a successful eval run."""
    accuracy_eval = AccuracyEval(
        input="x",
        expected_output="y",
        team=MagicMock(id="t-1", model=None),
    )

    schema = EvalSchema.from_accuracy_eval(accuracy_eval=accuracy_eval, result=AccuracyResult())

    assert schema.model_id is None
    assert schema.model_provider is None
