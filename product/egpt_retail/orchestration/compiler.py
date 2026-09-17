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
from types import MappingProxyType
from typing import Any, Collection, Dict, FrozenSet, List, Mapping, Optional, Tuple

from egpt_retail.contracts.use_case import (
    SHAPE_PARAMETERS,
    TEMPLATE_PARAMETERS,
    TEMPLATE_SHAPE,
    TIER_FOR_ROLE,
    UseCaseShape,
    UseCaseTemplate,
)

__all__ = [
    "OPTIONAL_SLOTS",
    "SHAPE_STAGES",
    "TEMPLATE_STAGES",
    "TRIGGER_SLOTS",
    "UnknownTemplate",
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


class UnknownTemplate(ValueError):
    """The spec named a template that does not exist, or one of another shape."""


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
    #: Neither supplied: an optional slot bound to its declared default (``SLOT_DEFAULTS``).
    DEFAULT = "default"


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
    #: The shape's parameter slots this stage reads. Every slot a shape declares must be
    #: consumed by one of its stages or be a trigger slot, and a test holds that line --
    #: because the Inform shape shipped with a ``comparison`` slot no stage read (K-14).
    consumes: Tuple[str, ...] = ()
    #: A stage without which a write must not run: the hard limits, the per-run cap, the
    #: rollback point, the approval gate. Missing any of these in a shape that writes refuses
    #: the whole compilation, exactly as a missing gate does. Round 1 of PR 208: an Act with
    #: ``write_to_target`` registered before ``apply_per_run_cap`` would otherwise have
    #: emitted an uncapped write step with nothing downstream to stop it.
    guards_write: bool = False


#: What "compute metrics" means once there is a gold layer. R5 materialises every ready KPI's
#: formula into ``gold.kpi_value`` nightly, so by the time a use case runs the metric is
#: already computed and ``read_gold`` returns the value. The plan's separate compute stage
#: predates that decision. Recorded as a substitution rather than deleted from the table,
#: because a module claiming to implement section 07 verbatim should say where it deviates.
_COMPUTE_SATISFIED = (
    "R5 materialises every ready KPI into gold.kpi_value nightly, so the metric is already "
    "computed by the time a use case runs and read_gold returns the value."
)


#: Slots consumed by the trigger rather than by a step. The trigger is the schedule that
#: starts the workflow, not a row on the plan screen, so no stage reads these.
TRIGGER_SLOTS: FrozenSet[str] = frozenset({"schedule"})

#: The four shapes' step sequences, from build-plan.md section 07, in order.
#:
#: **Trigger is not in these lists.** It is the schedule that starts the workflow rather than
#: a step inside it, and modelling it as a step would put a row on the plan screen that never
#: executes. It binds as the ``schedule`` slot instead.
SHAPE_STAGES: Mapping[UseCaseShape, Tuple[Stage, ...]] = {
    UseCaseShape.INFORM: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold", consumes=("metrics",)),
        Stage(
            "Compute metrics",
            StageKind.EXECUTOR,
            "compute_metrics",
            satisfied_by="read_gold",
            satisfied_because=_COMPUTE_SATISFIED,
            consumes=("dimensions",),
        ),
        Stage(
            "Compare to baseline",
            StageKind.EXECUTOR,
            "compare_to_baseline",
            consumes=("comparison", "window"),
            description=(
                "Each KPI at the run's date against gold at the baseline date -- the "
                "period before, or the same period last year -- per KPI and per breakdown "
                "row. A baseline date gold does not hold yet is materialised for it."
            ),
        ),
        Stage(
            "Explain",
            StageKind.MODEL,
            role="explanation",
            description="One model step. Explains the numbers; never decides anything.",
        ),
        Stage(
            "Check grounding",
            StageKind.EXECUTOR,
            "check_report_grounding",
            guards_write=False,
            description=(
                "Every figure in the explanation must be one the comparison carried; an "
                "explanation that invents one is withheld and the report carries the "
                "numbers alone."
            ),
        ),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage(
            "Render report",
            StageKind.EXECUTOR,
            "render_report",
            consumes=("title", "outputs"),
            description="The comparison and the narrative as an HTML report and its text twin.",
        ),
        Stage("Deliver", StageKind.EXECUTOR, "deliver_in_app", consumes=("recipient",)),
        Stage(
            "Send report",
            StageKind.EXECUTOR,
            "send_report",
            consumes=("recipient",),
            description="The HTML report by email to the recipients; held in a rehearsal.",
        ),
    ),
    UseCaseShape.ALERT: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold", consumes=("metric",)),
        Stage(
            "Compute metric",
            StageKind.EXECUTOR,
            "compute_metrics",
            satisfied_by="read_gold",
            satisfied_because=_COMPUTE_SATISFIED,
            consumes=("scope",),
        ),
        Stage(
            "Evaluate condition",
            StageKind.BRANCH,
            "evaluate_alert_condition",
            consumes=("operator", "threshold"),
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
            consumes=("severity",),
        ),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage("Notify", StageKind.EXECUTOR, "deliver_in_app", consumes=("recipient",)),
    ),
    UseCaseShape.RECOMMEND: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold", consumes=("metrics",)),
        Stage(
            "Select candidates",
            StageKind.EXECUTOR,
            "select_candidates",
            consumes=("candidates",),
        ),
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
        Stage(
            "Apply hard limits",
            StageKind.EXECUTOR,
            "apply_hard_limits",
            consumes=("guardrails",),
            guards_write=True,
        ),
        Stage(
            "Approver decides",
            StageKind.GATE,
            "await_approval",
            description="A person decides. The run stops here until they do.",
            consumes=("approver", "timeout behaviour"),
            guards_write=True,
        ),
        Stage(
            "Write to target",
            StageKind.EXECUTOR,
            "write_to_target",
            consumes=("write target",),
        ),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage("Notify owner", StageKind.EXECUTOR, "deliver_in_app"),
    ),
    UseCaseShape.ACT: (
        Stage("Read gold", StageKind.EXECUTOR, "read_gold", consumes=("metrics",)),
        Stage(
            "Select candidates",
            StageKind.EXECUTOR,
            "select_candidates",
            consumes=("candidates",),
        ),
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
        Stage(
            "Apply hard limits",
            StageKind.EXECUTOR,
            "apply_hard_limits",
            consumes=("guardrails",),
            guards_write=True,
        ),
        Stage(
            "Apply per-run cap",
            StageKind.EXECUTOR,
            "apply_per_run_cap",
            consumes=("per-run cap",),
            guards_write=True,
        ),
        Stage(
            "Write to target",
            StageKind.EXECUTOR,
            "write_to_target",
            consumes=("write target",),
        ),
        Stage(
            "Record rollback point",
            StageKind.EXECUTOR,
            "record_rollback_point",
            consumes=("rollback",),
            guards_write=True,
        ),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
        Stage("Notify owner", StageKind.EXECUTOR, "deliver_in_app"),
    ),
}

#: The sequences a shape has more than one of. Keyed by template; ``KPI_CHANGE`` is the Act
#: row of :data:`SHAPE_STAGES` itself, so a spec that names no template compiles exactly as
#: before. ``DOCUMENT_TO_RECORD`` is the first document-driven sequence: receive, screen,
#: extract, resolve, then the same write-side machinery Act already has. Every stage is a
#: generic verb -- nothing here says "order" or "Salesforce" -- and the use case is the slot
#: values, which is what lets a second document-driven use case reuse it with no new executor.
TEMPLATE_STAGES: Mapping[UseCaseTemplate, Tuple[Stage, ...]] = {
    UseCaseTemplate.KPI_CHANGE: SHAPE_STAGES[UseCaseShape.ACT],
    UseCaseTemplate.DOCUMENT_TO_RECORD: (
        Stage(
            "Receive",
            StageKind.EXECUTOR,
            "receive_messages",
            consumes=("source mailbox", "message filter"),
            description=(
                "New messages matching the filter, each processed once -- the message id is "
                "the provenance key. None → the run is logged and nothing else happens."
            ),
        ),
        Stage(
            "Screen",
            StageKind.BRANCH,
            "screen_documents",
            consumes=("document type",),
            description=(
                "Deterministic. Exactly one document of the declared type, from a known "
                "sender, under the size limit; anything else is an exception before a model "
                "is called."
            ),
        ),
        Stage(
            "Extract",
            StageKind.MODEL,
            role="extraction",
            consumes=("extraction template", "example document"),
            description=(
                "One model step. Fills the template's schema from the document; a field not "
                "on the document is null. Output validated against the schema before "
                "anything downstream reads it."
            ),
        ),
        Stage(
            "Resolve",
            StageKind.BRANCH,
            "resolve_entities",
            consumes=("master data", "match thresholds"),
            description=(
                "Scored matching against master data in a fixed order of attempts. Below "
                "threshold is an exception with the closest candidate, never a guess."
            ),
        ),
        Stage(
            "Apply guardrails",
            StageKind.BRANCH,
            "apply_hard_limits",
            consumes=("guardrails",),
            guards_write=True,
            description="Record-level checks. Any failure raises an exception; nothing is written.",
        ),
        Stage(
            "Build record",
            StageKind.EXECUTOR,
            "build_record",
            consumes=("target objects", "created as"),
            description=(
                "Maps the resolved fields onto the target's objects. A required field the "
                "mapping cannot fill is an exception, not a blank."
            ),
        ),
        Stage(
            "Apply change limit",
            StageKind.BRANCH,
            "apply_per_run_cap",
            consumes=("per-run cap",),
            guards_write=True,
        ),
        Stage(
            "Write", StageKind.EXECUTOR, "write_to_target", consumes=("write target",)
        ),
        Stage(
            "Record rollback point",
            StageKind.EXECUTOR,
            "record_rollback_point",
            consumes=("rollback",),
            guards_write=True,
        ),
        Stage(
            "Notify",
            StageKind.EXECUTOR,
            "send_email",
            consumes=("recipient", "acknowledge"),
            description=(
                "The sender is acknowledged with the record number or the exception reason; "
                "exceptions go to the configured address as well as the in-app record."
            ),
        ),
        Stage("Record provenance", StageKind.EXECUTOR, "record_provenance"),
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
    # Document-to-record slots. Flat keys until the contract carries them (R-15).
    "source mailbox": ("draft", "source_mailbox"),
    "message filter": ("draft", "message_filter"),
    "document type": ("draft", "document_type"),
    "extraction template": ("draft", "extraction_template"),
    "example document": ("draft", "example_document"),
    "master data": ("draft", "master_data"),
    "match thresholds": ("draft", "match_thresholds"),
    "acknowledge": ("draft", "acknowledge"),
    "target objects": ("draft", "target_objects"),
    "created as": ("draft", "created_as"),
}

#: Slots that may be empty without blocking publication: the value is optional by design.
#: An example document improves extraction but a template's schema is enough to run; an
#: acknowledgement is a courtesy, not a guard; an empty message filter means every new
#: message, which is a filter too.
#: Optional slots that bind a default when the spec leaves them blank (K23). A callable
#: derives the default from the spec.
SLOT_DEFAULTS: Mapping[str, Any] = MappingProxyType(
    {
        "window": None,
        "title": lambda spec: str(spec.get("question") or "Report"),
        "outputs": ["report"],
    }
)

OPTIONAL_SLOTS: FrozenSet[str] = frozenset(
    {
        "example document",
        "acknowledge",
        "message filter",
        # The Inform shape's report slots: a window falls back to the KPI's own, a title
        # to the question, outputs to the report alone.
        "window",
        "title",
        "outputs",
    }
)


@dataclass
class CompiledWorkflow:
    """What the compiler produced, and everything it could not."""

    use_case_id: str
    shape: UseCaseShape
    #: The sequence within the shape, where the shape has more than one. None means the
    #: shape's own sequence.
    template: Optional[UseCaseTemplate] = None
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
        what = (
            f"the {self.template.value} template"
            if self.template
            else f"the {self.shape.value} shape"
        )
        reasons += [
            f"{what} requires a {slot!r} parameter and nothing filled it"
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


def _template_of(
    spec: Mapping[str, Any], template: Optional[Any], shape: UseCaseShape
) -> Optional[UseCaseTemplate]:
    """The sequence within the shape, if the spec names one. Never inferred here.

    The interface reads the template from the requirement's own words and writes it into the
    spec; the compiler only checks that what it was given exists and belongs to the shape. A
    template of another shape is refused rather than silently re-shaped -- an Act sequence
    compiled under Inform would drop its write guards.
    """
    raw = template if template is not None else spec.get("template")
    if raw is None:
        return None
    if isinstance(raw, UseCaseTemplate):
        resolved = raw
    else:
        try:
            resolved = UseCaseTemplate(str(raw).strip().lower())
        except ValueError:
            raise UnknownTemplate(
                f"{raw!r} is not a template "
                f"({', '.join(t.value for t in UseCaseTemplate)})."
            ) from None
    if TEMPLATE_SHAPE[resolved] is not shape:
        raise UnknownTemplate(
            f"template {resolved.value!r} is a {TEMPLATE_SHAPE[resolved].value} sequence and "
            f"cannot be compiled as {shape.value}."
        )
    return resolved


def _spec_parameter(spec: Mapping[str, Any], slot: str) -> Any:
    """``spec.parameters[slot]`` -- the per-shape form's values, keyed by slot name.

    The contract's ``Spec.parameters`` (1.2.0) carries exactly the shape's or template's slot
    list, so the compiler reads one place for every shape. Accepted under the slot name as
    written ("per-run cap") and under its slug ("per_run_cap"), because the interface writes
    the slug and a person types the name.
    """
    params = spec.get("parameters")
    if not isinstance(params, Mapping):
        return None
    if slot in params:
        return params[slot]
    return params.get(_slug(slot))


def compile_workflow(
    spec: Mapping[str, Any],
    *,
    registered_executors: Collection[str],
    shape: Optional[Any] = None,
    template: Optional[Any] = None,
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
    resolved_template = _template_of(spec, template, resolved)
    known = set(registered_executors)
    compiled = CompiledWorkflow(
        use_case_id=identifier,
        shape=resolved,
        template=resolved_template,
        autonomy=autonomy,
    )
    stages = (
        TEMPLATE_STAGES[resolved_template]
        if resolved_template is not None
        else SHAPE_STAGES[resolved]
    )
    slots = (
        TEMPLATE_PARAMETERS[resolved_template]
        if resolved_template is not None
        else SHAPE_PARAMETERS[resolved]
    )

    steps: List[Dict[str, Any]] = []
    missing_gate = False
    for index, stage in enumerate(stages, start=1):
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
            if stage.kind is StageKind.GATE or stage.guards_write:
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
    for slot in slots:
        source, key = _SLOT_VALUES.get(slot, ("draft", _slug(slot)))
        authored = _lookup(draft, key)
        # The slot's own slug is always accepted on the draft, whatever legacy path the table
        # names: a document-to-record form writes ``guardrails`` where the KPI form wrote
        # ``step8.guardrail_amount``, and both are the "guardrails" slot.
        if not _filled(authored):
            authored = draft.get(_slug(slot))
        # The per-shape form's values, then the extractor's legacy top-level fields.
        spec_value = _spec_parameter(spec, slot)
        if not _filled(spec_value) and source == "spec":
            spec_value = _lookup(spec, key)

        if _filled(authored):
            compiled.parameters[slot] = authored
            compiled.parameter_origins[slot] = ParameterOrigin.AUTHORED
        elif _filled(spec_value):
            compiled.parameters[slot] = spec_value
            compiled.parameter_origins[slot] = ParameterOrigin.SPEC
        elif slot in SLOT_DEFAULTS:
            # An optional slot with a sensible default binds it, so the plan shows what will
            # actually run rather than a blank: the report's title is the question, its
            # outputs the report alone, its window the KPI's own declared one.
            value = SLOT_DEFAULTS[slot]
            compiled.parameters[slot] = value(spec) if callable(value) else value
            compiled.parameter_origins[slot] = ParameterOrigin.DEFAULT
        elif slot not in OPTIONAL_SLOTS:
            compiled.unfilled_slots.append(slot)

    # A sequence missing its gate carries no steps at all. That is the one shortfall where a
    # partial workflow is dangerous rather than merely incomplete: every remaining step still
    # runs, and the person who was supposed to approve the write has been compiled out. Any
    # other shortfall still emits what it could build -- `publishable` already stops
    # activation, and a reviewer needs to see the sequence to judge what is missing from it.
    compiled.config = {"steps": [] if missing_gate else steps}
    if missing_gate:
        guards = [
            stage.name
            for stage in stages
            if (stage.kind is StageKind.GATE or stage.guards_write)
            and stage.name in compiled.missing_capabilities
        ]
        compiled.notes = (
            f"Refused: the {resolved.value} shape's write guard(s) {', '.join(guards)} "
            "have no executor, so the write would run unguarded. No steps emitted."
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
    template: Optional[Any] = None,
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
        template=template,
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
    # Templates that are not simply a shape's own sequence get a row of their own, keyed
    # ``<shape>/<template>`` so a reader sees which shape's guarantees they carry.
    for tmpl, stages in TEMPLATE_STAGES.items():
        shape = TEMPLATE_SHAPE[tmpl]
        if stages is SHAPE_STAGES[shape]:
            continue
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
        report[f"{shape.value}/{tmpl.value}"] = {
            "stages": len(stages),
            "model_steps": sum(1 for s in stages if s.kind is StageKind.MODEL),
            "buildable": len(stages) - len(missing) - len(satisfied),
            "satisfied_elsewhere": satisfied,
            "missing": missing,
            "parameters": list(TEMPLATE_PARAMETERS[tmpl]),
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
