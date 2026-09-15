"""The six record guardrails (K15's Apply guardrails stage, document-to-record).

Typed rows, exactly the interface's ``RECORD_GUARDRAILS``: an id, what it checks, the one
parameter it takes (or none), which master-data role it needs, and the exception class it
raises. A guardrail that needs history a deployment did not connect is reported as
``skipped`` on every document rather than silently passing -- the rehearsal report is where
that shows, and the preflight screen already warns about it.

Every check is a pure function of the document and the master data. Any failure raises an
exception on the document; nothing is written for a document with an exception. There is no
severity and no override: the person who set the guardrail on the form gets the exception in
their mailbox and decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Guardrail",
    "RECORD_GUARDRAILS",
    "guardrails_from",
    "check_document",
    "parse_date",
]


@dataclass(frozen=True)
class Guardrail:
    id: str
    label: str
    check: str
    param_key: Optional[str]
    param_default: Optional[float]
    needs: Optional[str]
    exception: str


RECORD_GUARDRAILS: Mapping[str, Guardrail] = MappingProxyType(
    {
        g.id: g
        for g in (
            Guardrail(
                "duplicate_po",
                "Duplicate PO",
                "An order for this customer with the same PO number already exists",
                "lookbackMonths",
                12,
                "order_history",
                "DUPLICATE_PO",
            ),
            Guardrail(
                "unknown_sender",
                "Unknown sender",
                "The sender's email domain matches no customer or contact",
                None,
                None,
                "customer",
                "UNKNOWN_SENDER",
            ),
            Guardrail(
                "first_time_product",
                "First-time product",
                "This customer has never ordered this product",
                "lookbackMonths",
                12,
                "order_history",
                "FIRST_TIME_PRODUCT",
            ),
            Guardrail(
                "quantity_anomaly",
                "Quantity anomaly",
                "Quantity is more than N x this customer's median for the product",
                "multiplier",
                3,
                "order_history",
                "QUANTITY_ANOMALY",
            ),
            Guardrail(
                "due_date_sanity",
                "Due-date sanity",
                "Due date is in the past, unparseable, or more than N days out",
                "maxDays",
                180,
                None,
                "DUE_DATE_INVALID",
            ),
            Guardrail(
                "restricted_product",
                "Restricted product",
                "A resolved product is flagged restricted or inactive",
                None,
                None,
                "product",
                "RESTRICTED_PRODUCT",
            ),
        )
    }
)


def guardrails_from(value: Any) -> List[Tuple[Guardrail, Optional[float]]]:
    """The enabled guardrails and each one's parameter, from the slot value.

    Accepts a list of ids, a list of ``{id, param}`` rows (the form's shape), a mapping of
    id to parameter, or the phrase ``"all"``. Unknown ids are ignored: the form only offers
    the six, and a typo in a hand-assembled draft should not become a silent pass -- so it is
    reported by the executor as ``unknown``, not raised here.
    """
    enabled: List[Tuple[Guardrail, Optional[float]]] = []
    if isinstance(value, str) and value.strip().lower() in {"all", "all six"}:
        return [(g, g.param_default) for g in RECORD_GUARDRAILS.values()]
    items: Sequence[Any]
    if isinstance(value, Mapping):
        items = [{"id": k, "param": v} for k, v in value.items()]
    elif isinstance(value, (list, tuple)):
        items = value
    elif isinstance(value, str):
        items = [
            part.strip() for part in value.replace(";", ",").split(",") if part.strip()
        ]
    else:
        items = []
    for item in items:
        if isinstance(item, Mapping):
            identifier, param = item.get("id"), item.get("param")
        else:
            identifier, param = item, None
        key = str(identifier or "").strip().lower().replace("-", "_").replace(" ", "_")
        guardrail = RECORD_GUARDRAILS.get(key)
        if guardrail is None:
            continue
        value_ = guardrail.param_default
        if param is not None and guardrail.param_key is not None:
            try:
                value_ = float(param)
            except (TypeError, ValueError):
                value_ = guardrail.param_default
        enabled.append((guardrail, value_))
    return enabled


def parse_date(value: Any) -> Optional[date]:
    """ISO first, then the formats a purchase order actually carries. None when unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%d-%b-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _history_for(
    master: Mapping[str, Sequence[Mapping[str, Any]]],
    account_id: Any,
    *,
    months: Optional[float],
    today: date,
) -> List[Mapping[str, Any]]:
    rows = [
        h for h in master.get("order_history", []) if h.get("account_id") == account_id
    ]
    if months is None:
        return rows
    cutoff_days = int(months * 30.5)
    kept: List[Mapping[str, Any]] = []
    for row in rows:
        when = parse_date(row.get("date"))
        if when is None or (today - when).days <= cutoff_days:
            kept.append(row)
    return kept


#: The order guardrails are evaluated and reported in. Every enabled guardrail still runs
#: and every failure is in ``detail``; this decides which class heads the exception when
#: more than one fails, so a restricted product a customer has also never ordered is
#: reported as restricted, not as first-time.
EVALUATION_ORDER: Tuple[str, ...] = (
    "unknown_sender",
    "restricted_product",
    "duplicate_po",
    "due_date_sanity",
    "quantity_anomaly",
    "first_time_product",
)


def check_document(
    document: Mapping[str, Any],
    master: Mapping[str, Sequence[Mapping[str, Any]]],
    enabled: Sequence[Tuple[Guardrail, Optional[float]]],
    *,
    today: Optional[date] = None,
    seen_this_run: Optional[Sequence[Tuple[Any, str]]] = None,
) -> Dict[str, Any]:
    """Run the enabled guardrails over one resolved document.

    Returns ``{"passed": [...ids], "skipped": [...ids], "exceptions": [{class, guardrail,
    detail}]}``. ``skipped`` names a guardrail whose master-data role is absent; it is never
    silently counted as passed. ``seen_this_run`` is the ``(account_id, po_number)`` pairs
    already passed earlier in the same batch, so a PO resent twice in one poll is a
    duplicate on its second appearance even before history knows the first.
    """
    today = today or date.today()
    enabled = sorted(
        enabled,
        key=lambda pair: (
            EVALUATION_ORDER.index(pair[0].id) if pair[0].id in EVALUATION_ORDER else 99
        ),
    )
    seen = {
        (account, po.strip().lower()) for account, po in (seen_this_run or []) if po
    }
    extracted: Mapping[str, Any] = document.get("extracted") or {}
    resolved: Mapping[str, Any] = document.get("resolved") or {}
    customer: Mapping[str, Any] = resolved.get("customer") or {}
    account_id = customer.get("id") if customer.get("resolved") else None
    lines: Sequence[Mapping[str, Any]] = resolved.get("lines") or []

    passed: List[str] = []
    skipped: List[str] = []
    exceptions: List[Dict[str, Any]] = []

    def fail(guardrail: Guardrail, detail: str) -> None:
        exceptions.append(
            {"class": guardrail.exception, "guardrail": guardrail.id, "detail": detail}
        )

    for guardrail, param in enabled:
        if guardrail.needs and guardrail.needs not in master:
            skipped.append(guardrail.id)
            continue
        before = len(exceptions)

        if guardrail.id == "unknown_sender":
            method = customer.get("method")
            if not customer.get("resolved") or method not in {
                "sender_domain",
                "contact_email",
            }:
                sender = (document.get("message") or {}).get("sender", "")
                fail(
                    guardrail,
                    f"{sender or 'the sender'} matches no customer or contact",
                )

        elif guardrail.id == "duplicate_po":
            po = str(extracted.get("po_number") or "").strip().lower()
            if account_id is not None and po:
                if (account_id, po) in seen:
                    fail(
                        guardrail,
                        f"PO {extracted.get('po_number')} already arrived earlier in this run",
                    )
                else:
                    for row in _history_for(
                        master, account_id, months=param, today=today
                    ):
                        if str(row.get("po_number") or "").strip().lower() == po:
                            fail(
                                guardrail,
                                f"PO {extracted.get('po_number')} already exists for this customer",
                            )
                            break

        elif guardrail.id == "first_time_product":
            if account_id is not None:
                history = _history_for(master, account_id, months=param, today=today)
                ordered = {h.get("product_id") for h in history}
                for line in lines:
                    product = line.get("product") or {}
                    if product.get("resolved") and product.get("id") not in ordered:
                        fail(
                            guardrail,
                            f"line {line.get('line_no')}: {product.get('name')} never ordered by this customer before",
                        )

        elif guardrail.id == "quantity_anomaly":
            if account_id is not None:
                history = _history_for(master, account_id, months=None, today=today)
                multiplier = param if param is not None else 3
                for line in lines:
                    product = line.get("product") or {}
                    if not product.get("resolved"):
                        continue
                    past = [
                        float(h["quantity"])
                        for h in history
                        if h.get("product_id") == product.get("id")
                        and h.get("quantity") is not None
                    ]
                    quantity = line.get("quantity")
                    if (
                        past
                        and quantity is not None
                        and float(quantity) > multiplier * median(past)
                    ):
                        fail(
                            guardrail,
                            f"line {line.get('line_no')}: quantity {quantity:g} is over "
                            f"{multiplier:g} x the customer's median of {median(past):g}",
                        )

        elif guardrail.id == "due_date_sanity":
            raw = extracted.get("due_date")
            max_days = int(param if param is not None else 180)
            if raw is not None:
                due = parse_date(raw)
                if due is None:
                    fail(guardrail, f"due date {raw!r} is unparseable")
                elif due < today:
                    fail(guardrail, f"due date {due.isoformat()} is in the past")
                elif (due - today).days > max_days:
                    fail(
                        guardrail,
                        f"due date {due.isoformat()} is more than {max_days} days out",
                    )

        elif guardrail.id == "restricted_product":
            for line in lines:
                product = line.get("product") or {}
                if product.get("resolved") and (
                    product.get("restricted") or product.get("active") is False
                ):
                    fail(
                        guardrail,
                        f"line {line.get('line_no')}: {product.get('name')} is restricted",
                    )

        if len(exceptions) == before:
            passed.append(guardrail.id)
    return {"passed": passed, "skipped": skipped, "exceptions": exceptions}
