"""Business guardrails as rules (K15, generalised in K17).

A guardrail is a rule: a *kind* the platform knows how to evaluate, the *field* it applies
to, its parameters, the master-data role it needs, and the exception class it raises. Eight
kinds exist and every check a document-driven use case has asked for so far is one of them:

    duplicate_reference   a header field's value already exists in history for this
                          customer (or arrived earlier in this run)
    sender_known          the customer was identified from the sender, not guessed by name
    first_time_item       a line's resolved item was never ordered by this customer before
    quantity_outlier      a numeric line field is over N x the customer's median for the item
    date_window           a date field parses, is not in the past, and is at most N days out
    flagged_item          a line's resolved item is flagged restricted or inactive
    numeric_range         a numeric field sits between a minimum and a maximum
    total_matches_lines   a header total equals the sum of a line field, within a tolerance

The six purchase-order guardrails are a preset of these (``PURCHASE_ORDER_RULES``): the
same ids and exception classes as before, so a spec authored last week evaluates the same
way. A supplier invoice gets a duplicate-invoice rule and a total check from the same
interpreter, and a document kind onboarded tomorrow gets its rules from ``suggest_rules``
over its own field list, with nothing added here.

A rule whose master-data role is not connected is reported as ``skipped`` on every document,
never silently passed. Rules evaluate in a fixed priority so the most serious class heads a
multi-failure. Every check is a pure function of the document and the master data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime
from statistics import median
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt_retail.documents.templates import ExtractionTemplate

__all__ = [
    "Rule",
    "RuleKind",
    "RULE_KINDS",
    "PURCHASE_ORDER_RULES",
    "RECORD_GUARDRAILS",
    "rules_from",
    "guardrails_from",
    "suggest_rules",
    "check_document",
    "parse_date",
    "Guardrail",
]


@dataclass(frozen=True)
class RuleKind:
    id: str
    label: str
    #: What the check says, with ``{field}`` and the parameter names as placeholders.
    check: str
    #: ``header``, ``line`` or ``none`` -- which kind of field the rule takes.
    scope: str
    #: Parameter name -> default. Empty for a rule with no parameter.
    params: Mapping[str, float]
    #: The master-data role the rule needs, or None.
    needs: Optional[str]
    #: The exception class raised when the rule is not a preset with its own.
    exception: str
    #: Where the rule sits when several fail on one document (lower first).
    priority: int


RULE_KINDS: Mapping[str, RuleKind] = MappingProxyType(
    {
        k.id: k
        for k in (
            RuleKind(
                "sender_known",
                "Sender known",
                "The sender's email domain matches no customer or contact",
                "none",
                {},
                "customer",
                "UNKNOWN_SENDER",
                0,
            ),
            RuleKind(
                "flagged_item",
                "Flagged item",
                "A resolved {field} is flagged restricted or inactive",
                "line",
                {},
                "product",
                "RESTRICTED_ITEM",
                1,
            ),
            RuleKind(
                "duplicate_reference",
                "Duplicate reference",
                "A record for this customer with the same {field} already exists (last {lookback_months} months)",
                "header",
                {"lookback_months": 12},
                "order_history",
                "DUPLICATE_REFERENCE",
                2,
            ),
            RuleKind(
                "date_window",
                "Date window",
                "{field} is in the past, unparseable, or more than {max_days} days out",
                "header",
                {"max_days": 180},
                None,
                "DATE_INVALID",
                3,
            ),
            RuleKind(
                "numeric_range",
                "Numeric range",
                "{field} is outside {min} to {max}",
                "any",
                {"min": 0, "max": 1_000_000},
                None,
                "OUT_OF_RANGE",
                4,
            ),
            RuleKind(
                "total_matches_lines",
                "Total matches lines",
                "{field} differs from the sum of the lines by more than {tolerance}",
                "header",
                {"tolerance": 0.01},
                None,
                "TOTAL_MISMATCH",
                5,
            ),
            RuleKind(
                "quantity_outlier",
                "Quantity outlier",
                "{field} is more than {multiplier} x this customer's median for the item",
                "line",
                {"multiplier": 3},
                "order_history",
                "QUANTITY_ANOMALY",
                6,
            ),
            RuleKind(
                "first_time_item",
                "First-time item",
                "This customer has never ordered this {field} (last {lookback_months} months)",
                "line",
                {"lookback_months": 12},
                "order_history",
                "FIRST_TIME_ITEM",
                7,
            ),
        )
    }
)


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    label: str
    field: Optional[str]
    params: Mapping[str, float] = dc_field(default_factory=dict)
    exception: str = ""
    #: For ``total_matches_lines``: the line field summed. For item rules: the resolved
    #: entity key on the line (``product``).
    line_field: Optional[str] = None

    @property
    def definition(self) -> RuleKind:
        return RULE_KINDS[self.kind]

    @property
    def needs(self) -> Optional[str]:
        return self.definition.needs

    @property
    def exception_class(self) -> str:
        return self.exception or self.definition.exception

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "field": self.field,
            "params": dict(self.params),
            "exception": self.exception_class,
            "line_field": self.line_field,
            "needs": self.needs,
        }


#: The purchase-order preset: the six guardrails, as rules, with their original ids and
#: exception classes. What ``"all"`` and the legacy ``{id, param}`` rows resolve to.
PURCHASE_ORDER_RULES: Tuple[Rule, ...] = (
    Rule(
        "duplicate_po",
        "duplicate_reference",
        "Duplicate PO",
        "po_number",
        {"lookback_months": 12},
        "DUPLICATE_PO",
    ),
    Rule(
        "unknown_sender", "sender_known", "Unknown sender", None, {}, "UNKNOWN_SENDER"
    ),
    Rule(
        "first_time_product",
        "first_time_item",
        "First-time product",
        "description",
        {"lookback_months": 12},
        "FIRST_TIME_PRODUCT",
        line_field="product",
    ),
    Rule(
        "quantity_anomaly",
        "quantity_outlier",
        "Quantity anomaly",
        "quantity",
        {"multiplier": 3},
        "QUANTITY_ANOMALY",
        line_field="product",
    ),
    Rule(
        "due_date_sanity",
        "date_window",
        "Due-date sanity",
        "due_date",
        {"max_days": 180},
        "DUE_DATE_INVALID",
    ),
    Rule(
        "restricted_product",
        "flagged_item",
        "Restricted product",
        "description",
        {},
        "RESTRICTED_PRODUCT",
        line_field="product",
    ),
)
_PRESETS: Mapping[str, Rule] = MappingProxyType({r.id: r for r in PURCHASE_ORDER_RULES})

#: The legacy name the interface, tests and docs used for the preset. Same rows.
RECORD_GUARDRAILS = _PRESETS
Guardrail = Rule

#: Legacy single-parameter names on the ``{id, param}`` rows.
_LEGACY_PARAM = {
    "lookbackMonths": "lookback_months",
    "multiplier": "multiplier",
    "maxDays": "max_days",
}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rule_from_row(row: Mapping[str, Any]) -> Optional[Rule]:
    kind = str(row.get("kind") or "").strip().lower()
    identifier = (
        str(row.get("id") or "").strip().lower().replace("-", "_").replace(" ", "_")
    )
    preset = _PRESETS.get(identifier)
    if not kind and preset is None:
        return None
    if not kind and preset is not None:
        param = row.get("param")
        params = dict(preset.params)
        if param is not None and params:
            key = next(iter(params))
            params[key] = _float(param, params[key])
        if isinstance(row.get("params"), Mapping):
            for k, v in row["params"].items():
                params[_LEGACY_PARAM.get(str(k), str(k))] = _float(
                    v, params.get(str(k), 0.0)
                )
        return Rule(
            preset.id,
            preset.kind,
            preset.label,
            preset.field,
            params,
            preset.exception,
            preset.line_field,
        )
    definition = RULE_KINDS.get(kind)
    if definition is None:
        return None
    params = dict(definition.params)
    for k, v in (
        (row.get("params") or {}).items()
        if isinstance(row.get("params"), Mapping)
        else []
    ):
        params[_LEGACY_PARAM.get(str(k), str(k))] = _float(v, params.get(str(k), 0.0))
    if row.get("param") is not None and params:
        key = next(iter(params))
        params[key] = _float(row["param"], params[key])
    field_name = (
        str(row.get("field") or "") or (preset.field if preset else None) or None
    )
    label = str(
        row.get("label")
        or (
            preset.label
            if preset
            else f"{definition.label}: {field_name or ''}".strip(": ")
        )
    )
    return Rule(
        identifier or f"{kind}_{field_name or 'rule'}",
        kind,
        label,
        field_name,
        params,
        str(row.get("exception") or (preset.exception if preset else "")),
        str(row.get("line_field") or (preset.line_field if preset else ""))
        or (
            "product"
            if definition.scope == "line" and kind != "numeric_range"
            else None
        ),
    )


def rules_from(value: Any) -> List[Rule]:
    """The enabled rules from the slot value.

    Accepts the preset ids (``"duplicate_po"``), the interface's legacy ``{id, param}`` rows,
    full rule rows (``{id, kind, field, params, label, exception}``), a mapping of id to
    parameter, or ``"all"`` for the purchase-order preset. Unknown rows are dropped, not
    raised: the executor reports them, a typo in a hand-assembled draft must not become a
    silent pass.
    """
    if isinstance(value, str) and value.strip().lower() in {
        "all",
        "all six",
        "standard",
    }:
        return list(PURCHASE_ORDER_RULES)
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
    rules: List[Rule] = []
    for item in items:
        row = item if isinstance(item, Mapping) else {"id": item}
        rule = _rule_from_row(row)
        if rule is not None:
            rules.append(rule)
    return rules


def guardrails_from(value: Any) -> List[Tuple[Rule, Optional[float]]]:
    """Legacy shape: ``(rule, first parameter)`` pairs. Prefer :func:`rules_from`."""
    return [(r, next(iter(r.params.values()), None)) for r in rules_from(value)]


_NUMBER_LIKE = re.compile(r"(number|reference|ref|id|no)$")
_DATE_LIKE = re.compile(r"(date|due|deadline)")
_FUTURE_DATE = re.compile(r"(due|deliver|requested|deadline|expected|ship)")
_TOTAL_LIKE = re.compile(r"^(order_)?total$|_total$|^amount$")
_QUANTITY_LIKE = re.compile(r"^(quantity|qty|units?)$")
_LINE_TOTAL_LIKE = re.compile(r"^(line_total|line_amount|amount)$")
_ITEM_LIKE = re.compile(r"^(description|product|item|sku|material)$")


def suggest_rules(
    template: ExtractionTemplate, *, history_role: str = "order_history"
) -> List[Rule]:
    """Rules a template's own field names call for. Pure: a document kind onboarded
    tomorrow gets its suggested checks from its fields, and a person turns them on."""
    header = [n for n, _ in template.header]
    lines = [n for n, _ in template.lines]
    out: List[Rule] = [
        Rule("sender_known", "sender_known", "Sender known", None, {}, "UNKNOWN_SENDER")
    ]
    reference = next(
        (n for n in header if _NUMBER_LIKE.search(n) and not n.startswith("line")), None
    )
    if reference:
        out.append(
            Rule(
                f"duplicate_{reference}",
                "duplicate_reference",
                f"Duplicate {reference.replace('_', ' ')}",
                reference,
                {"lookback_months": 12},
            )
        )
    for name in header:
        # A date the document promises (due, delivery, requested) is checked against the
        # window; the document's own date (po_date, invoice_date) is in the past by nature.
        if _DATE_LIKE.search(name) and _FUTURE_DATE.search(name):
            out.append(
                Rule(
                    f"date_window_{name}",
                    "date_window",
                    f"{name.replace('_', ' ').capitalize()} sanity",
                    name,
                    {"max_days": 180},
                )
            )
    item_field = next((n for n in lines if _ITEM_LIKE.match(n)), None)
    if item_field:
        out.append(
            Rule(
                "flagged_item",
                "flagged_item",
                "Restricted item",
                item_field,
                {},
                line_field="product",
            )
        )
        out.append(
            Rule(
                "first_time_item",
                "first_time_item",
                "First-time item",
                item_field,
                {"lookback_months": 12},
                line_field="product",
            )
        )
    quantity = next((n for n in lines if _QUANTITY_LIKE.match(n)), None)
    if quantity:
        out.append(
            Rule(
                f"outlier_{quantity}",
                "quantity_outlier",
                f"{quantity.capitalize()} anomaly",
                quantity,
                {"multiplier": 3},
                line_field="product",
            )
        )
    total = next((n for n in header if _TOTAL_LIKE.search(n)), None)
    line_total = next((n for n in lines if _LINE_TOTAL_LIKE.match(n)), None)
    if total and line_total:
        out.append(
            Rule(
                f"total_{total}",
                "total_matches_lines",
                f"{total.replace('_', ' ').capitalize()} matches the lines",
                total,
                {"tolerance": 0.01},
                line_field=line_total,
            )
        )
    return out


def parse_date(value: Any) -> Optional[date]:
    """ISO first, then the formats a document actually carries. None when unparseable."""
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
    role: str,
    account_id: Any,
    *,
    months: Optional[float],
    today: date,
) -> List[Mapping[str, Any]]:
    rows = [h for h in master.get(role, []) if h.get("account_id") == account_id]
    if months is None:
        return rows
    cutoff_days = int(months * 30.5)
    kept: List[Mapping[str, Any]] = []
    for row in rows:
        when = parse_date(row.get("date"))
        if when is None or (today - when).days <= cutoff_days:
            kept.append(row)
    return kept


def _number(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def check_document(
    document: Mapping[str, Any],
    master: Mapping[str, Sequence[Mapping[str, Any]]],
    enabled: Sequence[Any],
    *,
    today: Optional[date] = None,
    seen_this_run: Optional[Sequence[Tuple[Any, str]]] = None,
) -> Dict[str, Any]:
    """Run the enabled rules over one resolved document.

    Returns ``{"passed": [...ids], "skipped": [...ids], "exceptions": [{class, guardrail,
    detail}]}``. ``skipped`` names a rule whose master-data role is absent; it is never
    silently counted as passed. ``seen_this_run`` is the ``(account_id, reference)`` pairs
    already passed earlier in the same batch, so a document resent twice in one poll is a
    duplicate on its second appearance even before history knows the first.
    """
    today = today or date.today()
    rules: List[Rule] = [r[0] if isinstance(r, tuple) else r for r in enabled]
    rules.sort(key=lambda r: r.definition.priority)
    seen = {
        (account, str(ref).strip().lower())
        for account, ref in (seen_this_run or [])
        if ref
    }

    extracted: Mapping[str, Any] = document.get("extracted") or {}
    resolved: Mapping[str, Any] = document.get("resolved") or {}
    customer: Mapping[str, Any] = resolved.get("customer") or {}
    account_id = customer.get("id") if customer.get("resolved") else None
    lines: Sequence[Mapping[str, Any]] = (
        resolved.get("lines") or extracted.get("lines") or []
    )
    history_role = "order_history"

    passed: List[str] = []
    skipped: List[str] = []
    exceptions: List[Dict[str, Any]] = []

    def fail(rule: Rule, detail: str) -> None:
        exceptions.append(
            {"class": rule.exception_class, "guardrail": rule.id, "detail": detail}
        )

    for rule in rules:
        if rule.needs and rule.needs not in master:
            skipped.append(rule.id)
            continue
        before = len(exceptions)
        kind = rule.kind
        p = rule.params
        label = (rule.field or "").replace("_", " ")

        if kind == "sender_known":
            if not customer.get("resolved") or customer.get("method") not in {
                "sender_domain",
                "contact_email",
            }:
                sender = (document.get("message") or {}).get("sender", "")
                fail(rule, f"{sender or 'the sender'} matches no customer or contact")

        elif kind == "duplicate_reference":
            reference = str(extracted.get(rule.field or "") or "").strip().lower()
            if account_id is not None and reference:
                if (account_id, reference) in seen:
                    fail(
                        rule,
                        f"{label} {extracted.get(rule.field or '')} already arrived earlier in this run",
                    )
                else:
                    for row in _history_for(
                        master,
                        history_role,
                        account_id,
                        months=p.get("lookback_months"),
                        today=today,
                    ):
                        past = (
                            row.get(rule.field or "")
                            if rule.field in row
                            else row.get("reference", row.get("po_number"))
                        )
                        if str(past or "").strip().lower() == reference:
                            fail(
                                rule,
                                f"{label} {extracted.get(rule.field or '')} already exists for this customer",
                            )
                            break

        elif kind == "first_time_item":
            if account_id is not None:
                history = _history_for(
                    master,
                    history_role,
                    account_id,
                    months=p.get("lookback_months"),
                    today=today,
                )
                ordered = {h.get("product_id") for h in history}
                for line in lines:
                    item = line.get(rule.line_field or "product") or {}
                    if item.get("resolved") and item.get("id") not in ordered:
                        fail(
                            rule,
                            f"line {line.get('line_no')}: {item.get('name')} never ordered by this customer before",
                        )

        elif kind == "quantity_outlier":
            if account_id is not None:
                history = _history_for(
                    master, history_role, account_id, months=None, today=today
                )
                multiplier = p.get("multiplier", 3)
                for line in lines:
                    item = line.get(rule.line_field or "product") or {}
                    if not item.get("resolved"):
                        continue
                    past = [
                        float(h["quantity"])
                        for h in history
                        if h.get("product_id") == item.get("id")
                        and h.get("quantity") is not None
                    ]
                    quantity = _number(line.get(rule.field or "quantity"))
                    if (
                        past
                        and quantity is not None
                        and quantity > multiplier * median(past)
                    ):
                        fail(
                            rule,
                            f"line {line.get('line_no')}: {label} {quantity:g} is over {multiplier:g} x the customer's median of {median(past):g}",
                        )

        elif kind == "date_window":
            raw = extracted.get(rule.field or "")
            max_days = int(p.get("max_days", 180))
            if raw is not None:
                due = parse_date(raw)
                if due is None:
                    fail(rule, f"{label} {raw!r} is unparseable")
                elif due < today:
                    fail(rule, f"{label} {due.isoformat()} is in the past")
                elif (due - today).days > max_days:
                    fail(
                        rule,
                        f"{label} {due.isoformat()} is more than {max_days} days out",
                    )

        elif kind == "flagged_item":
            for line in lines:
                item = line.get(rule.line_field or "product") or {}
                if item.get("resolved") and (
                    item.get("restricted") or item.get("active") is False
                ):
                    fail(
                        rule,
                        f"line {line.get('line_no')}: {item.get('name')} is restricted",
                    )

        elif kind == "numeric_range":
            low, high = p.get("min"), p.get("max")
            values: List[Tuple[str, Any]] = []
            if rule.field in extracted:
                values.append(("", extracted.get(rule.field or "")))
            for line in lines:
                if rule.field in line:
                    values.append(
                        (f"line {line.get('line_no')}: ", line.get(rule.field or ""))
                    )
            for prefix, raw in values:
                number = _number(raw)
                if number is None:
                    continue
                if (low is not None and number < low) or (
                    high is not None and number > high
                ):
                    fail(
                        rule,
                        f"{prefix}{label} {number:g} is outside {low:g} to {high:g}",
                    )

        elif kind == "total_matches_lines":
            total = _number(extracted.get(rule.field or ""))
            parts = [
                _number(line.get(rule.line_field or "line_total")) for line in lines
            ]
            if total is not None and parts and all(x is not None for x in parts):
                summed = sum(x for x in parts if x is not None)
                tolerance = p.get("tolerance", 0.01)
                if abs(summed - total) > max(tolerance, tolerance * abs(total)):
                    fail(
                        rule,
                        f"{label} {total:g} differs from the lines' sum {summed:g}",
                    )

        if len(exceptions) == before:
            passed.append(rule.id)
    return {"passed": passed, "skipped": skipped, "exceptions": exceptions}
