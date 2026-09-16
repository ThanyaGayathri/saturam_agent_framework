"""Golden cases (K19): documents with the outcome and values a person expects, generated or
supplied, re-run whenever the use case changes.

Three ways a rehearsal gets its documents, and this module serves two of them:

* ``generate_cases`` -- the platform invents documents from the template's own field list
  and the connected master data: a few clean ones, one per enabled guardrail that trips it,
  one the resolver cannot place, one scanned copy. Each carries what it expects: the outcome
  class and the header values it wrote. The purchase-order template keeps its curated
  sixteen (``documents/samples.py``); every other template gets this generator, which knows
  nothing about any document kind and fills fields by what their names say.
* ``score_case`` -- a case against what a run did with it: outcome first (created, or which
  exception), then each expected header value against what was extracted, then the line
  count. ``pass`` only when everything expected matched; a case with nothing expected is
  ``untested`` and never counts either way.

The mailbox is the third way and needs nothing here: the person ticks real messages.
"""

from __future__ import annotations

import base64
import re
from datetime import date, timedelta
from statistics import median
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence

from egpt_retail.documents.guardrails import Rule
from egpt_retail.documents.pdf import image_only_pdf, text_pdf
from egpt_retail.documents.templates import ExtractionTemplate

__all__ = ["generate_cases", "score_case", "case_to_message", "GENERATED_CASE_SOURCE"]

GENERATED_CASE_SOURCE = "generated"

_REFERENCE = re.compile(r"(number|reference|ref|no)$")
_DOC_DATE = re.compile(r"^(po|invoice|order|note|document|issue)_date$|^date$")
_FUTURE_DATE = re.compile(r"(due|deliver|requested|deadline|expected|ship)")
_REASON = re.compile(r"(reason|notes|comments|remarks|issue|complaint)")
_NAME = re.compile(r"(customer|supplier|vendor|buyer|account)_name$|^name$")
_ADDRESS = re.compile(r"address")
_EMAIL = re.compile(r"email")
_TOTAL = re.compile(r"^(order_)?total$|_total$|^amount$|^grand_total$")
_CURRENCY = re.compile(r"currency")
_TERMS = re.compile(r"terms")
_INSTRUCTIONS = re.compile(r"instruction|note|comment|remark")
_QUANTITY = re.compile(r"^(quantity|qty|units?)$")
_UOM = re.compile(r"unit_of_measure|uom|unit$")
_PRICE = re.compile(r"price|rate")
_LINE_TOTAL = re.compile(r"^(line_total|line_amount|amount|extended)$")
_ITEM = re.compile(r"^(description|product|item|sku|material)$")
_PART = re.compile(r"part|code|sku")
_LINE_NO = re.compile(r"^line_?no$|^line$|^no$")


def _prefix(template: ExtractionTemplate) -> str:
    return "".join(w[0] for w in template.label.split()[:2]).upper() or "DOC"


def _customer_context(master: Mapping[str, Any], index: int) -> Dict[str, Any]:
    customers = list(master.get("customer") or [])
    contacts = list(master.get("contact") or [])
    sites = list(master.get("ship_to") or [])
    if not customers:
        return {
            "id": None,
            "name": "Sample Customer Ltd",
            "domain": "sample-customer.example",
            "sender": "buyer@sample-customer.example",
            "site": None,
        }
    customer = customers[index % len(customers)]
    contact = next(
        (c for c in contacts if c.get("account_id") == customer.get("id")), None
    )
    site = next((s for s in sites if s.get("account_id") == customer.get("id")), None)
    domain = str(customer.get("email_domain") or "customer.example")
    return {
        "id": customer.get("id"),
        "name": str(customer.get("name") or "Customer"),
        "domain": domain,
        "sender": str(contact.get("email")) if contact else f"orders@{domain}",
        "site": site,
    }


def _products(master: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(p) for p in master.get("product") or [] if p.get("active", True)]


def _history_median(
    master: Mapping[str, Any], account_id: Any, product_id: Any
) -> Optional[float]:
    quantities = [
        float(h["quantity"])
        for h in master.get("order_history") or []
        if h.get("account_id") == account_id
        and h.get("product_id") == product_id
        and h.get("quantity") is not None
    ]
    return median(quantities) if quantities else None


def _ordered_before(master: Mapping[str, Any], account_id: Any) -> set:
    return {
        h.get("product_id")
        for h in master.get("order_history") or []
        if h.get("account_id") == account_id
    }


def _header_value(
    name: str,
    ctx: Mapping[str, Any],
    *,
    reference: str,
    today: date,
    total: float,
    twist: Mapping[str, Any],
) -> Any:
    if name in twist:
        return twist[name]
    if _REFERENCE.search(name) and not name.startswith("line"):
        return reference
    if _DOC_DATE.search(name):
        return (today - timedelta(days=1)).isoformat()
    if _FUTURE_DATE.search(name) and "date" in name:
        return (today + timedelta(days=12)).isoformat()
    if _NAME.search(name):
        return ctx["name"]
    if _EMAIL.search(name):
        return ctx["sender"]
    if _ADDRESS.search(name):
        site = ctx.get("site")
        return (
            f"{site.get('street')}, {site.get('city')}, {site.get('state')} {site.get('postcode')}"
            if site
            else "1 Sample Street, Springfield, MA 01104"
        )
    if _TOTAL.search(name):
        return round(total, 2)
    if _CURRENCY.search(name):
        return "USD"
    if _TERMS.search(name):
        return "Net 30"
    if _INSTRUCTIONS.search(name):
        return "Deliver to the receiving dock, weekdays 8-4."
    if _REASON.search(name):
        return "Two drums arrived dented; contents unusable."
    if "date" in name:
        return today.isoformat()
    return None


def _line_values(
    template: ExtractionTemplate,
    index: int,
    product: Mapping[str, Any],
    quantity: float,
    twist: Mapping[str, Any],
) -> Dict[str, Any]:
    price = float(product.get("unit_price") or 10.0)
    out: Dict[str, Any] = {}
    for name, _ in template.lines:
        if name in twist:
            out[name] = twist[name]
        elif _LINE_NO.match(name):
            out[name] = index
        elif _ITEM.match(name):
            out[name] = product.get("name")
        elif _QUANTITY.match(name):
            out[name] = quantity
        elif _UOM.search(name):
            out[name] = product.get("uom") or "EA"
        elif _PRICE.search(name) and not _LINE_TOTAL.match(name):
            out[name] = price
        elif _LINE_TOTAL.match(name):
            out[name] = round(price * quantity, 2)
        elif _PART.search(name):
            out[name] = (
                product.get("code") if "supplier" in name or "product" in name else None
            )
        else:
            out[name] = None
    return out


def _render(
    template: ExtractionTemplate,
    header: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
) -> List[str]:
    text = [template.label.upper(), ""]
    for name, _ in template.header:
        value = header.get(name)
        if value is not None:
            text.append(f"{name.replace('_', ' ').title()}: {value}")
    text.append("")
    columns = [n for n, _ in template.lines]
    text.append("  ".join(f"{c.replace('_', ' ').title():<18}" for c in columns))
    for line in lines:
        text.append(
            "  ".join(
                f"{('' if line.get(c) is None else line.get(c))!s:<18}" for c in columns
            )
        )
    return text


def generate_cases(
    template: ExtractionTemplate,
    master: Mapping[str, Any],
    rules: Sequence[Rule],
    *,
    today: Optional[date] = None,
    clean: int = 3,
    roles: Optional[Collection[str]] = None,
) -> List[Dict[str, Any]]:
    """Cases invented from the template's fields and the master data: ``clean`` clean ones,
    one per enabled rule kind that trips it, one unplaceable item (when the use case
    resolves products -- ``roles``, all of them when not given), one scanned copy."""
    today = today or date.today()
    products = _products(master) or [
        {
            "id": None,
            "name": "Sample item",
            "uom": "EA",
            "unit_price": 10.0,
            "code": "SKU-1",
        }
    ]
    kinds = {r.kind: r for r in rules}
    prefix = _prefix(template)
    cases: List[Dict[str, Any]] = []
    counter = 0

    def make(
        label: str,
        ctx: Mapping[str, Any],
        expected_outcome: str,
        *,
        header_twist: Optional[Mapping[str, Any]] = None,
        line_twist: Optional[Mapping[str, Any]] = None,
        product: Optional[Mapping[str, Any]] = None,
        quantity: Optional[float] = None,
        reference: Optional[str] = None,
        scanned: bool = False,
        sender: Optional[str] = None,
    ) -> Dict[str, Any]:
        nonlocal counter
        counter += 1
        chosen = product or products[counter % len(products)]
        qty = (
            quantity
            if quantity is not None
            else (_history_median(master, ctx["id"], chosen.get("id")) or 5)
        )
        ref = reference or f"{prefix}-{1000 + counter}"
        lines = [_line_values(template, 1, chosen, qty, line_twist or {})]
        total = (
            sum(
                float(line.get(n) or 0)
                for line in lines
                for n, _ in template.lines
                if _LINE_TOTAL.match(n)
            )
            or float(chosen.get("unit_price") or 10.0) * qty
        )
        # The document's own reference goes to its first reference-like header; any other
        # (an order reference on a return, a PO reference on an invoice) gets its own.
        reference_field = next(
            (
                n
                for n, _ in template.header
                if _REFERENCE.search(n) and not n.startswith("line")
            ),
            None,
        )
        header = {
            name: _header_value(
                name,
                ctx,
                reference=ref if name == reference_field else f"ORD-{2000 + counter}",
                today=today,
                total=total,
                twist=header_twist or {},
            )
            for name, _ in template.header
        }
        data = (
            image_only_pdf() if scanned else text_pdf(_render(template, header, lines))
        )
        expected_header = {
            k: v
            for k, v in header.items()
            if v is not None
            and (
                _REFERENCE.search(k)
                or _NAME.search(k)
                or "date" in k
                or _TOTAL.search(k)
            )
        }
        return {
            "label": label,
            "source": GENERATED_CASE_SOURCE,
            "sender": sender or ctx["sender"],
            "subject": f"{template.label} {ref}",
            "filename": f"{ref}.pdf",
            "document_b64": base64.b64encode(data).decode("ascii"),
            "expected": {
                "outcome": expected_outcome,
                "extracted": {} if scanned else expected_header,
                "line_count": 0 if scanned else len(lines),
            },
        }

    for i in range(clean):
        ctx = _customer_context(master, i)
        known = (
            [
                p
                for p in products
                if p.get("id") in _ordered_before(master, ctx["id"])
                and not p.get("restricted")
            ]
            or [p for p in products if not p.get("restricted")]
            or products
        )
        cases.append(
            make(
                f"Clean {i + 1}: {ctx['name']}",
                ctx,
                "created",
                product=known[i % len(known)],
            )
        )

    ctx = _customer_context(master, 0)
    if "sender_known" in kinds:
        cases.append(
            make(
                "Unknown sender",
                {
                    **ctx,
                    "name": "Riverside Trading",
                    "sender": "buyer@riverside-trading.example",
                    "id": None,
                    "site": None,
                },
                kinds["sender_known"].exception_class,
                sender="buyer@riverside-trading.example",
            )
        )
    if "duplicate_reference" in kinds:
        rule = kinds["duplicate_reference"]
        known = [
            p for p in products if p.get("id") in _ordered_before(master, ctx["id"])
        ] or products
        first = make(
            "Duplicate, first copy",
            ctx,
            "created",
            product=known[0],
            reference=f"{prefix}-DUP-1",
        )
        second = make(
            "Duplicate, resent",
            ctx,
            rule.exception_class,
            product=known[0],
            reference=f"{prefix}-DUP-1",
        )
        cases += [first, second]
    if "first_time_item" in kinds:
        never = [
            p
            for p in products
            if p.get("id") not in _ordered_before(master, ctx["id"])
            and not p.get("restricted")
        ]
        if never:
            cases.append(
                make(
                    "First-time item",
                    ctx,
                    kinds["first_time_item"].exception_class,
                    product=never[0],
                )
            )
    if "quantity_outlier" in kinds:
        known = [p for p in products if _history_median(master, ctx["id"], p.get("id"))]
        if known:
            med = _history_median(master, ctx["id"], known[0].get("id")) or 5
            cases.append(
                make(
                    "Quantity anomaly",
                    ctx,
                    kinds["quantity_outlier"].exception_class,
                    product=known[0],
                    quantity=med * 10,
                )
            )
    if "date_window" in kinds:
        rule = kinds["date_window"]
        cases.append(
            make(
                "Date in the past",
                ctx,
                rule.exception_class,
                header_twist={
                    rule.field or "due_date": (today - timedelta(days=20)).isoformat()
                },
            )
        )
    if "flagged_item" in kinds:
        restricted = [p for p in products if p.get("restricted")]
        if restricted:
            cases.append(
                make(
                    "Restricted item",
                    ctx,
                    kinds["flagged_item"].exception_class,
                    product=restricted[0],
                )
            )
    if "total_matches_lines" in kinds:
        rule = kinds["total_matches_lines"]
        cases.append(
            make(
                "Total does not add up",
                ctx,
                rule.exception_class,
                header_twist={rule.field or "total": 999999.0},
            )
        )
    if "numeric_range" in kinds:
        rule = kinds["numeric_range"]
        cases.append(
            make(
                "Out of range",
                ctx,
                rule.exception_class,
                header_twist={
                    rule.field or "total": float(rule.params.get("max", 1_000_000)) * 10
                },
            )
        )
    item_field = next((n for n, _ in template.lines if _ITEM.match(n)), None)
    if item_field and (roles is None or "product" in roles):
        cases.append(
            make(
                "Item nobody sells",
                ctx,
                "PRODUCT_NOT_FOUND",
                line_twist={item_field: "Miscellaneous sundries, assorted"},
            )
        )
    cases.append(make("Scanned copy", ctx, "SCANNED_DOCUMENT", scanned=True))
    return cases


def case_to_message(case: Mapping[str, Any]) -> Dict[str, Any]:
    """A stored case as the message dict ``receive_messages`` accepts as injected input."""
    data = base64.b64decode(case["document_b64"])
    return {
        "id": f"<{case['id']}@cases>",
        "sender": case.get("sender") or "",
        "subject": case.get("subject") or case.get("label") or "",
        "received_at": "",
        "body_text": "",
        "attachments": [
            {
                "filename": case.get("filename") or "document.pdf",
                "content_type": "application/pdf",
                "size": len(data),
                "data": data,
            }
        ],
    }


def _same(expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual is None
    try:
        e, a = float(expected), float(actual)
        return abs(e - a) <= max(0.01, 0.01 * abs(e))
    except (TypeError, ValueError):
        pass
    return (
        re.sub(r"\s+", " ", str(expected)).strip().lower()
        == re.sub(r"\s+", " ", str(actual or "")).strip().lower()
    )


def score_case(
    case: Mapping[str, Any],
    *,
    document: Optional[Mapping[str, Any]],
    exception: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """The verdict on one case after a run: what it did, what was expected, and every check."""
    expected: Mapping[str, Any] = case.get("expected") or {}
    outcome = (
        str(exception.get("class"))
        if exception
        else ("created" if document else "not_run")
    )
    checks: List[Dict[str, Any]] = []
    if not expected:
        return {
            "status": "untested",
            "outcome": outcome,
            "expected_outcome": None,
            "checks": checks,
            "detail": (exception or {}).get("detail"),
        }
    wanted = str(expected.get("outcome") or "created")
    checks.append(
        {
            "name": "outcome",
            "passed": outcome == wanted,
            "detail": f"expected {wanted}, got {outcome}",
        }
    )
    # A document that raised an exception left the batch with its extraction on the
    # exception row only; the outcome is the check there, and the values are checked on
    # the cases that went through.
    extracted = (document or {}).get("extracted") or {}
    for key, value in (expected.get("extracted") or {}).items():
        if document is None:
            break
        checks.append(
            {
                "name": f"extracted.{key}",
                "passed": _same(value, extracted.get(key)),
                "detail": f"expected {value!r}, got {extracted.get(key)!r}",
            }
        )
    if expected.get("line_count") is not None and document is not None:
        got = len(extracted.get("lines") or [])
        checks.append(
            {
                "name": "line_count",
                "passed": got == int(expected["line_count"]),
                "detail": f"expected {expected['line_count']}, got {got}",
            }
        )
    return {
        "status": "pass" if all(c["passed"] for c in checks) else "fail",
        "outcome": outcome,
        "expected_outcome": wanted,
        "checks": checks,
        "detail": (exception or {}).get("detail"),
    }
