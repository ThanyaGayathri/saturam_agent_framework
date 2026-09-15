"""``/extraction-templates`` and ``/guardrail-rules`` (K17): the two catalogues a document-
driven use case draws on, served as data.

The shipped templates and the rule kinds are read-only lists; drafting a template runs the
extraction-tier model once; suggesting rules is deterministic over a template's fields. The
interface holds the person's own templates in its workspace and sends the chosen one on the
spec, so nothing here stores anything.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from egpt_retail.api import models as wire
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.settings import RetailSettings
from egpt_retail.documents.guardrails import RULE_KINDS, suggest_rules
from egpt_retail.documents.template_draft import (
    TemplateDraft,
    adraft_template,
    draft_instructions,
    example_text_from,
)
from egpt_retail.documents.templates import (
    EXTRACTION_TEMPLATES,
    template_from,
    template_to_dict,
)

__all__ = ["router", "drafting_agent"]

router = APIRouter()


def _template_out(raw: Any, *, source: str) -> wire.ExtractionTemplate:
    data = dict(raw)
    for key in ("header", "lines"):
        data[key] = [
            {"name": f.rstrip("*"), "required": f.endswith("*"), "hint": ""}
            if isinstance(f, str)
            else f
            for f in data.get(key) or []
        ]
    data.setdefault("source", source)
    return wire.ExtractionTemplate.model_validate(data)


def drafting_agent() -> Optional[Any]:
    """The drafting agent, or None when no model is configured. Overridden in tests."""
    if not os.environ.get("AZURE_OPENAI_API_KEY"):
        return None
    from egpt.agent import Agent
    from egpt.integrations.models.azure import AzureOpenAI

    settings = RetailSettings()
    return Agent(
        id="extraction_template_drafter",
        name="extraction_template_drafter",
        model=AzureOpenAI(id=settings.retail_azure_deployment),
        instructions=draft_instructions(),
        output_schema=TemplateDraft,
    )


@router.get("/extraction-templates", response_model=List[wire.ExtractionTemplate])
def list_extraction_templates(
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.ExtractionTemplate]:
    return [
        _template_out(template_to_dict(t), source="shipped")
        for t in EXTRACTION_TEMPLATES.values()
    ]


@router.post("/extraction-templates/draft", response_model=wire.ExtractionTemplateDraft)
async def draft_extraction_template(
    body: wire.ExtractionTemplateDraftRequest,
    agent: Optional[Any] = Depends(drafting_agent),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.ExtractionTemplateDraft:
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail="No extraction model is configured (AZURE_OPENAI_API_KEY).",
        )
    try:
        example = example_text_from(body.example_document_b64, body.example_text)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"The example document could not be read: {exc}"
        )
    drafted = await adraft_template(
        agent, body.description, label=body.label, example_text=example
    )
    return wire.ExtractionTemplateDraft.model_validate(
        {
            "template": _template_out(drafted["template"], source="drafted").model_dump(
                mode="json"
            ),
            "notes": drafted["notes"],
        }
    )


@router.get("/guardrail-rules/kinds", response_model=List[wire.GuardrailRuleKind])
def list_guardrail_rule_kinds(
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.GuardrailRuleKind]:
    return [
        wire.GuardrailRuleKind.model_validate(
            {
                "id": k.id,
                "label": k.label,
                "check": k.check,
                "scope": k.scope,
                "params": dict(k.params),
                "needs": k.needs,
                "exception": k.exception,
            }
        )
        for k in RULE_KINDS.values()
    ]


@router.post("/guardrail-rules/suggest", response_model=List[wire.GuardrailRule])
def suggest_guardrail_rules(
    body: wire.ExtractionTemplate, _: None = Depends(require_scopes("use-cases:read"))
) -> List[wire.GuardrailRule]:
    try:
        template = template_from(body.model_dump(mode="json"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [
        wire.GuardrailRule.model_validate(rule.as_dict())
        for rule in suggest_rules(template)
    ]
