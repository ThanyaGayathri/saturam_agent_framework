"""The use case shapes, and what each one's spec has to carry.

Two packages read this and they must agree. K13 proposes a shape per suggestion and gates it
against what a deployment has configured; K15 compiles a frozen spec of that shape into a
workflow definition. A shape defined twice is a shape that drifts, and the drift would show up
as a suggestion whose shape the compiler cannot build.

**Four shapes, and the build plan argues the set is closed:** *"Four shapes is the complete
set, because the axis is side effects rather than subject matter. A forecast and a variance
analysis are both Inform with different compute steps... Nothing about the retail domain adds
a fifth shape."* A customer who could add one would be adding a side effect the executors
cannot perform, which is why this is a Python enum rather than a customer-extensible
vocabulary -- mirrored into the shipped vocabulary so prompts render it from the same place as
everything else.

**The shape is a property of the spec, not something inferred from the autonomy level.** Each
shape has its own spec form, so the shape is chosen before the spec is filled and the slots
below are what that form asks for. R9's ``executors.gaps.shape_for`` derives a shape from
``autonomy`` and ``level`` instead, over three shapes with no Alert; that predates the
per-shape forms and cannot express an Alert at all. See open item **K-8**.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping, Tuple

__all__ = [
    "SHAPE_PARAMETERS",
    "TIER_FOR_ROLE",
    "Autonomy",
    "UseCaseShape",
]


class UseCaseShape(str, Enum):
    """The four shapes, ordered by side effect: none, notify, write-after-approval, write."""

    INFORM = "inform"
    ALERT = "alert"
    RECOMMEND = "recommend"
    ACT = "act"


class Autonomy(str, Enum):
    """Mirrors the API contract's ``Autonomy``. A separate axis from the shape.

    The shape says what the system does to the world; autonomy says whether a person sees it
    first. They are not the same question -- an Inform can be delivered unattended, and a
    Recommend waits for an approver whatever this says.
    """

    COPILOT = "copilot"
    AUTOPILOT = "autopilot"


#: The parameters each shape's spec form must carry, from build-plan.md section 07, verbatim
#: and in the table's order. These are what the UI asks for once a shape is chosen, and what
#: K15 binds into the compiled workflow's slots.
SHAPE_PARAMETERS: Mapping[UseCaseShape, Tuple[str, ...]] = MappingProxyType(
    {
        UseCaseShape.INFORM: (
            "metrics",
            "dimensions",
            "comparison",
            "schedule",
            "recipient",
        ),
        UseCaseShape.ALERT: (
            "metric",
            "operator",
            "threshold",
            "scope",
            "severity",
            "recipient",
            "schedule",
        ),
        UseCaseShape.RECOMMEND: (
            "candidates",
            "metrics",
            "guardrails",
            "approver",
            "write target",
            "timeout behaviour",
            "schedule",
        ),
        UseCaseShape.ACT: (
            "candidates",
            "metrics",
            "guardrails",
            "write target",
            "per-run cap",
            "rollback",
            "schedule",
        ),
    }
)


#: The model tier each model-step role runs on, from K14 section 1 under G6: explanation runs
#: on the small tier, suggestion on the frontier tier because it is rare and quality-critical.
#:
#: Here rather than in the authoring skill because both halves of K15 need it and must agree.
#: The compiler emits it so activation can register the generated agent; the step-prompt skill
#: writes it into the prompt header. A model that could choose its own tier would choose
#: frontier every time, so this is never asked of one.
TIER_FOR_ROLE: Mapping[str, str] = MappingProxyType(
    {
        "explanation": "small",
        "proposal": "frontier",
    }
)
