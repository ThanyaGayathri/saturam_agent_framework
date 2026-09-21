"""The Extract stage's model half (K15): the extractor agent's instructions and output
schema, built from the role template and the extraction template, and the two calls that
run it over a document.

The role's invariant half -- identity, phases, rules, refusals -- is ``ROLE_TEMPLATES
["extraction"]`` in ``skills/step_prompt.py``, written once and reviewed once. What this
module adds per use case is only the extraction template's field list: which header and
line fields to fill, which are required. The output schema is generated from the same
template, so the model is refused the freedom to add a field the template does not declare,
and ``validate_extraction`` refuses it again on the way out.

The tier is the role's (``TIER_FOR_ROLE["extraction"]`` = frontier); the deployment is the
caller's to choose, and the cookbook takes it from ``EGPT_RETAIL_AZURE_DEPLOYMENT`` /
``AZURE_OPENAI_DEPLOYMENT``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, Field, create_model

from egpt_retail.documents.templates import ExtractionTemplate
from egpt_retail.skills.step_prompt import ROLE_TEMPLATES

__all__ = [
    "extraction_instructions",
    "extraction_output_model",
    "document_prompt",
    "extract_with_agent",
    "aextract_with_agent",
]

_NUMERIC = {"quantity", "unit_price", "line_total", "order_total", "total", "tax"}


def extraction_instructions(
    template: ExtractionTemplate, example_document: Optional[str] = None
) -> str:
    """The agent's instructions: the role template rendered as prose, then the field list."""
    role = ROLE_TEMPLATES["extraction"]
    lines: List[str] = [role.identity, ""]
    lines.append("Phases:")
    lines += [f"- {p}" for p in role.phases]
    lines += ["", "Rules:"]
    lines += [f"- {r}" for r in role.rules]
    lines += ["", "Refuse, and return the empty schema, when:"]
    lines += [f"- {r}" for r in role.refusals]
    lines += [
        "",
        f"Template: {template.label} ({template.id}). Fill exactly these fields; null where the document does not show a value.",
        "Header fields (* required):",
    ]
    lines += [
        f"- {name}{'*' if required else ''}" for name, required in template.header
    ]
    lines.append("Line fields (* required), one entry per line on the document:")
    lines += [f"- {name}{'*' if required else ''}" for name, required in template.lines]
    lines += [
        "",
        "Dates as ISO (YYYY-MM-DD) when the document's date is unambiguous; otherwise as written. Numbers as numbers, without currency symbols or thousands separators.",
    ]
    if example_document:
        lines += [
            "",
            "An example document in this format, to learn where the fields sit -- never to copy values from:",
            example_document,
        ]
    return "\n".join(lines)


def extraction_output_model(template: ExtractionTemplate) -> Type[BaseModel]:
    """A pydantic model with exactly the template's fields, every one optional."""

    def field(name: str) -> Tuple[Any, Any]:
        annotation: Any = Optional[float] if name in _NUMERIC else Optional[str]
        return annotation, Field(default=None)

    line_model = create_model(
        f"{template.id}_line", **{name: field(name) for name, _ in template.lines}
    )  # type: ignore[call-overload]
    header = {name: field(name) for name, _ in template.header}
    header["lines"] = (List[line_model], Field(default_factory=list))  # type: ignore[valid-type]
    return create_model(f"{template.id}_extraction", **header)  # type: ignore[call-overload]


def document_prompt(document: Mapping[str, Any]) -> str:
    """The message the agent is run with: the document's text, and nothing the resolver
    knows. Attachments are read by the Screen stage once; the agent gets the text."""
    text = (document.get("document") or {}).get("text") or ""
    message = document.get("message") or {}
    return (
        f"Sender: {message.get('sender', '')}\nSubject: {message.get('subject', '')}\n"
        f"Attachment: {(document.get('document') or {}).get('filename', '')}\n\n"
        f"Document text:\n{text}"
    )


def extract_with_agent(agent: Any, document: Mapping[str, Any]) -> Dict[str, Any]:
    """Run one document through the extractor agent; the structured output as a dict."""
    response = agent.run(document_prompt(document))
    content = response.content
    if isinstance(content, BaseModel):
        return content.model_dump()
    if isinstance(content, Mapping):
        return dict(content)
    raise ValueError(f"extractor returned {type(content).__name__}, not the schema")


async def aextract_with_agent(
    agent: Any, document: Mapping[str, Any]
) -> Dict[str, Any]:
    response = await agent.arun(document_prompt(document))
    content = response.content
    if isinstance(content, BaseModel):
        return content.model_dump()
    if isinstance(content, Mapping):
        return dict(content)
    raise ValueError(f"extractor returned {type(content).__name__}, not the schema")
