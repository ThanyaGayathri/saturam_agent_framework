"""From a compiled workflow to a running one (K16).

``workflow_from_compiled`` turns the config the orchestration compiler produced -- a list of
step dicts naming executors by ``executor_ref`` and model steps by ``agent_id`` -- into a
framework ``Workflow`` against the live executor registry. Executors are resolved by name,
so the workflow runs exactly the functions ``build_registry()`` registered and nothing else.
A model step becomes a ``Step`` that owns one ``Agent`` built for the step's role
(``extraction`` today: instructions from the role template, a schema generated from the
extraction template) and runs it once per document in the batch -- the agent is built once
per workflow, never per document, and the only model call in the sequence is this one.

``run_parameters`` turns the slot values the interface stored -- a configured source's id, a
form's camelCase keys -- into what the executors read from ``additional_data``. Credentials
never arrive from the interface: a source carries a credential reference and the platform
resolves it (``credentials.py``) from the environment locally, from a vault in the cloud.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

from egpt.workflow import Workflow
from egpt.workflow.step import Step
from egpt.workflow.types import OnError, StepInput, StepOutput

from egpt_retail.documents.extraction import (
    aextract_with_agent,
    extraction_instructions,
    extraction_output_model,
)
from egpt_retail.documents.templates import template_for
from egpt_retail.executors.batch import batch_from, empty_batch, slot
from egpt_retail.executors.documents import aextract_documents, extract_documents
from egpt_retail.usecases.credentials import CredentialUnavailable, resolve_credentials

__all__ = [
    "UnresolvableStep",
    "workflow_from_compiled",
    "run_parameters",
    "make_extraction_step",
    "make_explanation_step",
    "build_explanation_agent",
]

log = logging.getLogger(__name__)


class UnresolvableStep(ValueError):
    """A compiled step names an executor or a role this deployment cannot build."""


AgentFactory = Callable[[str, str], Any]
"""``(role, agent_id) -> Agent`` -- how the runtime gets a routed model for a role."""


def make_extraction_step(
    agent: Any, *, template_id: Any = "purchase_order", name: str = "Extract"
) -> Step:
    """The Extract stage as a step: the batch in, the batch with ``extracted`` filled out.

    ``additional_data["extractor"]`` overrides the agent (the rehearsal's stand-in over the
    sample set); otherwise the agent is run once per document through
    ``extract_with_agent`` and every output is validated against the template.
    """

    async def extract(step_input: StepInput) -> StepOutput:
        batch = batch_from(step_input, step_names=("Screen", "screen_documents"))
        if batch is None:
            return StepOutput(
                content={**empty_batch(), "notes": "no batch to extract"},
                success=True,
                stop=True,
            )
        template = slot(step_input, "extraction template", template_id) or template_id
        override = (step_input.additional_data or {}).get("extractor")
        if override is not None:
            # The rehearsal's stand-in is a plain function; keep it off the loop anyway.
            batch = await asyncio.to_thread(
                extract_documents, batch, override, template_id=template
            )
        elif agent is None:

            async def refuse(document: Mapping[str, Any]) -> Mapping[str, Any]:
                raise RuntimeError(
                    "no extraction model is configured for this deployment"
                )

            batch = await aextract_documents(batch, refuse, template_id=template)
        else:

            async def with_agent(document: Mapping[str, Any]) -> Mapping[str, Any]:
                return await aextract_with_agent(agent, document)

            batch = await aextract_documents(batch, with_agent, template_id=template)
        nothing_left = not batch["documents"] and not batch["exceptions"]
        return StepOutput(content=batch, success=True, stop=nothing_left)

    return Step(name=name, executor=extract, on_error=OnError.fail)


def make_explanation_step(agent: Any, *, name: str = "Explain") -> Step:
    """The Explain stage as a step (K23): the comparison in, the comparison out with the
    model's ``explanation`` beside it. ``additional_data["explainer"]`` overrides the agent
    (a rehearsal's stand-in); no agent and no override is an honest empty explanation --
    the report then carries the numbers only -- never a refusal of the run.
    """
    from egpt_retail.executors.analytics import comparison_of
    from egpt_retail.executors.diagnostics import diagnosis_of
    from egpt_retail.executors.predict import risk_of
    from egpt_retail.executors.recommend import outcomes_of, recommendations_of
    from egpt_retail.skills.report_explanation import (
        render_at_risk_message,
        render_diagnosis_message,
        render_message,
    )

    async def explain(step_input: StepInput) -> StepOutput:
        comparison = comparison_of(step_input)
        # The diagnostic kind (K25): the decomposition is what the model narrates, and
        # it travels with the comparison so the report renders both.
        diagnosis = diagnosis_of(step_input)
        # The at-risk kind (K26): the scoring and the plays are what the model briefs.
        risk = risk_of(step_input)
        recommendation_result = recommendations_of(step_input)
        if comparison is None and diagnosis is None and risk is None:
            return StepOutput(
                content={"refused": "no comparison to explain"},
                success=False,
                stop=True,
            )
        options = step_input.additional_data or {}
        override = options.get("explainer")
        explanation: Optional[Dict[str, Any]] = None
        notes = ""
        if override is not None:
            explanation = dict(
                await asyncio.to_thread(
                    override,
                    risk if risk is not None else (diagnosis or comparison),
                )
            )
        elif agent is None:
            notes = "no explanation model is configured; the report carries the numbers only"
        else:
            question = str(options.get("question") or "")
            if risk is not None:
                message = render_at_risk_message(
                    risk,
                    recommendation_result,
                    comparison=comparison,
                    question=question,
                    outcomes=outcomes_of(step_input),
                )
            elif diagnosis is not None:
                message = render_diagnosis_message(diagnosis, question=question)
            else:
                message = render_message(comparison or {}, question=question)
            response = await agent.arun(message)
            content = getattr(response, "content", None)
            if content is None:
                notes = "the model returned no explanation"
            elif hasattr(content, "model_dump"):
                explanation = content.model_dump()
            elif isinstance(content, Mapping):
                explanation = dict(content)
            else:
                notes = "the model returned an explanation of the wrong shape"
        result: Dict[str, Any] = {
            "comparison_result": comparison,
            "explanation": explanation,
            "notes": notes,
        }
        if diagnosis is not None:
            result["diagnosis"] = diagnosis
        if risk is not None:
            result["risk"] = risk
        if recommendation_result is not None:
            result["recommendation_result"] = recommendation_result
        outcomes = outcomes_of(step_input)
        if outcomes is not None:
            result["outcomes"] = outcomes
        return StepOutput(content=result, success=True)

    return Step(name=name, executor=explain, on_error=OnError.fail)


def build_explanation_agent(
    model: Any,
    *,
    agent_id: str,
    db: Any = None,
    diagnosis: bool = False,
    at_risk: bool = False,
) -> Any:
    """The explanation agent for one use case: the report_explanation skill's prompt and
    output schema -- the diagnosis_explanation prompt for the diagnostic kind (K25), the
    at_risk_explanation prompt for the at-risk kind (K26), same schema -- on the small
    tier's model."""
    from egpt.agent import Agent

    from egpt_retail.skills.report_explanation import (
        ReportExplanation,
        render_at_risk_prompt,
        render_diagnosis_prompt,
        render_prompt,
    )

    instructions = (
        render_at_risk_prompt()
        if at_risk
        else render_diagnosis_prompt()
        if diagnosis
        else render_prompt()
    )
    return Agent(
        id=agent_id,
        name=agent_id,
        model=model,
        instructions=instructions,
        output_schema=ReportExplanation,
        db=db,
    )


def _step_name(step: Mapping[str, Any]) -> str:
    return str(
        step.get("name") or step.get("executor_ref") or step.get("agent_id") or "step"
    )


def workflow_from_compiled(
    compiled: Mapping[str, Any],
    *,
    executors: Mapping[str, Callable[..., Any]],
    agent_factory: Optional[AgentFactory] = None,
    db: Any = None,
    workflow_id: Optional[str] = None,
) -> Workflow:
    """A runnable ``Workflow`` from a ``CompiledWorkflow`` serialised as a dict.

    ``executors`` is name -> function, from ``build_registry()``. ``agent_factory`` builds
    the routed agent for a model step's role; ``None`` builds a workflow whose model steps
    refuse at run time unless an ``extractor`` override is supplied, which is what the
    offline tests use.
    """
    config = compiled.get("config") or {}
    steps: List[Any] = []
    model_steps = {m.get("agent_id"): m for m in compiled.get("model_steps") or []}
    for raw in config.get("steps") or []:
        name = _step_name(raw)
        agent_id = raw.get("agent_id")
        if agent_id:
            role = str((model_steps.get(agent_id) or {}).get("role") or "")
            if role not in {"extraction", "explanation"}:
                raise UnresolvableStep(
                    f"step {name!r}: no runtime for the {role or 'unnamed'} role yet"
                )
            agent = (
                agent_factory(role, str(agent_id))
                if agent_factory is not None
                else None
            )
            if role == "explanation":
                steps.append(make_explanation_step(agent, name=name))
            else:
                steps.append(make_extraction_step(agent, name=name))
            continue
        ref = str(raw.get("executor_ref") or "")
        executor = executors.get(ref)
        if executor is None:
            raise UnresolvableStep(f"step {name!r}: executor {ref!r} is not registered")
        steps.append(Step(name=name, executor=executor, on_error=OnError.fail))
    if not steps:
        raise UnresolvableStep("the compiled workflow has no steps")
    identifier = workflow_id or f"usecase__{compiled.get('use_case_id', 'use_case')}"
    return Workflow(
        id=identifier,
        name=str(compiled.get("use_case_id") or identifier),
        description=f"Compiled {compiled.get('shape')}/{compiled.get('template') or 'shape'} workflow.",
        db=db,
        steps=steps,
    )


def build_extraction_agent(
    model: Any, *, agent_id: str, template_id: Any = "purchase_order", db: Any = None
) -> Any:
    """The extraction agent for one use case: role instructions plus the template's schema."""
    from egpt.agent import Agent

    template = template_for(template_id)
    return Agent(
        id=agent_id,
        name=agent_id,
        model=model,
        instructions=extraction_instructions(template),
        output_schema=extraction_output_model(template),
        db=db,
    )


# --- parameters ----------------------------------------------------------------------------

_ROLE_KEYS = {
    "customer": "customer",
    "contact": "contact",
    "shipto": "ship_to",
    "ship_to": "ship_to",
    "product": "product",
    "orderhistory": "order_history",
    "order_history": "order_history",
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def run_parameters(
    parameters: Mapping[str, Any],
    *,
    sources: Mapping[str, Mapping[str, Any]] = {},
    environ: Optional[Mapping[str, str]] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """Executor-ready ``additional_data`` from the compiled parameters.

    ``sources`` is the configured-source catalogue the interface saved (id -> record with
    ``sourceType`` and ``credentialRef``); a slot holding a source id is expanded to the
    connection the executor reads, with its credentials resolved. Every other slot passes
    through under its own name; ``master data`` is normalised to the loader's role names.
    ``strict=False`` (a rehearsal) leaves a source whose credentials do not resolve as the
    bare record: the rehearsal injects its messages and holds its writes, so the executor
    that would have needed the credential never asks for it.
    """

    def resolve(source: Mapping[str, Any], kind: str) -> Dict[str, Any]:
        try:
            return resolve_credentials(source, kind=kind, environ=environ)
        except CredentialUnavailable:
            if strict:
                raise
            return {
                k: v
                for k, v in source.items()
                if k not in {"credentialRef", "credential_ref"}
            }

    out: Dict[str, Any] = {}
    for name, value in parameters.items():
        out[name] = value

    def source_for(value: Any) -> Optional[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            return value
        if isinstance(value, str):
            record = sources.get(value)
            if record is not None:
                return record
            for candidate in sources.values():
                if (
                    str(candidate.get("name") or candidate.get("label") or "").lower()
                    == value.lower()
                ):
                    return candidate
        return None

    mailbox = source_for(parameters.get("source mailbox"))
    if mailbox is not None:
        out["source mailbox"] = resolve(mailbox, "mailbox")
    elif parameters.get("source mailbox"):
        out["source mailbox"] = resolve({"credentialRef": "env:gmail"}, "mailbox")

    target = source_for(parameters.get("write target"))
    if target is not None:
        out["write target"] = resolve(target, "salesforce")
    elif parameters.get("write target"):
        out["write target"] = {
            "type": "salesforce",
            **resolve({"credentialRef": "env:salesforce"}, "salesforce"),
        }

    master = parameters.get("master data")
    if (
        isinstance(master, Mapping)
        and not any(k in master for k in ("customer", "product", "ship_to"))
        or isinstance(master, str)
    ):
        out["master data"] = {"source": "salesforce"}
    elif isinstance(master, Mapping) and not any(
        isinstance(v, list) for v in master.values()
    ):
        roles = sorted(
            {
                _ROLE_KEYS.get(_slug(k).replace("_", ""), _slug(k))
                for k, v in master.items()
                if v
            }
        )
        out["master data"] = {"source": "salesforce", "roles": roles or None}

    recipient = parameters.get("recipient")
    if isinstance(recipient, Mapping):
        out["recipient"] = recipient.get("who") or recipient.get("email") or ""
    return out
