"""Master-data roles as data (K21): which roles a use case needs, and which extracted
fields feed each role's resolution.

The resolver knows five roles -- the sender's account, the sender as a contact, the
account's ship-to site, the product on a line, and the history of what the target already
holds. Before K21 every document was resolved against all of them, and the field names it
read (``customer_name``, ``ship_to_address``, ``description``) were the purchase order's.
Now a use case needs only the roles its mapping and guardrails reference
(:func:`roles_needed`), and the field each role reads comes from the template
(:func:`resolution_sources`): derived from its field names, or declared on the template
as ``resolution`` when the names are unusual. A supplier invoice from a vendor, a return
request that becomes a Case, a delivery note with no products on it -- each resolves what
it maps, nothing more.

``frontend/src/domain/use-case-roles.js`` mirrors this module rule for rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple

from egpt_retail.documents.guardrails import Rule, rules_from
from egpt_retail.documents.templates import ExtractionTemplate

__all__ = [
    "Role",
    "ROLES",
    "roles_needed",
    "resolution_sources",
    "reference_field",
    "reference_of",
]


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    #: The semantic-layer concept the interface binds it to.
    concept: str
    #: The prefix a mapping source expression uses to read the resolved row (``customer.``).
    prefix: Optional[str]
    #: Roles this one cannot be resolved without.
    depends: Tuple[str, ...]
    hint: str


ROLES: Mapping[str, Role] = MappingProxyType(
    {
        r.id: r
        for r in (
            Role(
                "customer",
                "Sender account",
                "Customer",
                "customer.",
                (),
                "The organisation the document comes from -- a customer or a supplier -- "
                "matched by sender email domain, then by name.",
            ),
            Role(
                "contact",
                "Sender contact",
                "Contact",
                "contact.",
                ("customer",),
                "The person who sent it, matched by sender email.",
            ),
            Role(
                "ship_to",
                "Ship-to site",
                "Location",
                "ship_to.",
                ("customer",),
                "The account's addresses; matched on postcode + street number, then full text.",
            ),
            Role(
                "product",
                "Product",
                "Product",
                "product.",
                (),
                "Matched by the sender's part number, then product code, then description.",
            ),
            Role(
                "order_history",
                "Record history",
                "Order",
                None,
                ("customer",),
                "What the target already holds per account; powers the history guardrails.",
            ),
        )
    }
)

_PREFIXES: Tuple[Tuple[str, str], ...] = tuple(
    (role.prefix, role.id) for role in ROLES.values() if role.prefix
)


def _sources_of(mapping: Any) -> Iterable[str]:
    fields = list(getattr(mapping, "fields", ()) or ())
    child = getattr(mapping, "child", None)
    if child is not None:
        fields += list(getattr(child, "fields", ()) or ())
    for row in fields:
        for source in getattr(row, "source", ()) or ():
            yield str(source)


def roles_needed(mapping: Any, rules: Any = None) -> Set[str]:
    """The roles a use case resolves: every role a mapping source expression reads
    (``customer.id``, ``ship_to.street``, ``product.pricebook_entry_id``; ``$pricebook_id``
    rides with the product load), every role an enabled guardrail needs, and what those
    depend on. Empty mapping and no rules: nothing is resolved."""
    needed: Set[str] = set()
    for source in _sources_of(mapping):
        for prefix, role in _PREFIXES:
            if source.startswith(prefix):
                needed.add(role)
        if source == "$pricebook_id":
            needed.add("product")
    for rule in _rules(rules):
        if rule.needs and rule.needs in ROLES:
            needed.add(rule.needs)
        if rule.kind in {"first_time_item", "quantity_outlier", "flagged_item"}:
            needed.add("product")
    changed = True
    while changed:
        changed = False
        for role in list(needed):
            for dependency in ROLES[role].depends:
                if dependency not in needed:
                    needed.add(dependency)
                    changed = True
    return needed


def _rules(value: Any) -> Sequence[Rule]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)) and all(isinstance(r, Rule) for r in value):
        return tuple(value)
    try:
        return tuple(rules_from(value))
    except (KeyError, ValueError, TypeError):
        return ()


# The field each role reads, derived from the template's names. Header names first.
_ACCOUNT_NAME = re.compile(
    r"^(customer|buyer|supplier|vendor|account|company|client|sold_to|sender|from)(_?name)?$"
)
_ACCOUNT_NAME_LOOSE = re.compile(r"(customer|supplier|vendor|account|client)_name")
_SHIP_TO = re.compile(
    r"(ship|deliver|delivery|shipping).*(address|to|site)|^(site|location)$"
)
_ITEM = re.compile(
    r"^(description|product|item|material|article|sku_description|product_description|"
    r"item_description|product_name|item_name)$"
)
_THEIR_PART = re.compile(
    r"^(customer|buyer|their|client)_?(part|item|sku|code|ref|product)"
)
_OUR_CODE = re.compile(
    r"^(supplier|our|vendor|seller)?_?(product_code|sku|part_number|part_no|part|"
    r"item_code|item_number|item_no|code|product_id|material_number|catalogue_number)$"
)
_REFERENCE = re.compile(r"(number|reference|ref|id|no)$")


def _first(names: Sequence[str], pattern: re.Pattern[str]) -> Optional[str]:
    return next((n for n in names if pattern.search(n)), None)


def resolution_sources(template: ExtractionTemplate) -> Dict[str, Any]:
    """Which extracted field feeds each role, for this template:
    ``{"customer": {"name": ...}, "ship_to": {"address": ...}, "product": {"description":
    ..., "customer_part_number": ..., "supplier_product_code": ...}, "reference": ...}``.
    Derived from the field names; a template's own ``resolution`` declaration overrides any
    entry. A role with no field still resolves from what the message carries (the sender)."""
    header = [n for n, _ in template.header]
    lines = [n for n, _ in template.lines]
    derived: Dict[str, Any] = {
        "customer": {
            "name": _first(header, _ACCOUNT_NAME) or _first(header, _ACCOUNT_NAME_LOOSE)
        },
        "ship_to": {"address": _first(header, _SHIP_TO)},
        "product": {
            "description": _first(lines, _ITEM),
            "customer_part_number": _first(lines, _THEIR_PART),
            "supplier_product_code": _first(lines, _OUR_CODE),
        },
        "reference": reference_field(template),
    }
    declared = getattr(template, "resolution", None) or {}
    for role, value in declared.items():
        if isinstance(value, Mapping) and isinstance(derived.get(role), dict):
            derived[role].update({k: v for k, v in value.items() if v})
        elif value:
            derived[role] = value
    return derived


def reference_field(template: ExtractionTemplate) -> Optional[str]:
    """The header field a document is known by (``po_number``, ``invoice_number``,
    ``rma_reference``): declared on the template, else the first number-like header."""
    declared = getattr(template, "reference", None)
    if declared:
        return str(declared)
    header = [n for n, _ in template.header]
    return next(
        (n for n in header if _REFERENCE.search(n) and not n.startswith("line")), None
    )


def reference_of(template: ExtractionTemplate, extracted: Mapping[str, Any]) -> Any:
    """The reference value on one extracted document, or None."""
    field = reference_field(template)
    return extracted.get(field) if field else None
