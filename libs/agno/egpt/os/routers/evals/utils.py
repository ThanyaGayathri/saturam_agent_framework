from typing import Any, Optional, Tuple, Union

from fastapi import HTTPException

from egpt.agent import Agent, RemoteAgent
from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.eval.accuracy import AccuracyEval
from egpt.eval.agent_as_judge import AgentAsJudgeEval
from egpt.eval.performance import PerformanceEval
from egpt.eval.reliability import ReliabilityEval
from egpt.models.base import Model
from egpt.os.routers.evals.schemas import EvalRunInput, EvalSchema
from egpt.team import RemoteTeam, Team


def _served_model(run_response: Any, component: Any) -> Tuple[Optional[str], Optional[str]]:
    """The model that actually served an eval's run, for labelling the result (aicoe-tracker#185).

    Reads what the run recorded rather than what the component was configured with.
    ``_stamp_resolved_model`` re-stamps ``RunOutput.model``/``model_provider`` with the resolved
    model, so this reports the frontier model on an escalated run where ``component.model`` still
    names the primary. Eval output is what gets compared across tiers -- an escalation experiment
    whose results are all labelled with the primary's id cannot answer the question it was run to
    answer.

    Falls back to the component's configured model when the run recorded nothing. That is
    narrower than it may look: ``run_response.model``/``model_provider`` are stamped
    unconditionally from the configured model at run start (``agent/_run.py:1937``), then
    re-stamped only if a provider escalates -- so the fallback fires when there is no
    ``run_response`` at all (a run that never completed, or a caller passing ``None``) or when
    ``component.model`` was itself ``None``. In the ordinary non-escalated case the stamp already
    equals the configured model, so both branches agree.

    ``getattr`` throughout: this helper is handed whatever the route has, and a duck-typed
    component carrying a ``.model`` without ``.id``/``.provider`` should degrade to ``None``
    rather than raise while labelling a result (PR #137 review).
    """
    model_id = getattr(run_response, "model", None)
    model_provider = getattr(run_response, "model_provider", None)
    # Truthiness, not `is not None`: `RunOutput.model` defaults to `""`, so an unstamped run and
    # an empty stamp are the same state and both must fall through. No in-tree `Model` produces
    # an empty `id` or `provider` (`__post_init__` derives a non-empty provider from `name`), so
    # this cannot discard a real served value (PR #137 review).
    if model_id and model_provider:
        return model_id, model_provider

    configured = getattr(component, "model", None)
    return (
        model_id or getattr(configured, "id", None),
        model_provider or getattr(configured, "provider", None),
    )


async def run_accuracy_eval(
    eval_run_input: EvalRunInput,
    db: Union[BaseDb, AsyncBaseDb],
    agent: Optional[Agent] = None,
    team: Optional[Team] = None,
    default_model: Optional[Model] = None,
) -> EvalSchema:
    """Run an Accuracy evaluation for the given agent or team"""
    if not eval_run_input.expected_output:
        raise HTTPException(status_code=400, detail="expected_output is required for accuracy evaluation")

    accuracy_eval = AccuracyEval(
        db=db,
        agent=agent,
        team=team,
        input=eval_run_input.input,
        expected_output=eval_run_input.expected_output,
        additional_guidelines=eval_run_input.additional_guidelines,
        additional_context=eval_run_input.additional_context,
        num_iterations=eval_run_input.num_iterations or 1,
        name=eval_run_input.name,
        model=default_model,
    )

    result = await accuracy_eval.arun(print_results=False, print_summary=False)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to run accuracy evaluation")

    eval_run = EvalSchema.from_accuracy_eval(accuracy_eval=accuracy_eval, result=result)

    # Restore original model after eval
    if default_model is not None:
        if agent is not None:
            agent.model = default_model
        elif team is not None:
            team.model = default_model

    return eval_run


async def run_agent_as_judge_eval(
    eval_run_input: EvalRunInput,
    db: Union[BaseDb, AsyncBaseDb],
    agent: Optional[Union[Agent, RemoteAgent]] = None,
    team: Optional[Union[Team, RemoteTeam]] = None,
    default_model: Optional[Model] = None,
) -> EvalSchema:
    """Run an AgentAsJudge evaluation for the given agent or team"""
    if not eval_run_input.criteria:
        raise HTTPException(status_code=400, detail="criteria is required for agent-as-judge evaluation")

    # Run agent/team to get output
    if agent:
        agent_response = await agent.arun(eval_run_input.input, stream=False)  # type: ignore[misc]
        output = str(agent_response.content) if agent_response.content else ""
        agent_id = agent.id
        team_id = None
    elif team:
        team_response = await team.arun(eval_run_input.input, stream=False)  # type: ignore[misc]
        output = str(team_response.content) if team_response.content else ""
        agent_id = None
        team_id = team.id
    else:
        raise HTTPException(status_code=400, detail="Either agent_id or team_id must be provided")

    agent_as_judge_eval = AgentAsJudgeEval(
        db=db,
        criteria=eval_run_input.criteria,
        scoring_strategy=eval_run_input.scoring_strategy or "binary",
        threshold=eval_run_input.threshold or 7,
        additional_guidelines=eval_run_input.additional_guidelines,
        name=eval_run_input.name,
        model=default_model,
    )

    result = await agent_as_judge_eval.arun(
        input=eval_run_input.input, output=output, print_results=False, print_summary=False
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to run agent as judge evaluation")

    # Use evaluator's model
    eval_model_id = agent_as_judge_eval.model.id if agent_as_judge_eval.model is not None else None
    eval_model_provider = agent_as_judge_eval.model.provider if agent_as_judge_eval.model is not None else None

    eval_run = EvalSchema.from_agent_as_judge_eval(
        agent_as_judge_eval=agent_as_judge_eval,
        result=result,
        agent_id=agent_id,
        team_id=team_id,
        model_id=eval_model_id,
        model_provider=eval_model_provider,
    )

    # Restore original model after eval
    if default_model is not None:
        if agent is not None and isinstance(agent, Agent):
            agent.model = default_model
        elif team is not None and isinstance(team, Team):
            team.model = default_model

    return eval_run


async def run_performance_eval(
    eval_run_input: EvalRunInput,
    db: Union[BaseDb, AsyncBaseDb],
    agent: Optional[Agent] = None,
    team: Optional[Team] = None,
    default_model: Optional[Model] = None,
) -> EvalSchema:
    """Run a performance evaluation for the given agent or team"""
    # `PerformanceEval` discards what `func` returns, and it writes `self.model_id` into its own
    # DB record *inside* `arun()` -- so the served model has to be stamped onto the eval object
    # from within the run itself, not after it returns (aicoe-tracker#185). The closure captures
    # each response and updates the labels in place; the last run wins, which is the same
    # component and therefore the same answer for every iteration.
    served_model_id: Optional[str] = None
    served_model_provider: Optional[str] = None

    def _record(response: Any, component: Any) -> None:
        nonlocal served_model_id, served_model_provider
        served_model_id, served_model_provider = _served_model(response, component)
        performance_eval.model_id = served_model_id
        performance_eval.model_provider = served_model_provider

    if agent:

        async def run_component():  # type: ignore
            response = await agent.arun(eval_run_input.input, stream=False)  # type: ignore[misc]
            _record(response, agent)
            return response

    elif team:

        async def run_component():  # type: ignore
            response = await team.arun(eval_run_input.input, stream=False)  # type: ignore[misc]
            _record(response, team)
            return response

    performance_eval = PerformanceEval(
        db=db,
        name=eval_run_input.name,
        func=run_component,
        num_iterations=eval_run_input.num_iterations or 10,
        warmup_runs=eval_run_input.warmup_runs,
        agent_id=agent.id if agent else None,
        team_id=team.id if team else None,
        # Set below, once the runs that stamp the served model have happened.
        model_id=None,
        model_provider=None,
    )

    result = await performance_eval.arun(print_results=False, print_summary=False)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to run performance evaluation")

    # Populated by `_record` during the runs above; falls back to the configured model when the
    # eval never invoked `func` (warmup-only or zero iterations).
    model_id, model_provider = served_model_id, served_model_provider
    if model_id is None and model_provider is None:
        model_id, model_provider = _served_model(None, agent or team)

    eval_run = EvalSchema.from_performance_eval(
        performance_eval=performance_eval,
        result=result,
        agent_id=agent.id if agent else None,
        team_id=team.id if team else None,
        model_id=model_id,
        model_provider=model_provider,
    )

    # Restore original model after eval
    if default_model is not None:
        if agent is not None:
            agent.model = default_model
        elif team is not None:
            team.model = default_model

    return eval_run


async def run_reliability_eval(
    eval_run_input: EvalRunInput,
    db: Union[BaseDb, AsyncBaseDb],
    agent: Optional[Agent] = None,
    team: Optional[Team] = None,
    default_model: Optional[Model] = None,
) -> EvalSchema:
    """Run a reliability evaluation for the given agent or team"""
    if eval_run_input.expected_tool_calls is None:
        raise HTTPException(status_code=400, detail="expected_tool_calls is required for reliability evaluations")

    if agent:
        agent_response = await agent.arun(eval_run_input.input, stream=False)  # type: ignore[misc]
        reliability_eval = ReliabilityEval(
            db=db,
            name=eval_run_input.name,
            agent_response=agent_response,
            expected_tool_calls=eval_run_input.expected_tool_calls,
            allow_additional_tool_calls=eval_run_input.allow_additional_tool_calls,
            expected_tool_call_arguments=eval_run_input.expected_tool_call_arguments,
        )
        model_id, model_provider = _served_model(agent_response, agent)

    elif team:
        team_response = await team.arun(eval_run_input.input, stream=False)  # type: ignore[misc]
        reliability_eval = ReliabilityEval(
            db=db,
            name=eval_run_input.name,
            team_response=team_response,
            expected_tool_calls=eval_run_input.expected_tool_calls,
            allow_additional_tool_calls=eval_run_input.allow_additional_tool_calls,
            expected_tool_call_arguments=eval_run_input.expected_tool_call_arguments,
        )
        model_id, model_provider = _served_model(team_response, team)

    result = await reliability_eval.arun(print_results=False)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to run reliability evaluation")

    eval_run = EvalSchema.from_reliability_eval(
        reliability_eval=reliability_eval,
        result=result,
        agent_id=agent.id if agent else None,
        team_id=team.id if team else None,
        model_id=model_id,
        model_provider=model_provider,
    )

    # Restore original model after eval
    if default_model is not None:
        if agent is not None:
            agent.model = default_model
        elif team is not None:
            team.model = default_model

    return eval_run
