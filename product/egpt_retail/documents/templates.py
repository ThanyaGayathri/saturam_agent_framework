"""Extraction templates: the schema the Extract stage fills, and the validation the Resolve
stage runs before it reads anything (K15).

Mirrors ``frontend/src/domain/use-case-autonomy.js`` ``EXTRACTION_TEMPLATES`` field for
field -- the interface shows the person this list, the model is told to fill exactly this
list, and this module refuses anything else. A field the document does not show is ``None``,
never invented; a required field that is ``None`` is an exception on the document, not a
blank on the record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "ExtractionTemplate",
    "EXTRACTION_TEMPLATES",
    "template_from",
    "template_for",
    "template_to_dict",
    "validate_extraction",
    "schema_for",
]


@dataclass(frozen=True)
class ExtractionTemplate:
    id: str
    label: str
    #: The kind of record this template becomes (``order``, ``payable``, ``return``): the key
    #: a target mapping is looked up under.
    record: str
    #: Header fields, ``(name, required)``.
    header: Tuple[Tuple[str, bool], ...]
    #: Line fields, ``(name, required)``.
    lines: Tuple[Tuple[str, bool], ...]
    #: The header field a document is known by (``po_number``); None derives it from the
    #: names (``documents/roles.py``).
    reference: Optional[str] = None
    #: Which fields feed each master-data role, when the names alone would not say:
    #: ``{"customer": {"name": "vendor"}, "product": {"description": "item"}}``.
    resolution: Mapping[str, Any] = dc_field(default_factory=dict)

    @property
    def required_header(self) -> Tuple[str, ...]:
        return tuple(name for name, required in self.header if required)

    @property
    def required_lines(self) -> Tuple[str, ...]:
        return tuple(name for name, required in self.lines if required)


def _fields(*names: str) -> Tuple[Tuple[str, bool], ...]:
    """``"po_number*"`` is required, ``"po_date"`` is not -- the same star the interface uses."""
    return tuple((n.rstrip("*"), n.endswith("*")) for n in names)


EXTRACTION_TEMPLATES: Mapping[str, ExtractionTemplate] = MappingProxyType(
    {
        "purchase_order": ExtractionTemplate(
            "purchase_order",
            "Purchase order",
            "order",
            _fields(
                "po_number*",
                "po_date",
                "customer_name*",
                "buyer_name",
                "buyer_email",
                "ship_to_address*",
                "bill_to_address",
                "due_date",
                "currency",
                "payment_terms",
                "order_total",
                "delivery_instructions",
            ),
            _fields(
                "line_no*",
                "customer_part_number",
                "supplier_product_code",
                "description*",
                "quantity*",
                "unit_of_measure*",
                "unit_price",
                "line_total",
                "line_due_date",
                "line_notes",
            ),
        ),
        "supplier_invoice": ExtractionTemplate(
            "supplier_invoice",
            "Supplier invoice",
            "payable",
            _fields(
                "invoice_number*",
                "invoice_date*",
                "supplier_name*",
                "po_reference",
                "due_date",
                "currency",
                "total*",
            ),
            _fields(
                "line_no*",
                "description*",
                "quantity",
                "unit_price",
                "line_total*",
                "tax",
            ),
        ),
        "return_request": ExtractionTemplate(
            "return_request",
            "Return request",
            "return",
            _fields("rma_reference", "customer_name*", "order_reference*", "reason"),
            _fields("line_no*", "product*", "quantity*", "condition"),
        ),
    }
)


def _slug(text: str) -> str:
    return (
        re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_") or "document"
    )


def _field_tuple(raw: Any) -> Tuple[str, bool]:
    """``"po_number*"`` or ``{"name": "po_number", "required": true}`` -> ``(name, required)``."""
    if isinstance(raw, Mapping):
        return _slug(str(raw.get("name") or raw.get("id") or "")), bool(
            raw.get("required", False)
        )
    text = str(raw or "").strip()
    return _slug(text.rstrip("*")), text.endswith("*")


def template_from(value: Any) -> ExtractionTemplate:
    """The template a slot value names or carries.

    A string is an id or a label of a shipped template. A mapping is a template the person
    assembled on the form (K17): ``{id?, label, record?, header: [...], lines: [...]}`` with
    fields as ``"name*"`` strings or ``{name, required}`` objects. Nothing about a template
    is code, so a use case onboarded tomorrow with a document kind the platform has never
    seen runs the same extractor with its own field list.
    """
    if isinstance(value, ExtractionTemplate):
        return value
    if isinstance(value, Mapping):
        label = str(
            value.get("label") or value.get("name") or value.get("id") or "Document"
        )
        identifier = _slug(str(value.get("id") or label))
        header = tuple(_field_tuple(f) for f in value.get("header") or [])
        lines = tuple(_field_tuple(f) for f in value.get("lines") or [])
        header = tuple(f for f in header if f[0])
        lines = tuple(f for f in lines if f[0])
        if not header and not lines:
            shipped = EXTRACTION_TEMPLATES.get(identifier)
            if shipped is not None:
                return shipped
            raise ValueError(f"extraction template {label!r} declares no fields")
        record = str(value.get("record") or value.get("creates") or identifier)
        resolution = value.get("resolution")
        return ExtractionTemplate(
            identifier,
            label,
            record,
            header,
            lines,
            str(value["reference"]) if value.get("reference") else None,
            dict(resolution) if isinstance(resolution, Mapping) else {},
        )
    key = str(value or "").strip().lower()
    for template in EXTRACTION_TEMPLATES.values():
        if key in (template.id, template.label.lower()):
            return template
    raise KeyError(f"unknown extraction template {value!r}")


def template_for(identifier: Any) -> ExtractionTemplate:
    """Alias of :func:`template_from`, kept for the callers that pass an id."""
    return template_from(identifier)


def template_to_dict(template: ExtractionTemplate) -> Dict[str, Any]:
    return {
        "id": template.id,
        "label": template.label,
        "record": template.record,
        "header": [f"{n}{'*' if r else ''}" for n, r in template.header],
        "lines": [f"{n}{'*' if r else ''}" for n, r in template.lines],
        **({"reference": template.reference} if template.reference else {}),
        **({"resolution": dict(template.resolution)} if template.resolution else {}),
    }


def schema_for(template: ExtractionTemplate) -> Dict[str, Any]:
    """A JSON schema the extraction agent's ``output_schema`` is built from, and the
    text the step prompt shows it. Every field nullable: null is the honest answer for a
    field the document does not show."""

    def prop(name: str) -> Dict[str, Any]:
        numeric = name in {
            "quantity",
            "unit_price",
            "line_total",
            "order_total",
            "total",
            "tax",
        }
        return {"type": [("number" if numeric else "string"), "null"]}

    return {
        "type": "object",
        "properties": {
            **{name: prop(name) for name, _ in template.header},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {name: prop(name) for name, _ in template.lines},
                    "required": [name for name, _ in template.lines],
                },
            },
        },
        "required": [name for name, _ in template.header] + ["lines"],
    }


def validate_extraction(
    template: ExtractionTemplate, payload: Any
) -> Tuple[Dict[str, Any], List[str]]:
    """Coerce a model's output into the template's shape and list what is wrong with it.

    Returns ``(clean, problems)``. ``clean`` always has every header field (``None`` when
    absent) and a ``lines`` list with every line field; ``problems`` names each required
    field that is missing, each unknown field the model added (dropped, not kept -- a field
    the template does not declare has no column to land in), and each line whose quantity is
    not a positive number. An empty ``problems`` is the only pass.
    """
    problems: List[str] = []
    if not isinstance(payload, Mapping):
        return {"lines": []}, ["extraction output is not an object"]
    header_names = [name for name, _ in template.header]
    line_names = [name for name, _ in template.lines]

    clean: Dict[str, Any] = {
        name: _blank_to_none(payload.get(name)) for name in header_names
    }
    for extra in sorted(set(payload) - set(header_names) - {"lines"}):
        problems.append(f"unknown header field {extra!r} dropped")
    for name in template.required_header:
        if clean.get(name) is None:
            problems.append(f"required header field {name!r} missing")

    raw_lines = payload.get("lines")
    lines: List[Dict[str, Any]] = []
    if not isinstance(raw_lines, list) or not raw_lines:
        problems.append("no lines extracted")
        raw_lines = []
    for index, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, Mapping):
            problems.append(f"line {index} is not an object")
            continue
        line: Dict[str, Any] = {
            name: _blank_to_none(raw.get(name)) for name in line_names
        }
        if line.get("line_no") is None:
            line["line_no"] = index
        for name in template.required_lines:
            if line.get(name) is None:
                problems.append(f"line {index}: required field {name!r} missing")
        quantity = line.get("quantity")
        if quantity is not None:
            try:
                line["quantity"] = float(quantity)
            except (TypeError, ValueError):
                problems.append(f"line {index}: quantity {quantity!r} is not a number")
            else:
                if line["quantity"] <= 0:
                    problems.append(f"line {index}: quantity must be positive")
        lines.append(line)
    clean["lines"] = lines
    return clean, problems


def _blank_to_none(value: Optional[Any]) -> Optional[Any]:
    if isinstance(value, str) and not value.strip():
        return None
    return value
