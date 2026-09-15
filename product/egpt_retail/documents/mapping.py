"""Target mappings: resolved fields onto a target system's objects (K15's Build record stage).

One mapping per ``(target system, target objects)`` the platform knows how to write, keyed
the way the interface's ``TARGET_OBJECTS`` names them. A mapping is a pure function from a
resolved document to a record tree -- a parent object with its fields and its child objects
-- plus the list of required fields it could not fill. A required field the mapping cannot
fill is an exception on the document, not a blank on the record.

The record tree is what ``write_to_target`` walks and ``record_rollback_point`` reverses:
create the parent, then each child with the parent's new id in ``parent_field``; roll back by
deleting the parent (Salesforce cascades ``OrderItem`` with its ``Order``).

Only ``salesforce/order_orderitem`` exists today. ``opportunity`` is listed in the interface
as the second Salesforce target and lands here as a second builder when a use case needs it.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from egpt_retail.documents.guardrails import parse_date

__all__ = ["TARGET_MAPPINGS", "build_record_tree", "RecordBuilder"]

RecordBuilder = Callable[
    [Mapping[str, Any], Mapping[str, Any]], Tuple[Dict[str, Any], List[str]]
]


def _status(created_as: Any) -> str:
    value = str(created_as or "draft").strip().lower()
    return "Activated" if value in {"active", "activated", "live"} else "Draft"


def _order_orderitem(
    document: Mapping[str, Any], options: Mapping[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    extracted: Mapping[str, Any] = document.get("extracted") or {}
    resolved: Mapping[str, Any] = document.get("resolved") or {}
    customer: Mapping[str, Any] = resolved.get("customer") or {}
    ship_to: Mapping[str, Any] = resolved.get("ship_to") or {}
    missing: List[str] = []

    if not customer.get("resolved"):
        missing.append("Order.AccountId (customer unresolved)")
    po_number = extracted.get("po_number")
    if not po_number:
        missing.append("Order.PoNumber (po_number)")
    pricebook_id = options.get("pricebook_id")
    if not pricebook_id:
        missing.append("Order.Pricebook2Id (standard pricebook id not loaded)")

    today = date.today()
    po_date = parse_date(extracted.get("po_date")) or today
    due = parse_date(extracted.get("due_date"))
    site: Mapping[str, Any] = ship_to.get("row") or {}
    order: Dict[str, Any] = {
        "AccountId": customer.get("id"),
        "PoNumber": po_number,
        "PoDate": po_date.isoformat(),
        "EffectiveDate": max(po_date, today).isoformat(),
        "Status": "Draft",
        "Pricebook2Id": pricebook_id,
        "Description": f"Created by Enterprise GPT from {extracted.get('customer_name') or 'customer'} "
        f"PO {po_number or '?'}"
        + (
            f"; delivery: {extracted['delivery_instructions']}"
            if extracted.get("delivery_instructions")
            else ""
        ),
    }
    if site:
        order.update(
            {
                "ShippingStreet": site.get("street"),
                "ShippingCity": site.get("city"),
                "ShippingState": site.get("state_name") or site.get("state"),
                "ShippingPostalCode": site.get("postcode"),
                "ShippingCountry": site.get("country") or "United States",
            }
        )
    elif not ship_to.get("resolved"):
        missing.append("Order.Shipping* (ship-to unresolved)")

    children: List[Dict[str, Any]] = []
    for line in resolved.get("lines") or []:
        product: Mapping[str, Any] = line.get("product") or {}
        if not product.get("resolved"):
            missing.append(f"OrderItem line {line.get('line_no')}: product unresolved")
            continue
        entry = product.get("pricebook_entry_id")
        if not entry:
            missing.append(
                f"OrderItem line {line.get('line_no')}: product has no pricebook entry"
            )
        quantity = line.get("quantity")
        if quantity is None:
            missing.append(f"OrderItem line {line.get('line_no')}: quantity")
        unit_price = line.get("unit_price")
        if unit_price is None:
            unit_price = product.get("unit_price")
        if unit_price is None:
            missing.append(
                f"OrderItem line {line.get('line_no')}: unit price (none on document or product)"
            )
        line_due = parse_date(line.get("line_due_date")) or due
        item: Dict[str, Any] = {
            "PricebookEntryId": entry,
            "Quantity": quantity,
            "UnitPrice": unit_price,
        }
        if line_due is not None:
            item["ServiceDate"] = line_due.isoformat()
        if line.get("description"):
            item["Description"] = str(line["description"])[:255]
        children.append(
            {
                "object": "OrderItem",
                "parent_field": "OrderId",
                "fields": item,
                "line_no": line.get("line_no"),
            }
        )
    if not children and not any(m.startswith("OrderItem") for m in missing):
        missing.append("OrderItem: no lines")

    tree: Dict[str, Any] = {
        "target": "salesforce",
        "objects": "order_orderitem",
        "object": "Order",
        "fields": order,
        "children": children,
        #: How the write finishes: an Activated order is created Draft and then activated,
        #: because Salesforce refuses OrderItem inserts on an already-active Order.
        "finish_status": _status(options.get("created_as")),
        "rollback": {"object": "Order", "action": "delete"},
    }
    return tree, missing


TARGET_MAPPINGS: Mapping[Tuple[str, str], RecordBuilder] = {
    ("salesforce", "order_orderitem"): _order_orderitem,
}


def build_record_tree(
    target: str,
    objects: str,
    document: Mapping[str, Any],
    options: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """The record tree for one document, and every required field it could not fill."""
    key = (
        str(target or "").strip().lower(),
        str(objects or "").strip().lower().replace("+", "_").replace(" ", "_"),
    )
    builder = TARGET_MAPPINGS.get(key)
    if builder is None:
        raise KeyError(f"no mapping for target {key[0]!r} objects {key[1]!r}")
    return builder(document, options or {})
