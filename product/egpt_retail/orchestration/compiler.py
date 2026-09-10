"""K15's deterministic half -- a frozen spec compiled into a workflow definition.

The build plan's claim is that generation is tractable because *"for a given autonomy level
the step sequence is the same every time. Only the parameters differ. A new use case is not a
new flow, it is one of four shapes with its slots filled."* :data:`SHAPE_STAGES` is that table
transcribed, and everything here is the consequence: take the shape the spec was written
against, walk its stages, resolve each to an executor the registry actually has, bind the
spec's values into the shape's slots, and refuse when a slot the shape requires is empty.

**The shape comes from the spec.** Each shape has its own form in the interface, so the shape
is chosen before the spec is filled rather than inferred afterwards from an autonomy level.
R9's ``executors.gaps.shape_for`` infers one from ``autonomy`` and ``level`` across three
shapes and cannot express an Alert at all; K15 does not call it. Autonomy remains a separate
axis and is recorded, not used to pick the sequence -- an Inform can be delivered unattended,
and a Recommend waits for its approver whatever the autonomy says.

**No model is consulted anywhere in this module.**

**A stage with no executor is reported, never emitted.** The package's whole guarantee is that
the agent *"composes what exists and cannot invent a deterministic step"*, and the same has to
hold of the compiler that runs before it -- otherwise a workflow naming a step nobody wrote
reaches activation and fails on its first scheduled run. What the compiler does instead is
subtract: :func:`capability_report` names, per shape, the stages this deployment could not
build. That report is the useful output today, because most of what Alert, Recommend and Act
need has no executor in this deployment yet.
"""

from __future__ import annotations

import asyncio
import copy
import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Collection, Dict, List, Mapping, Optional, Tuple

from egpt_retail.contracts.use_case import (
    SHAPE_PARAMETERS,
    TIER_FOR_ROLE,
    UseCaseShape,
)

__all__ = [
    "SHAPE_STAGES",
    "CompiledWorkflow",
    "ParameterOrigin",
    "Stage",
    "StageKind",
    "UnknownShape",
    "acompile_workflow",
    "capability_report",
    "compile_workflow",
    "diff_against",
    "normalise_config",
]


class UnknownShape(ValueError):
    """The spec named no shape, or named one that is not one of the four."""


class ParameterOrigin(str, Enum):
    """Where a bound parameter's value came from.

    **The template is the shape**, so there is nothing here for a template to have supplied.
    Two Alert use cases share the Alert template -- the same sequence, the same slots -- and
    differ only in what fills those slots. What varies between them is exactly this, and the
    question worth recording is where each value came from.

    The distinction the activation screen needs: a spec derived from a conversation and a
    value a person typed are not the same evidence. On an Act, whether the per-run cap was
    extracted by a model from what somebody said in chat, or set deliberately on the form, is
    the difference between a limit that was chosen and one that was inferred.
    """

    #: The author supplied it on the shape's form, or edited what the chat proposed.
    AUTHORED = "authored"
    #: Derived from the conversation by the spec extractor -- the KPIs, grain and window.
    SPEC = "spec"


class StageKind(str, Enum):
    """What a stage becomes in the compiled definition.

    ``GATE`` is not a step type of its own -- it is a step carrying
    ``human_review.requires_confirmation``, which is how the framework already expresses
    "a person decides here". ``BRANCH`` is a deterministic step that decides whether the
    steps after it run; the build plan's rule that *"every branch in those sequences is a
    deterministic step, never a model"* is why there is no model branch kind.
    """

    EXECUTOR = "executor"
    MODEL = "model"
    GATE = "gate"
    BRANCH = "branch"


@dataclass(frozen=True)
class Stage:
    """One stage of a shape's sequence, and what it needs to become a step."""

    name: str
    kind: StageKind
    #: For every kind but MODEL, the executor the registry must resolve.
    executor_ref: Optional[str] = None
    #: For MODEL stages, the role the step plays. Becomes part of the agent id and is what
    #: the step-prompt skill authors against.
    role: Optional[str] = None
    description: str = ""
    #: Set where this deployment's architecture already satisfies the stage elsewhere, naming
    #: the executor that subsumes it. The stage then contributes no step of its own and is
    #: not a missing capability -- but the substitution stays visible in
    #: :func:`capability_report` rather than being quietly dropped from the table.
    satisfied_by: Optional[str] = None
    satisfied_because: str = ""


#: What "compute metrics" means once there is a gold layer. R5 materialises every ready KPI's
#: formula into ``gold.kpi_value`` nightly, so by the time a use case runs the metric is
#: already computed and ``read_gold`` returns the value. The plan's separate compute stage
#: predates that decision. Recorded as a substitution rather than deleted from the table,
#: because a module claiming to implement section 07 verbatim should say where it deviates.
_COMPUTE_SATISFIED = (
    "R5 materialises every ready KPI into gold.kpi_value nightly, so the metric is already "
    "computed by the time a use case runs and read_gold returns the value."
)


#: The four shapes' step sequences, from build-plan.md section 07, in order.
#:
#: **Trigger is not in these lists.** It is the schedule that starts the workflow rather than
#: a step inside it, and modelling it as a step would put a row on the plan screen that never
#: executes. It binds as the ``schedule`` slot instead.
SHAPE_STAGES: Mapping[UseCaseShape, Tuple[Stage, ...]] = {
    UseCaseShape.INFORM: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold"),
        Stage(
            "Compute metrics",
            StageKind.EXECUTOR,
            "compute_metrics",
            satisfied_by="read_gold",
            satisfied_because=_COMPUTE_SATISFIED,
        ),
        Stage("Compare to baseline", StageKind.EXECUTOR, "compare_to_baseline"),
        Stage(
            "Explain",
            StageKind.MODEL,
            role="explanation",
            description="One model step. Explains the numbers; never decides anything.",
        ),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage("Deliver", StageKind.EXECUTOR, "deliver_in_app"),
    ),
    UseCaseShape.ALERT: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold"),
        Stage(
            "Compute metric",
            StageKind.EXECUTOR,
            "compute_metrics",
            satisfied_by="read_gold",
            satisfied_because=_COMPUTE_SATISFIED,
        ),
        Stage(
            "Evaluate condition",
            StageKind.BRANCH,
            "evaluate_alert_condition",
            description=(
                "Deterministic. Compares the metric to the threshold and decides whether the "
                "steps after it run. Logs the outcome either way -- an alert that does not "
                "fire is still logged, so a quiet week is visibly quiet (open item R-13)."
            ),
        ),
        Stage(
            "Write the alert",
            StageKind.EXECUTOR,
            "write_alert",
            description="Only reached when the condition fired.",
        ),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage("Notify", StageKind.EXECUTOR, "deliver_in_app"),
    ),
    UseCaseShape.RECOMMEND: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold"),
        Stage("Select candidates", StageKind.EXECUTOR, "select_candidates"),
        Stage(
            "Compute metrics",
            StageKind.EXECUTOR,
            "compute_metrics",
            satisfied_by="read_gold",
            satisfied_because=_COMPUTE_SATISFIED,
        ),
        Stage(
            "Propose",
            StageKind.MODEL,
            role="proposal",
            description="One model step. Proposes changes; the limits below decide which stand.",
        ),
        Stage("Apply hard limits", StageKind.EXECUTOR, "apply_hard_limits"),
        Stage(
            "Approver decides",
            StageKind.GATE,
            "await_approval",
            description="A person decides. The run stops here until they do.",
        ),
        Stage("Write to target", StageKind.EXECUTOR, "write_to_target"),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage("Notify owner", StageKind.EXECUTOR, "deliver_in_app"),
    ),
    UseCaseShape.ACT: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold"),
        Stage("Select candidates", StageKind.EXECUTOR, "select_candidates"),
        Stage(
            "Compute metrics",
            StageKind.EXECUTOR,
            "compute_metrics",
            satisfied_by="read_gold",
            satisfied_because=_COMPUTE_SATISFIED,
        ),
        Stage(
            "Propose",
            StageKind.MODEL,
            role="proposal",
            description="One model step. Proposes changes; the limits below decide which stand.",
        ),
        Stage("Apply hard limits", StageKind.EXECUTOR, "apply_hard_limits"),
        Stage("Apply per-run cap", StageKind.EXECUTOR, "apply_per_run_cap"),
        Stage("Write to target", StageKind.EXECUTOR, "write_to_target"),
        Stage("Record rollback point", StageKind.EXECUTOR, "record_rollback_point"),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage("Notify owner", StageKind.EXECUTOR, "deliver_in_app"),
    ),
}

#: Which spec or draft field fills each slot, as a dotted path into the contract's
#: ``UseCaseDraft`` where the field exists there today (``step8.schedule`` is what the
#: interface actually writes; a flat ``schedule`` is also accepted so a caller assembling a
#: draft by hand is not refused). Slots the contract has no field for yet -- operator,
#: threshold, severity, candidates, approver, write target, timeout, per-run cap, rollback --
#: read a flat key under the slot's slug until R-15 gives them one. The per-shape form the
#: interface will carry is exactly this list, which is why R-15 quotes it.
_SLOT_VALUES: Mapping[str, Tuple[str, str]] = {
    "metrics": ("spec", "kpi_identifiers"),
    "metric": ("spec", "kpi_identifiers"),
    "dimensions": ("spec", "grain"),
    "scope": ("spec", "grain"),
    "comparison": ("spec", "time_window"),
    "recipient": ("draft", "step8.recipient"),
    "schedule": ("draft", "step8.schedule"),
    "operator": ("draft", "operator"),
    "threshold": ("draft", "threshold"),
    "severity": ("draft", "severity"),
    "guardrails": ("draft", "step8.guardrail_amount"),
    "candidates": ("draft", "candidates"),
    "approver": ("draft", "approver"),
    "write target": ("draft", "write_target"),
    "timeout behaviour": ("draft", "timeout_behaviour"),
    "per-run cap": ("draft", "per_run_cap"),
    "rollback": ("draft", "rollback"),
}


@dataclass
class CompiledWorkflow:
    """What the compiler produced, and everything it could not."""

    use_case_id: str
    shape: UseCaseShape
    autonomy: Optional[str] = None
    #: The serialised workflow, in the shape ``plan_from_config`` walks.
    config: Dict[str, Any] = field(default_factory=lambda: {"steps": []})
    #: Stage name -> executor_ref, for stages whose executor this deployment does not have.
    missing_capabilities: Dict[str, str] = field(default_factory=dict)
    #: Stage name -> the executor that subsumes it. Recorded so the substitution is visible
    #: to a reviewer instead of the stage simply not appearing.
    satisfied_elsewhere: Dict[str, str] = field(default_factory=dict)
    #: Slots the shape requires that no spec or draft field filled.
    unfilled_slots: List[str] = field(default_factory=list)
    #: The bound parameter values, slot -> value.
    parameters: Dict[str, Any] = field(default_factory=dict)
    #: Slot -> where its value came from. Same keys as ``parameters``.
    parameter_origins: Dict[str, ParameterOrigin] = field(default_factory=dict)
    #: One entry per model step: its agent id, role and the stage it came from. The
    #: step-prompt skill authors against exactly this list.
    model_steps: List[Dict[str, str]] = field(default_factory=list)
    notes: str = ""

    @property
    def publishable(self) -> bool:
        """Whether this may be activated.

        Both conditions, and neither is negotiable. A missing executor means a step that
        cannot run; an unfilled slot means a shape's guarantee is unbacked -- an Act with no
        per-run cap bound is an unattended writer with no bound on how much it writes, which
        is the case the build plan singles out as needing to be held rather than dropped.
        """
        return not self.missing_capabilities and not self.unfilled_slots

    @property
    def required_routing_rows(self) -> Dict[str, str]:
        """Agent id -> model tier, for every model step this workflow carries.

        **A generated workflow cannot be planned until these rows exist.** R8's
        ``ROUTING_TABLE`` is a static literal keyed by agent id, and K15 mints an agent id per
        use case (``weekly_pipeline__explanation``), so no hand-written table can contain one.
        R9's ``plan_from_config`` refuses an agent step whose tier it cannot resolve -- rightly,
        because an unrouted step has no model -- which means activation has to register these
        before the plan screen can render. The values are decided here so that registration is
        a lookup rather than a judgement; the registration path itself is Lane R's, and it does
        not exist yet. See open item **K-9**.
        """
        return {
            step["agent_id"]: step["model_tier"]
            for step in self.model_steps
            if step.get("model_tier")
        }

    @property
    def refusal_reasons(self) -> List[str]:
        """Why this is not publishable, written for a person, most structural first."""
        reasons = [
            f"stage {stage!r} needs executor {ref!r}, which this deployment does not register"
            for stage, ref in self.missing_capabilities.items()
        ]
        reasons += [
            f"the {self.shape.value} shape requires a {slot!r} parameter and nothing filled it"
            for slot in self.unfilled_slots
        ]
        return reasons


def _step_dict(
    *,
    name: str,
    step_id: str,
    description: Optional[str] = None,
    executor_ref: Optional[str] = None,
    agent_id: Optional[str] = None,
    requires_confirmation: bool = False,
    confirmation_message: Optional[str] = None,
) -> Dict[str, Any]:
    """One serialised ``Step``, in the shape ``egpt.workflow.Step.to_dict`` produces.

    Built here rather than by constructing ``Step`` objects for two reasons. A model step
    would need an ``Agent`` instance only to carry its id, and building one agent per stage is
    exactly what CLAUDE.md forbids. And ``Step`` assigns a random ``step_id``, which would
    make two compilations of one spec differ in every step -- the diff this package is
    verified by would then never be clean. ``test_k15_orchestration`` asserts this dict equals
    what ``Step.to_dict()`` produces for the same inputs, so a change to the framework's
    serialisation fails there rather than at activation.
    """
    return {
        "type": "Step",
        "name": name,
        "step_id": step_id,
        "description": description,
        "max_retries": 3,
        "skip_on_failure": False,
        "strict_input_validation": False,
        "add_workflow_history": None,
        "num_history_runs": 3,
        "human_review": {
            "requires_confirmation": requires_confirmation,
            "confirmation_message": confirmation_message,
            "requires_user_input": False,
            "user_input_message": None,
            "user_input_schema": None,
            "requires_output_review": False,
            "output_review_message": None,
            "requires_iteration_review": False,
            "iteration_review_message": None,
            "on_reject": "skip",
            "on_error": "skip",
            "max_retries": 3,
            "timeout": None,
            "on_timeout": "cancel",
        },
        **({"executor_ref": executor_ref} if executor_ref else {}),
        **({"agent_id": agent_id} if agent_id else {}),
    }


def _slug(text: str) -> str:
    return "_".join(part for part in text.lower().replace("-", " ").split() if part)


def _lookup(source: Mapping[str, Any], key: str) -> Any:
    """``step8.schedule`` from a nested draft, or ``schedule`` from a flat one. Dotted first.

    The nested form is the contract's; the flat form is what a test or a hand-built spec
    passes. Both are accepted so the compiler consumes what the interface produces without
    refusing what a person types, and the dotted path wins where both are present because it
    is the one the activation screen reads back.
    """
    if "." in key:
        node: Any = source
        for part in key.split("."):
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(part)
        if node is not None:
            return node
        return source.get(key.rsplit(".", 1)[-1])
    return source.get(key)


def _filled(value: Any) -> bool:
    """Whether a slot value counts as bound. Zero and False are values; empty is not."""
    if value is None:
        return False
    if isinstance(value, (str, list, tuple, dict, set)):
        return bool(value)
    return True


def _shape_of(spec: Mapping[str, Any], shape: Optional[Any]) -> UseCaseShape:
    """The shape this spec was written against. Never inferred.

    A spec form is chosen per shape, so a spec that names no shape was not produced by one of
    those forms and there is nothing here to compile. Guessing would pick Inform, which is the
    one shape whose parameters every other shape's spec happens to satisfy -- an Act would
    quietly compile as a report.
    """
    raw = shape if shape is not None else spec.get("shape")
    if raw is None:
        raise UnknownShape(
            "the spec names no shape. Each shape has its own spec form, so a shape is chosen "
            "before a spec is filled; compiling without one would default to Inform and turn "
            "an Act into a report. Pass shape= explicitly, or add it to the spec."
        )
    if isinstance(raw, UseCaseShape):
        return raw
    try:
        return UseCaseShape(str(raw).strip().lower())
    except ValueError:
        raise UnknownShape(
            f"{raw!r} is not one of the four shapes "
            f"({', '.join(s.value for s in UseCaseShape)})."
        ) from None


def compile_workflow(
    spec: Mapping[str, Any],
    *,
    registered_executors: Collection[str],
    shape: Optional[Any] = None,
    autonomy: Optional[str] = None,
    draft: Optional[Mapping[str, Any]] = None,
    use_case_id: Optional[str] = None,
) -> CompiledWorkflow:
    """Compile one frozen spec into a workflow definition. Pure; consults no model.

    ``registered_executors`` is what ``build_registry()`` resolves -- the compiler's entire
    universe of deterministic steps. Passing it in rather than importing the registry keeps
    this pure and lets a caller compile against a deployment other than its own, which is what
    :func:`capability_report` does.

    ``spec`` is what the interface's spec screen produced: the shape the person chose, plus
    the values a conversation and a form filled into that shape's slots. That pairing is the
    whole input -- **the shape is the template**, so two Alert use cases arrive here as the
    same sequence with different slot values, and nothing else distinguishes them.
    """
    draft = draft or {}
    identifier = use_case_id or str(spec.get("use_case_id") or "use_case")
    resolved = _shape_of(spec, shape)
    known = set(registered_executors)
    compiled = CompiledWorkflow(
        use_case_id=identifier, shape=resolved, autonomy=autonomy
    )

    steps: List[Dict[str, Any]] = []
    missing_gate = False
    for index, stage in enumerate(SHAPE_STAGES[resolved], start=1):
        step_id = f"{identifier}.{index:02d}.{_slug(stage.name)}"
        if stage.kind is StageKind.MODEL:
            agent_id = f"{identifier}__{stage.role or _slug(stage.name)}"
            steps.append(
                _step_dict(
                    name=stage.name,
                    step_id=step_id,
                    description=stage.description or None,
                    agent_id=agent_id,
                )
            )
            role = stage.role or ""
            compiled.model_steps.append(
                {
                    "agent_id": agent_id,
                    "role": role,
                    "stage": stage.name,
                    # Emitted so activation can register the row R8 needs. The tier follows
                    # from the role under G6 and is never the model's choice.
                    "model_tier": TIER_FOR_ROLE.get(role, ""),
                }
            )
            continue

        if stage.satisfied_by:
            compiled.satisfied_elsewhere[stage.name] = stage.satisfied_by
            continue

        ref = stage.executor_ref or ""
        if ref not in known:
            compiled.missing_capabilities[stage.name] = ref
            if stage.kind is StageKind.GATE:
                missing_gate = True
            continue
        steps.append(
            _step_dict(
                name=stage.name,
                step_id=step_id,
                description=stage.description or None,
                executor_ref=ref,
                requires_confirmation=stage.kind is StageKind.GATE,
                confirmation_message=(
                    f"Approve before {identifier} writes to its target."
                    if stage.kind is StageKind.GATE
                    else None
                ),
            )
        )

    # The shape declares the slots; the spec and the form fill them. A value the author typed
    # wins over one the chat proposed -- otherwise editing a field the conversation had
    # already filled would do nothing -- and the origin of whichever won is recorded, because
    # "the approver set this cap" and "a model inferred this cap from a sentence" are
    # different facts about the same number.
    for slot in SHAPE_PARAMETERS[resolved]:
        source, key = _SLOT_VALUES.get(slot, ("draft", _slug(slot)))
        authored = _lookup(draft, key)
        spec_value = _lookup(spec, key) if source == "spec" else None

        if _filled(authored):
            compiled.parameters[slot] = authored
            compiled.parameter_origins[slot] = ParameterOrigin.AUTHORED
        elif _filled(spec_value):
            compiled.parameters[slot] = spec_value
            compiled.parameter_origins[slot] = ParameterOrigin.SPEC
        else:
            compiled.unfilled_slots.append(slot)

    # A sequence missing its gate carries no steps at all. That is the one shortfall where a
    # partial workflow is dangerous rather than merely incomplete: every remaining step still
    # runs, and the person who was supposed to approve the write has been compiled out. Any
    # other shortfall still emits what it could build -- `publishable` already stops
    # activation, and a reviewer needs to see the sequence to judge what is missing from it.
    compiled.config = {"steps": [] if missing_gate else steps}
    if missing_gate:
        compiled.notes = (
            f"Refused: the {resolved.value} shape's approval gate has no executor, so every "
            "step after it would run unapproved. No steps emitted."
        )
    elif not compiled.publishable:
        compiled.notes = (
            f"Not publishable as {resolved.value}: "
            + "; ".join(compiled.refusal_reasons)
            + "."
        )
    return compiled


async def acompile_workflow(
    spec: Mapping[str, Any],
    *,
    registered_executors: Collection[str],
    shape: Optional[Any] = None,
    autonomy: Optional[str] = None,
    draft: Optional[Mapping[str, Any]] = None,
    use_case_id: Optional[str] = None,
) -> CompiledWorkflow:
    """Async variant. Pure, so it exists to keep the calling convention uniform."""
    return await asyncio.to_thread(
        compile_workflow,
        spec,
        registered_executors=registered_executors,
        shape=shape,
        autonomy=autonomy,
        draft=draft,
        use_case_id=use_case_id,
    )


def capability_report(
    registered_executors: Collection[str],
) -> Dict[str, Dict[str, Any]]:
    """Per shape, which stages this deployment can build and which it cannot.

    The useful output of this module today, and the reason K15 is worth having before R10
    lands: it says exactly which executors Lane R must write before each shape can be
    generated at all, and it says it as a set difference rather than as an estimate.
    """
    known = set(registered_executors)
    report: Dict[str, Dict[str, Any]] = {}
    for shape, stages in SHAPE_STAGES.items():
        missing = {
            stage.name: stage.executor_ref or ""
            for stage in stages
            if stage.kind is not StageKind.MODEL
            and not stage.satisfied_by
            and (stage.executor_ref or "") not in known
        }
        satisfied = {
            stage.name: stage.satisfied_by or ""
            for stage in stages
            if stage.satisfied_by
        }
        report[shape.value] = {
            "stages": len(stages),
            "model_steps": sum(1 for s in stages if s.kind is StageKind.MODEL),
            "buildable": len(stages) - len(missing) - len(satisfied),
            "satisfied_elsewhere": satisfied,
            "missing": missing,
            "parameters": list(SHAPE_PARAMETERS[shape]),
        }
    return report


def normalise_config(config: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """A config reduced to what a diff should compare: order, name, ref and gate.

    ``step_id`` is deterministic here but not in a hand-built workflow, and every hand-built
    step carries framework defaults nobody chose. Comparing the whole dict would report
    differences that mean nothing; comparing this reports the ones that do.
    """
    reduced: List[Dict[str, Any]] = []
    for step in config.get("steps") or ():
        if not isinstance(step, Mapping):
            continue
        review = step.get("human_review") or {}
        reduced.append(
            {
                "name": step.get("name"),
                "ref": step.get("executor_ref") or step.get("agent_id"),
                "kind": "executor" if step.get("executor_ref") else "model",
                "gate": bool(review.get("requires_confirmation")),
            }
        )
    return reduced


def diff_against(
    compiled: CompiledWorkflow, hand_built: Mapping[str, Any]
) -> List[str]:
    """Differences between a compiled definition and a hand-built one, as readable lines.

    K15's evidence line is that the two *"match step for step, or every difference is
    explainable and one of the two is wrong"*. This produces the list a person reads to make
    that judgement; it does not make it.

    Aligned rather than compared position by position. One extra step at the front of a
    hand-built sequence shifts every later step, and a positional diff then reports five
    differences where there is one -- which buries the finding under its own consequences.
    """
    ours = normalise_config(compiled.config)
    theirs = normalise_config(copy.deepcopy(dict(hand_built)))
    mine_keys = [step["ref"] for step in ours]
    their_keys = [step["ref"] for step in theirs]

    lines: List[str] = []
    matcher = difflib.SequenceMatcher(a=mine_keys, b=their_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                mine, yours = ours[i1 + offset], theirs[j1 + offset]
                if mine["gate"] != yours["gate"]:
                    lines.append(
                        f"{mine['name']}: generated "
                        f"{'requires' if mine['gate'] else 'does not require'} confirmation, "
                        f"hand-built "
                        f"{'requires' if yours['gate'] else 'does not require'} it"
                    )
            continue
        for step in ours[i1:i2]:
            lines.append(f"only generated: {step['name']} ({step['ref']})")
        for step in theirs[j1:j2]:
            lines.append(f"only hand-built: {step['name']} ({step['ref']})")
    return lines
