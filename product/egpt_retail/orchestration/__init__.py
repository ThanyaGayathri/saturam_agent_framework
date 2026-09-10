"""K15 -- turning a frozen spec into a publishable workflow definition.

Two parts, and only one is a model. :mod:`egpt_retail.orchestration.compiler` is the
deterministic half: the shape follows from the spec's autonomy level, the spec's values bind
into that shape's parameter slots, and the result is validated against the executor registry.
:mod:`egpt_retail.skills.step_prompt` is the model half, authoring the prompt for each model
step the compiled shape carries.
"""

from egpt_retail.orchestration.compiler import (
    SHAPE_STAGES,
    TRIGGER_SLOTS,
    CompiledWorkflow,
    ParameterOrigin,
    Stage,
    StageKind,
    UnknownShape,
    acompile_workflow,
    capability_report,
    compile_workflow,
    diff_against,
    normalise_config,
)

__all__ = [
    "SHAPE_STAGES",
    "TRIGGER_SLOTS",
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
