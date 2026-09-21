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

**Two different things are called a template, and conflating them is easy.**

1. *The shape template.* The sense that matters to K15: **the shape is the template.** Two
   Alert use cases are the same sequence with the same slots, and everything that
   distinguishes them fills those slots. There is no artefact for this -- it is
   :data:`SHAPE_PARAMETERS` and the compiler's stage table, nothing more. Nothing is
   "created from" a shape template; a spec is written against one.
2. *A starter use case.* The contract's ``UseCaseStatus.template`` and ``template_id``: a use
   case that ships with the product, which a person copies to get a draft with its fields
   already filled ("the draft is prefilled from it"). The prototype's catalog page holds
   fifteen. This is a content and interface concept and it does not reach the compiler at
   all -- by the time a spec exists, whether a person wrote it from scratch or copied a
   starter is not a fact the workflow depends on.

K13 may cite the second in a suggestion's ``template_id``. K15 uses only the first.

**A third sense arrived with the first document-driven use case, and it is deliberately the
first sense with one more axis.** Two use cases can share a shape and not a sequence: a
Process Automation that tunes safety stock from a KPI and one that turns an emailed purchase
order into a Salesforce order are both Act -- one unattended write, guarded, capped, reversible
-- but nothing in the KPI-driven sequence reads a document, and nothing in the document-driven
one reads gold. :class:`UseCaseTemplate` names the sequence within the shape; the shape still
decides what the write may and may not do. A template is data (a stage table and a slot list),
never a Python workflow, so the orchestration agent composes it the way it composes a shape.
The test of the design: a second document-to-record use case must need no new executor.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping, Tuple

__all__ = [
    "SHAPE_PARAMETERS",
    "TEMPLATE_PARAMETERS",
    "TEMPLATE_SHAPE",
    "TIER_FOR_ROLE",
    "UseCaseTemplate",
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
            "window",
            "title",
            "outputs",
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


class UseCaseTemplate(str, Enum):
    """The sequence within a shape, where a shape has more than one.

    ``KPI_CHANGE`` is the Act sequence build-plan section 07 describes: read gold, select
    candidates, propose, guard, cap, write. ``DOCUMENT_TO_RECORD`` is the Act sequence a
    document-driven automation needs: receive, screen, extract, resolve, guard, build, cap,
    write. Same shape, same guarantees around the write, different stages before it. The
    interface never asks for this; it reads it from the requirement.
    """

    KPI_CHANGE = "kpi_change"
    DOCUMENT_TO_RECORD = "document_to_record"
    #: The Inform sequence that explains a change (K25): one KPI, two periods, the change
    #: decomposed across the fact's dimensions by arithmetic before the model narrates it.
    METRIC_DIAGNOSIS = "metric_diagnosis"
    #: The Inform sequence that finds who or what is at risk (K26): every open subject
    #: scored by a model trained on the fact's own history, the account's context
    #: attached, and the outcome a recommendation or an alert handed to Decision
    #: Intelligence -- never a bare number.
    AT_RISK = "at_risk"


#: Which shape each template is a sequence of. Both are Act today; a template of another shape
#: would be added here, not inferred.
TEMPLATE_SHAPE: Mapping[UseCaseTemplate, UseCaseShape] = MappingProxyType(
    {
        UseCaseTemplate.KPI_CHANGE: UseCaseShape.ACT,
        UseCaseTemplate.DOCUMENT_TO_RECORD: UseCaseShape.ACT,
        UseCaseTemplate.METRIC_DIAGNOSIS: UseCaseShape.INFORM,
        UseCaseTemplate.AT_RISK: UseCaseShape.INFORM,
    }
)

#: The parameters each template's spec form carries, in the form's order. ``KPI_CHANGE`` is
#: exactly the Act shape's list; ``DOCUMENT_TO_RECORD`` replaces the KPI-driven front half
#: (candidates, metrics) with the document-driven one and keeps the write-side slots
#: (guardrails, write target, per-run cap, rollback, schedule) under the same names, because
#: the executors that consume them are the same ones.
TEMPLATE_PARAMETERS: Mapping[UseCaseTemplate, Tuple[str, ...]] = MappingProxyType(
    {
        UseCaseTemplate.KPI_CHANGE: SHAPE_PARAMETERS[UseCaseShape.ACT],
        UseCaseTemplate.DOCUMENT_TO_RECORD: (
            "source mailbox",
            "message filter",
            "document type",
            "extraction template",
            "example document",
            "master data",
            "match thresholds",
            "guardrails",
            "recipient",
            "acknowledge",
            "write target",
            "target objects",
            "created as",
            "per-run cap",
            "rollback",
            "schedule",
        ),
        # The diagnostic (K25): the Inform list, ``dimensions`` meaning the columns the
        # change is searched across, plus the two things the arithmetic cannot decide.
        UseCaseTemplate.METRIC_DIAGNOSIS: (
            "metrics",
            "comparison",
            "window",
            "dimensions",
            "sensitivity",
            "evidence columns",
            "schedule",
            "recipient",
            "title",
            "outputs",
        ),
        # The at-risk kind (K26): the KPIs it reads for context, the churn window and the
        # outcome the person wants; the risk lines and the run's size keep their
        # defaults unless the spec sets them.
        UseCaseTemplate.AT_RISK: (
            "metrics",
            "churn window",
            "outcome",
            "risk levels",
            "top n",
            "schedule",
            "recipient",
            "title",
            "outputs",
        ),
    }
)


#: The model tier each model-step role runs on, from K14 section 1 under G6: explanation runs
#: on the small tier, suggestion on the frontier tier because it is rare and quality-critical.
#: Extraction runs on the frontier tier too: a mis-read quantity becomes a wrong order, and the
#: cost is one call per document rather than per run.
#:
#: Here rather than in the authoring skill because both halves of K15 need it and must agree.
#: The compiler emits it so activation can register the generated agent; the step-prompt skill
#: writes it into the prompt header. A model that could choose its own tier would choose
#: frontier every time, so this is never asked of one.
TIER_FOR_ROLE: Mapping[str, str] = MappingProxyType(
    {
        "explanation": "small",
        "proposal": "frontier",
        "extraction": "frontier",
    }
)
