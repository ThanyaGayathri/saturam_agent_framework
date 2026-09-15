"""Record mappings as data (K15, generalised in K18): the resolved document onto the target's
objects.

A mapping is a parent object with its field rows, an optional child object repeated per line,
how the record is finished (draft or activated), and how it is rolled back. Every field row
says where its value comes from -- a *source expression* over the resolved document -- and
whether the record can be created without it. The interpreter (``apply_mapping``) evaluates
the rows; nothing in it knows what an order is. The two Salesforce mappings that ship are rows
of this shape, and a mapping the form assembles over the target's live object metadata is
the same shape and runs the same interpreter.

Source expressions, in the order a person would write them on the form:

    po_number                   a header field the extractor filled
    extracted.po_number         the same, spelled out
    line.quantity               a line field (child rows only)
    customer.id / customer.name the resolved customer row
    ship_to.street ...          the resolved ship-to site row (street, city, state, postcode, country)
    product.pricebook_entry_id  the resolved product row on the line (id, name, code, unit_price, ...)
    $pricebook_id, $today       run-level values
    =Draft                      a literal

A row may list several sources (``["line.unit_price", "product.unit_price"]``): the first
non-empty wins. ``transform`` is one of ``date`` (parse to ISO), ``date_not_before_today``,
``text:255`` (truncate), ``number``. A required row the mapping cannot fill is reported by
object and target field -- an exception on the document, never a blank on the record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt_retail.documents.guardrails import parse_date

__all__ = [
    "FieldRow",
    "ChildMapping",
    "RecordMapping",
    "SHIPPED_MAPPINGS",
    "mapping_from",
    "mapping_to_dict",
    "apply_mapping",
    "build_record_tree",
    "suggest_mapping",
]


@dataclass(frozen=True)
class FieldRow:
    target: str
    #: One or more source expressions; the first that yields a value wins.
    source: Tuple[str, ...]
    required: bool = False
    transform: Optional[str] = None
    #: What the interface shows beside the row.
    label: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "source": list(self.source),
            "required": self.required,
            "transform": self.transform,
            "label": self.label,
        }


@dataclass(frozen=True)
class ChildMapping:
    object: str
    parent_field: str
    fields: Tuple[FieldRow, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "object": self.object,
            "parent_field": self.parent_field,
            "fields": [f.as_dict() for f in self.fields],
        }


@dataclass(frozen=True)
class RecordMapping:
    id: str
    label: str
    target: str
    object: str
    fields: Tuple[FieldRow, ...]
    child: Optional[ChildMapping] = None
    #: The status field and the values ``created as`` maps onto; empty when the object has none.
    status_field: Optional[str] = None
    status_values: Mapping[str, str] = dc_field(
        default_factory=lambda: {"draft": "Draft", "active": "Activated"}
    )
    #: How a created record is undone.
    rollback_action: str = "delete"
    #: The kind of record the mapping creates (``order``, ``opportunity``): what a template's
    #: ``record`` is matched against when suggesting.
    record: str = ""
    source: str = "authored"

    def as_dict(self) -> Dict[str, Any]:
        return mapping_to_dict(self)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")


def _row_from(raw: Any) -> Optional[FieldRow]:
    if isinstance(raw, str):
        # "AccountId ← customer.id" on the form, or "Target=source".
        target, _, source = (
            re.split(r"\s*(<-|←|=)\s*", raw, maxsplit=1)
            if re.search(r"<-|←|=", raw)
            else (raw, "", "")
        )
        return (
            FieldRow(
                target.strip(),
                tuple(s.strip() for s in source.split("|") if s.strip()),
                False,
            )
            if target.strip()
            else None
        )
    if not isinstance(raw, Mapping) or not raw.get("target"):
        return None
    source = raw.get("source")
    if isinstance(source, str):
        sources: Tuple[str, ...] = tuple(
            s.strip() for s in source.split("|") if s.strip()
        )
    elif isinstance(source, (list, tuple)):
        sources = tuple(str(s).strip() for s in source if str(s).strip())
    else:
        sources = ()
    return FieldRow(
        str(raw["target"]).strip(),
        sources,
        bool(raw.get("required", False)),
        str(raw["transform"]) if raw.get("transform") else None,
        str(raw.get("label") or ""),
    )


def mapping_from(value: Any) -> RecordMapping:
    """The mapping a slot value names or carries: a shipped id, or the object the form built."""
    if isinstance(value, RecordMapping):
        return value
    if isinstance(value, Mapping) and (value.get("fields") or value.get("object")):
        label = str(value.get("label") or value.get("object") or "Mapping")
        identifier = _slug(str(value.get("id") or label))
        fields = tuple(
            r
            for r in (_row_from(x) for x in value.get("fields") or [])
            if r is not None
        )
        child_raw = value.get("child")
        child = None
        if isinstance(child_raw, Mapping) and child_raw.get("object"):
            child = ChildMapping(
                str(child_raw["object"]),
                str(child_raw.get("parent_field") or f"{value.get('object')}Id"),
                tuple(
                    r
                    for r in (_row_from(x) for x in child_raw.get("fields") or [])
                    if r is not None
                ),
            )
        if not fields:
            shipped = SHIPPED_MAPPINGS.get(identifier)
            if shipped is not None:
                return shipped
            raise ValueError(f"mapping {label!r} has no field rows")
        status_values = value.get("status_values")
        return RecordMapping(
            identifier,
            label,
            str(value.get("target") or "salesforce").lower(),
            str(value.get("object") or ""),
            fields,
            child,
            str(value["status_field"]) if value.get("status_field") else None,
            dict(status_values)
            if isinstance(status_values, Mapping)
            else {"draft": "Draft", "active": "Activated"},
            str(value.get("rollback_action") or "delete"),
            str(value.get("record") or ""),
            str(value.get("source") or "authored"),
        )
    key = _slug(str(value or "").replace("+", "_"))
    for mapping in SHIPPED_MAPPINGS.values():
        if key in (
            mapping.id,
            _slug(mapping.label),
            mapping.id.replace("salesforce_", ""),
        ):
            return mapping
    raise KeyError(f"no mapping named {value!r}")


def mapping_to_dict(mapping: RecordMapping) -> Dict[str, Any]:
    return {
        "id": mapping.id,
        "label": mapping.label,
        "target": mapping.target,
        "object": mapping.object,
        "record": mapping.record,
        "source": mapping.source,
        "fields": [f.as_dict() for f in mapping.fields],
        "child": mapping.child.as_dict() if mapping.child else None,
        "status_field": mapping.status_field,
        "status_values": dict(mapping.status_values),
        "rollback_action": mapping.rollback_action,
    }


def _rows(*specs: Tuple[Any, ...]) -> Tuple[FieldRow, ...]:
    out = []
    for spec in specs:
        target, source, required = spec[0], spec[1], spec[2]
        transform = spec[3] if len(spec) > 3 else None
        label = spec[4] if len(spec) > 4 else ""
        out.append(
            FieldRow(
                target,
                tuple(source) if isinstance(source, (list, tuple)) else (source,),
                required,
                transform,
                label,
            )
        )
    return tuple(out)


SHIPPED_MAPPINGS: Mapping[str, RecordMapping] = MappingProxyType(
    {
        m.id: m
        for m in (
            RecordMapping(
                "order_orderitem",
                "Order + OrderItem (standard)",
                "salesforce",
                "Order",
                _rows(
                    ("AccountId", "customer.id", True, None, "the resolved customer"),
                    ("PoNumber", "po_number", True),
                    ("PoDate", ["po_date", "$today"], False, "date"),
                    (
                        "EffectiveDate",
                        ["po_date", "$today"],
                        True,
                        "date_not_before_today",
                    ),
                    (
                        "Pricebook2Id",
                        "$pricebook_id",
                        True,
                        None,
                        "the standard pricebook",
                    ),
                    (
                        "ShippingStreet",
                        "ship_to.street",
                        True,
                        None,
                        "the resolved ship-to",
                    ),
                    ("ShippingCity", "ship_to.city", False),
                    ("ShippingState", ["ship_to.state_name", "ship_to.state"], False),
                    ("ShippingPostalCode", "ship_to.postcode", False),
                    ("ShippingCountry", ["ship_to.country", "=United States"], False),
                    ("Description", "delivery_instructions", False, "text:255"),
                ),
                ChildMapping(
                    "OrderItem",
                    "OrderId",
                    _rows(
                        (
                            "PricebookEntryId",
                            "product.pricebook_entry_id",
                            True,
                            None,
                            "the resolved product's price entry",
                        ),
                        ("Quantity", "line.quantity", True, "number"),
                        (
                            "UnitPrice",
                            ["line.unit_price", "product.unit_price"],
                            True,
                            "number",
                        ),
                        (
                            "ServiceDate",
                            ["line.line_due_date", "due_date"],
                            False,
                            "date",
                        ),
                        ("Description", "line.description", False, "text:255"),
                    ),
                ),
                status_field="Status",
                record="order",
                source="shipped",
            ),
            RecordMapping(
                "opportunity",
                "Opportunity + Products",
                "salesforce",
                "Opportunity",
                _rows(
                    ("AccountId", "customer.id", True, None, "the resolved customer"),
                    ("Name", ["po_number", "customer_name"], True, "text:120"),
                    (
                        "CloseDate",
                        ["due_date", "$today"],
                        True,
                        "date_not_before_today",
                    ),
                    ("StageName", "=Prospecting", True),
                    (
                        "Pricebook2Id",
                        "$pricebook_id",
                        True,
                        None,
                        "the standard pricebook",
                    ),
                    ("Description", "delivery_instructions", False, "text:255"),
                ),
                ChildMapping(
                    "OpportunityLineItem",
                    "OpportunityId",
                    _rows(
                        ("PricebookEntryId", "product.pricebook_entry_id", True),
                        ("Quantity", "line.quantity", True, "number"),
                        (
                            "UnitPrice",
                            ["line.unit_price", "product.unit_price"],
                            True,
                            "number",
                        ),
                    ),
                ),
                status_field=None,
                record="opportunity",
                source="shipped",
            ),
        )
    }
)


# --- the interpreter ---------------------------------------------------------------------------


def _lookup(path: str, context: Mapping[str, Any]) -> Any:
    if path.startswith("="):
        return path[1:]
    if path.startswith("$"):
        return context.get("$", {}).get(path[1:])
    head, _, rest = path.partition(".")
    if not rest:
        return (context.get("extracted") or {}).get(head)
    scope = context.get(head)
    if not isinstance(scope, Mapping):
        return None
    return scope.get(rest)


def _transform(value: Any, transform: Optional[str], today: date) -> Any:
    if value is None or value == "":
        return None
    if not transform:
        return value
    if transform == "date":
        parsed = parse_date(value)
        return parsed.isoformat() if parsed else None
    if transform == "date_not_before_today":
        parsed = parse_date(value) or today
        return max(parsed, today).isoformat()
    if transform == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if transform.startswith("text:"):
        return str(value)[: int(transform.split(":", 1)[1])]
    return value


def _fill(
    rows: Sequence[FieldRow],
    context: Mapping[str, Any],
    *,
    object_name: str,
    prefix: str,
    today: date,
) -> Tuple[Dict[str, Any], List[str]]:
    fields: Dict[str, Any] = {}
    missing: List[str] = []
    for row in rows:
        value = None
        for source in row.source:
            value = _transform(_lookup(source, context), row.transform, today)
            if value is not None:
                break
        if value is None:
            if row.required:
                why = row.label or " or ".join(row.source) or "no source"
                missing.append(f"{prefix}{object_name}.{row.target} ({why})")
            continue
        fields[row.target] = value
    return fields, missing


def apply_mapping(
    mapping: RecordMapping,
    document: Mapping[str, Any],
    options: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """The record tree for one document under the mapping, and every required target field
    it could not fill."""
    options = options or {}
    today = options.get("today") or date.today()
    extracted: Mapping[str, Any] = document.get("extracted") or {}
    resolved: Mapping[str, Any] = document.get("resolved") or {}
    customer = resolved.get("customer") or {}
    ship_to = resolved.get("ship_to") or {}
    run_values = {
        "pricebook_id": options.get("pricebook_id"),
        "today": today.isoformat(),
        "created_as": options.get("created_as"),
    }
    base: Dict[str, Any] = {
        "extracted": extracted,
        "customer": customer if customer.get("resolved") else {},
        "ship_to": ship_to.get("row") or {} if ship_to.get("resolved") else {},
        "$": run_values,
    }
    fields, missing = _fill(
        mapping.fields, base, object_name=mapping.object, prefix="", today=today
    )
    finish = None
    if mapping.status_field:
        wanted = str(options.get("created_as") or "draft").strip().lower()
        finish = mapping.status_values.get(
            "active" if wanted in {"active", "activated", "live"} else "draft", "Draft"
        )
        fields.setdefault(
            mapping.status_field, mapping.status_values.get("draft", "Draft")
        )
    children: List[Dict[str, Any]] = []
    if mapping.child is not None:
        lines = resolved.get("lines") or extracted.get("lines") or []
        for line in lines:
            product = line.get("product") or {}
            context = {
                **base,
                "line": line,
                "product": product if product.get("resolved") else {},
            }
            child_fields, child_missing = _fill(
                mapping.child.fields,
                context,
                object_name=mapping.child.object,
                prefix=f"line {line.get('line_no')}: ",
                today=today,
            )
            missing.extend(child_missing)
            if not child_missing:
                children.append(
                    {
                        "object": mapping.child.object,
                        "parent_field": mapping.child.parent_field,
                        "fields": child_fields,
                        "line_no": line.get("line_no"),
                    }
                )
        if not lines:
            missing.append(f"{mapping.child.object}: no lines")
    tree: Dict[str, Any] = {
        "target": mapping.target,
        "objects": mapping.id,
        "object": mapping.object,
        "fields": fields,
        "children": children,
        "finish_status": finish,
        "status_field": mapping.status_field,
        "rollback": {"object": mapping.object, "action": mapping.rollback_action},
    }
    return tree, missing


def build_record_tree(
    target: str,
    objects: Any,
    document: Mapping[str, Any],
    options: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """The record tree for one document. ``objects`` is a shipped mapping's id or the mapping
    the form assembled; ``target`` must agree with the mapping's target."""
    mapping = mapping_from(objects)
    if target and mapping.target != str(target).lower():
        raise KeyError(
            f"mapping {mapping.id!r} writes to {mapping.target}, not {target}"
        )
    return apply_mapping(mapping, document, options)


# --- suggesting a mapping from names ------------------------------------------------------------

_SYNONYMS: Mapping[str, Tuple[str, ...]] = MappingProxyType(
    {
        "po_number": ("ponumber", "purchaseordernumber", "customerpo", "poref", "po"),
        "po_date": ("podate", "orderdate", "effectivedate"),
        "due_date": (
            "servicedate",
            "duedate",
            "requesteddate",
            "closedate",
            "deliverydate",
            "expecteddate",
        ),
        "delivery_date": ("servicedate", "deliverydate", "duedate"),
        "customer_name": ("accountname", "customer"),
        "supplier_name": ("vendorname", "suppliername", "accountname", "name"),
        "description": ("description", "name", "productname", "itemdescription"),
        "quantity": ("quantity", "qty"),
        "unit_price": ("unitprice", "price", "listprice"),
        "line_total": ("totalprice", "linetotal", "amount"),
        "total": ("amount", "totalamount", "grandtotal"),
        "order_total": ("totalamount", "amount"),
        "currency": ("currencyisocode", "currency"),
        "invoice_number": ("invoicenumber", "invoicenumber__c", "name"),
        "delivery_instructions": ("description", "shippinginstructions", "notes"),
        "reason": ("description", "reason"),
    }
)
_NORMALISE = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NORMALISE.sub("", str(text).lower().replace("__c", ""))


def suggest_mapping(
    template_fields: Mapping[str, Sequence[str]],
    parent: Mapping[str, Any],
    child: Optional[Mapping[str, Any]] = None,
    *,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """A mapping proposed from names: the template's field names against the target
    objects' field names and labels, with the reference fields a resolved document always
    carries (customer, product, pricebook) placed by type. Deterministic; the person edits.

    ``template_fields`` is ``{"header": [...names], "lines": [...names]}``; ``parent`` and
    ``child`` are ``{"name": "Order", "fields": [{"name", "label", "type", "required",
    "reference_to": [...]}]}`` as ``describe_object`` returns them.
    """
    header = list(template_fields.get("header") or [])
    lines = list(template_fields.get("lines") or [])

    def match(
        candidates: Sequence[str], target_field: Mapping[str, Any]
    ) -> Optional[str]:
        names = {
            _norm(target_field.get("name", "")),
            _norm(target_field.get("label", "")),
        }
        for candidate in candidates:
            keys = {_norm(candidate), *(_norm(s) for s in _SYNONYMS.get(candidate, ()))}
            if keys & names:
                return candidate
        return None

    def rows_for(
        obj: Mapping[str, Any], candidates: Sequence[str], *, line_scope: bool
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        used: set = set()
        for target_field in obj.get("fields") or []:
            name = str(target_field.get("name"))
            kind = str(target_field.get("type") or "").lower()
            refs = [str(r) for r in target_field.get("reference_to") or []]
            required = bool(target_field.get("required"))
            source: List[str] = []
            transform: Optional[str] = None
            row_label = ""
            if kind == "reference":
                if "Account" in refs and not line_scope:
                    source, row_label = ["customer.id"], "the resolved customer"
                elif "PricebookEntry" in refs:
                    source, row_label = (
                        ["product.pricebook_entry_id"],
                        "the resolved product's price entry",
                    )
                elif "Pricebook2" in refs:
                    source, row_label = ["$pricebook_id"], "the standard pricebook"
                elif "Product2" in refs:
                    source, row_label = ["product.id"], "the resolved product"
                elif "Contact" in refs:
                    source = []
            elif name.lower().startswith("shipping") and not line_scope:
                part = name[len("Shipping") :].lower()
                source = {
                    "street": ["ship_to.street"],
                    "city": ["ship_to.city"],
                    "state": ["ship_to.state_name", "ship_to.state"],
                    "postalcode": ["ship_to.postcode"],
                    "country": ["ship_to.country", "=United States"],
                }.get(part, [])
                row_label = "the resolved ship-to" if source else ""
            else:
                candidate = match(
                    [c for c in candidates if c not in used], target_field
                )
                if candidate:
                    used.add(candidate)
                    source = [f"line.{candidate}" if line_scope else candidate]
                    if kind == "date":
                        transform = (
                            "date_not_before_today"
                            if _norm(name) in {"effectivedate", "closedate"}
                            else "date"
                        )
                    elif kind in {"double", "currency", "int", "percent"}:
                        transform = "number"
                    elif kind in {"string", "textarea"} and target_field.get("length"):
                        transform = f"text:{int(target_field['length'])}"
                if (
                    kind == "date"
                    and not source
                    and _norm(name) in {"effectivedate", "closedate"}
                ):
                    source, transform = ["$today"], "date_not_before_today"
            if name.lower() == "status" and kind == "picklist":
                continue  # the status field is set by 'created as', not mapped
            if source or required:
                rows.append(
                    {
                        "target": name,
                        "source": source,
                        "required": required,
                        "transform": transform,
                        "label": row_label
                        or (
                            str(target_field.get("label") or "")
                            if required and not source
                            else ""
                        ),
                    }
                )
        # A line that names its price-book entry names its product through it; mapping both
        # is redundant and, when they disagree, refused by the target.
        if any(r["source"] == ["product.pricebook_entry_id"] for r in rows):
            rows = [r for r in rows if r["source"] != ["product.id"]]
        return rows

    parent_name = str(parent.get("name") or "")
    status_field = next(
        (
            str(f.get("name"))
            for f in parent.get("fields") or []
            if str(f.get("name", "")).lower() == "status"
            and str(f.get("type", "")).lower() == "picklist"
        ),
        None,
    )
    out: Dict[str, Any] = {
        "id": _slug(
            label or (f"{parent_name}_{child.get('name')}" if child else parent_name)
        ),
        "label": label
        or (f"{parent_name} + {child.get('name')}" if child else parent_name),
        "target": "salesforce",
        "object": parent_name,
        "record": _slug(parent_name),
        "source": "authored",
        "fields": rows_for(parent, header, line_scope=False),
        "child": None,
        "status_field": status_field,
        "status_values": {"draft": "Draft", "active": "Activated"},
        "rollback_action": "delete",
    }
    if child and child.get("name"):
        parent_field = next(
            (
                str(f.get("name"))
                for f in child.get("fields") or []
                if parent_name
                in (child_ref for child_ref in (f.get("reference_to") or []))
            ),
            f"{parent_name}Id",
        )
        out["child"] = {
            "object": str(child["name"]),
            "parent_field": parent_field,
            "fields": [
                r
                for r in rows_for(child, lines, line_scope=True)
                if r["target"] != parent_field
            ],
        }
    return out
