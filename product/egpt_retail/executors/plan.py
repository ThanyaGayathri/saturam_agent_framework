"""``derive_plan`` (R9): the compiled workflow as the plan the builder renders. A view, never authored.

"Plan derived from the compiled workflow, not written alongside it" (delivery plan, R9), and
the API contract's ``Plan`` says the same: steps four and five of the builder render this,
and a plan authored in parallel with the workflow is the failure the build plan names. So the
only input is a stored workflow component's config (``Workflow.to_dict()``), walked
recursively -- ``steps``, a ``Condition``'s ``else_steps``, a ``Router``'s ``choices``, a
``Loop``/``Parallel``/``Steps`` block's ``steps`` -- and every ``Step`` becomes one
``PlanStep``: ``executor_ref`` is an executor (name, description, inputs, outputs and
``permits_or_blocks`` from ``executors/metadata.py``), ``agent_id`` is an agent (``model_tier``
from R8's routing table).

An ``executor_ref`` the registry cannot resolve, or an ``agent_id`` the routing table does not
name, is a refusal (``UnknownPlanRef``). ``POST /components`` does not validate either
(``registry/schema.py`` checks step *types* only), and ``Step.from_dict`` would silently yield
``None`` for the executor at run time -- so this view is where a stored workflow referencing
an executor nobody registered becomes visible, by name, before it is activated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Collection, Dict, Iterator, List, Mapping, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.executors.metadata import EXECUTOR_METADATA
from egpt_retail.routing.table import NO_MODEL_TIER, ROUTING_TABLE, model_tier_for

__all__ = [
    "UnknownPlanRef",
    "derive_plan",
    "plan_from_config",
    "plan_refs",
    "walk_steps",
]

#: The keys under which a serialised step holds nested steps, by step type.
_NESTED_KEYS = ("steps", "else_steps", "choices")


class UnknownPlanRef(LookupError):
    """A stored workflow names an executor or agent this deployment does not have."""


def walk_steps(config: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Every serialised ``Step`` in ``config``, depth first, in execution order.

    Recurses through every nested-steps key a step type may carry. A container (Condition,
    Router, Loop, Parallel, Steps) is not itself a plan step -- it decides *which* steps run,
    and the plan lists the steps that can run.
    """
    for step in config.get("steps") or ():
        if not isinstance(step, Mapping):
            continue
        yield from _walk_step(step)


def _walk_step(step: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    step_type = step.get("type", "Step")
    if step_type == "Step":
        yield step
        return
    for key in _NESTED_KEYS:
        for nested in step.get(key) or ():
            if isinstance(nested, Mapping):
                yield from _walk_step(nested)


def plan_refs(config: Mapping[str, Any]) -> List[str]:
    """Every ``executor_ref`` and ``agent_id`` in the config, recursively, in order."""
    refs: List[str] = []
    for step in walk_steps(config):
        ref = step.get("executor_ref") or step.get("agent_id")
        if ref:
            refs.append(str(ref))
    return refs


def plan_from_config(
    config: Mapping[str, Any],
    *,
    use_case_id: str,
    version: int,
    workflow_component_id: Optional[str],
    executor_names: Collection[str],
    agent_ids: Collection[str] = (),
    compiled_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """The contract's ``Plan`` for one stored workflow config. Pure.

    ``executor_names`` is what ``build_registry()`` resolves; ``agent_ids`` what ``build_agents``
    registers plus whatever the caller built per request (the spec extractor). An agent step
    must also have a row in R8's routing table, or its tier is unknowable.
    """
    steps: List[Dict[str, Any]] = []
    for index, step in enumerate(walk_steps(config), start=1):
        executor_ref = step.get("executor_ref")
        agent_id = step.get("agent_id")
        if executor_ref:
            if executor_ref not in executor_names:
                raise UnknownPlanRef(
                    f"step {index} ({step.get('name')!r}) references executor {executor_ref!r}, "
                    "which build_registry() does not resolve; register it (contracts/EXECUTOR_REGISTRY.md) "
                    "before this workflow is activated"
                )
            meta = EXECUTOR_METADATA.get(executor_ref)
            steps.append(
                {
                    "index": index,
                    "kind": "executor",
                    "ref": executor_ref,
                    "name": str(
                        step.get("name") or (meta.name if meta else executor_ref)
                    ),
                    "description": str(
                        step.get("description") or (meta.description if meta else "")
                    ),
                    "inputs": list(meta.inputs) if meta else [],
                    "outputs": list(meta.outputs) if meta else [],
                    "permits_or_blocks": bool(meta.permits_or_blocks)
                    if meta
                    else False,
                    "model_tier": None,
                    "estimated_seconds": meta.estimated_seconds if meta else None,
                }
            )
        elif agent_id:
            if agent_id not in agent_ids and agent_id not in ROUTING_TABLE:
                raise UnknownPlanRef(
                    f"step {index} ({step.get('name')!r}) references agent {agent_id!r}, which is neither "
                    "registered nor in R8's routing table"
                )
            tier = model_tier_for(agent_id)
            if tier == NO_MODEL_TIER:
                raise UnknownPlanRef(
                    f"step {index} ({step.get('name')!r}) references agent {agent_id!r}, which has no row in "
                    "R8's routing table, so its model tier is unknown"
                )
            steps.append(
                {
                    "index": index,
                    "kind": "agent",
                    "ref": agent_id,
                    "name": str(step.get("name") or agent_id),
                    "description": str(
                        step.get("description")
                        or "One model step. Explains or extracts; never decides anything."
                    ),
                    "inputs": [],
                    "outputs": [],
                    # By the alignment rules an agent never permits or blocks.
                    "permits_or_blocks": False,
                    "model_tier": tier,
                    "estimated_seconds": None,
                }
            )
        else:
            raise UnknownPlanRef(
                f"step {index} ({step.get('name')!r}) carries neither executor_ref nor agent_id; "
                "an inline callable cannot be stored, so it cannot be planned"
            )
    return {
        "use_case_id": use_case_id,
        "version": version,
        "workflow_component_id": workflow_component_id,
        "steps": steps,
        "compiled_at": (compiled_at or datetime.now(timezone.utc))
        .isoformat()
        .replace("+00:00", "Z"),
    }


async def derive_plan(step_input: StepInput) -> StepOutput:
    """Load the stored workflow component named by ``additional_data["workflow_component_id"]``
    from the control plane and derive its plan. A config this deployment cannot resolve is a
    refusal (``success=False``) naming the reference -- the one thing this step blocks on, and
    it blocks a *view*, not a run."""
    from egpt_retail.app.components import build_agents, build_registry
    from egpt_retail.app.db import build_control_plane_db
    from egpt_retail.app.settings import RetailSettings

    options: Mapping[str, Any] = step_input.additional_data or {}
    component_id = options.get("workflow_component_id")
    if not component_id:
        return StepOutput(
            content={"refused": "no workflow_component_id in additional_data"},
            success=False,
            error="no workflow_component_id",
        )
    from egpt.registry.hydrate import fetch_config_row

    settings = RetailSettings()
    db = build_control_plane_db(settings)
    tenant_id = (settings.retail_customer_slug or "").strip() or None
    version = options.get("version")
    # The same read the framework's own `get_workflow_by_id` starts with: the current version's
    # config row, scoped to the tenant. Hydration is deliberately not run -- a plan is a view
    # over the stored config, and hydrating would need every referenced agent and executor to
    # resolve, which is exactly what this view reports rather than requires.
    row = fetch_config_row(
        db,
        str(component_id),
        "workflow",
        version=int(version) if version else None,
        tenant_id=tenant_id,
    )
    if row is None or not isinstance(row.get("config"), Mapping):
        return StepOutput(
            content={
                "refused": f"no stored workflow component {component_id!r} (or no config on it)"
            },
            success=False,
            error="component not found",
        )
    config = row["config"]
    executor_names = {f.__name__ for f in build_registry().functions}
    agent_ids = {a.id for a in build_agents(settings, db) if a.id}
    try:
        plan = plan_from_config(
            config,
            use_case_id=str(options.get("use_case_id") or ""),
            version=int(version or row.get("version") or 1),
            workflow_component_id=str(component_id),
            executor_names=executor_names,
            agent_ids=agent_ids,
        )
    except UnknownPlanRef as exc:
        return StepOutput(content={"refused": str(exc)}, success=False, error=str(exc))
    return StepOutput(content=plan, success=True)
