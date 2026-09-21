"""Drafting an extraction template from words and an example (K17).

The person says what arrives and what it should become, optionally with one example
document, and the extraction model proposes the field list in the template's own shape:
snake_case names, the essential ones required, a hint per field saying where on the
document it sits. The person edits and keeps it; nothing is stored until they do. The same
model tier as the Extract stage (frontier), because reading a document layout is the same
skill as filling one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from egpt_retail.documents.reader import read_document
from egpt_retail.documents.templates import _slug, template_from, template_to_dict

__all__ = [
    "TemplateDraft",
    "DraftedField",
    "draft_instructions",
    "draft_template",
    "adraft_template",
]


class DraftedField(BaseModel):
    name: str = Field(description="snake_case; what the field is, not where it is")
    required: bool = Field(
        default=False,
        description="True only when the record cannot be created without it",
    )
    hint: str = Field(
        default="",
        description="Where on this kind of document the value is usually found",
    )


class TemplateDraft(BaseModel):
    label: str = Field(
        description="The document kind, as a person names it: 'Delivery note'"
    )
    record: str = Field(
        default="",
        description="What one document becomes, one word: order, payable, receipt, return",
    )
    header: List[DraftedField] = Field(
        default_factory=list, description="Fields that appear once per document"
    )
    lines: List[DraftedField] = Field(
        default_factory=list,
        description="Fields that repeat per line; always include line_no first",
    )
    notes: str = Field(
        default="",
        description="What you were unsure about; empty when the draft is clean",
    )


def draft_instructions() -> str:
    return "\n".join(
        [
            "You draft extraction templates for a document-driven automation. A template is the",
            "list of fields an extraction step will fill from each document of one kind, and a",
            "record will be created from those fields.",
            "",
            "Rules:",
            "- Field names are snake_case nouns for what the value is (po_number, delivery_date),",
            "  never where it is (top_right_box).",
            "- Header fields appear once per document; line fields repeat per line. Always put",
            "  line_no first among the line fields.",
            "- Mark a field required only when the record cannot be created without it. Two to",
            "  five required header fields is typical.",
            "- Prefer the field names other templates use where they mean the same thing:",
            "  customer_name, supplier_name, ship_to_address, due_date, currency, total,",
            "  line_no, description, quantity, unit_of_measure, unit_price, line_total.",
            "- Use the example document, when given, to see which fields this format actually",
            "  carries; do not invent fields the example and the description do not support.",
            "- Never copy a value from the example into the template; the template holds names.",
            "- Say in notes what you could not decide. Empty notes means a clean draft.",
        ]
    )


def draft_prompt(
    description: str, *, label: Optional[str] = None, example_text: Optional[str] = None
) -> str:
    parts = [f"Description of the use case:\n{description.strip()}"]
    if label:
        parts.append(f"The person calls this document kind: {label.strip()}")
    if example_text:
        parts.append(f"Text of one example document:\n{example_text.strip()[:12000]}")
    parts.append("Draft the template.")
    return "\n\n".join(parts)


def example_text_from(
    example_document_b64: Optional[str], example_text: Optional[str]
) -> Optional[str]:
    """The example's text: the text given, else the PDF's text layer."""
    if example_text and example_text.strip():
        return example_text
    if example_document_b64:
        import base64

        data = base64.b64decode(example_document_b64)
        document = read_document(data, filename="example.pdf")
        return document.text or None
    return None


def _to_template(draft: TemplateDraft) -> Dict[str, Any]:
    raw = {
        "label": draft.label,
        "record": draft.record,
        "header": [{"name": f.name, "required": f.required} for f in draft.header],
        "lines": [{"name": f.name, "required": f.required} for f in draft.lines],
    }
    template = template_to_dict(template_from(raw))
    hints = {_slug(f.name): f.hint for f in draft.header + draft.lines if f.hint}
    template["header"] = [
        {
            "name": n.rstrip("*"),
            "required": n.endswith("*"),
            "hint": hints.get(n.rstrip("*"), ""),
        }
        for n in template["header"]
    ]
    template["lines"] = [
        {
            "name": n.rstrip("*"),
            "required": n.endswith("*"),
            "hint": hints.get(n.rstrip("*"), ""),
        }
        for n in template["lines"]
    ]
    template["source"] = "drafted"
    return template


def draft_template(
    agent: Any,
    description: str,
    *,
    label: Optional[str] = None,
    example_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the drafting agent once. ``agent`` has ``output_schema=TemplateDraft``."""
    response = agent.run(
        draft_prompt(description, label=label, example_text=example_text)
    )
    draft = response.content
    if not isinstance(draft, TemplateDraft):
        raise ValueError(
            f"drafting agent returned {type(draft).__name__}, not a TemplateDraft"
        )
    return {"template": _to_template(draft), "notes": draft.notes}


async def adraft_template(
    agent: Any,
    description: str,
    *,
    label: Optional[str] = None,
    example_text: Optional[str] = None,
) -> Dict[str, Any]:
    response = await agent.arun(
        draft_prompt(description, label=label, example_text=example_text)
    )
    draft = response.content
    if not isinstance(draft, TemplateDraft):
        raise ValueError(
            f"drafting agent returned {type(draft).__name__}, not a TemplateDraft"
        )
    return {"template": _to_template(draft), "notes": draft.notes}
