"""Scored matching against master data, in a fixed order of attempts (K15's Resolve stage).

Deterministic and lexical. Each role is tried the same way every time -- exact keys first
(sender domain, contact email, customer part number, product code, normalised street), a
fuzzy score only after every exact attempt failed -- and a score under the threshold is an
exception carrying the closest candidate, never a guess. Two candidates within the ambiguity
margin of each other is also an exception (``*_AMBIGUOUS``): the resolver does not pick the
first of two near-ties, because "picked the wrong site and shipped there" is exactly the
failure a threshold exists to prevent.

The master data shape every role reads is the normalised one ``salesforce.load_master_data``
produces and the sample set reproduces -- plain dicts, so a second source (an ERP export, a
CSV) plugs in by producing the same rows:

    customer:      {id, name, email_domain}
    contact:       {id, email, account_id}
    ship_to:       {id, account_id, street, city, state, postcode}
    product:       {id, code, name, uom, restricted, active, aliases: [..],
                    part_numbers: {email_domain: part}, pricebook_entry_id, unit_price}
    order_history: {account_id, po_number, product_id, quantity, date}

The fuzzy score is the mean of rapidfuzz's ``token_sort_ratio`` and ``token_set_ratio`` on
normalised text, on 0..1. Sort alone punishes a reordered or abbreviated name; set alone
gives a two-word description a perfect score against any name containing both words, which
is how "Solvent, drum" would silently become xylene. The mean keeps both honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from rapidfuzz import fuzz

__all__ = [
    "Thresholds",
    "Match",
    "thresholds_from",
    "normalise",
    "score_text",
    "resolve_customer",
    "resolve_ship_to",
    "resolve_product",
]


@dataclass(frozen=True)
class Thresholds:
    name: float = 0.8
    address: float = 0.75
    product: float = 0.85
    #: Two candidates whose scores differ by less than this are a tie, and a tie is refused.
    ambiguity: float = 0.05


def thresholds_from(value: Any) -> Thresholds:
    """From the ``match thresholds`` slot value (the interface's camelCase keys accepted)."""
    raw: Mapping[str, Any] = value if isinstance(value, Mapping) else {}

    def pick(*keys: str, default: float) -> float:
        for key in keys:
            if raw.get(key) is not None:
                return float(raw[key])
        return default

    return Thresholds(
        name=pick("name", "customer", default=0.8),
        address=pick("address", "ship_to", "shipTo", default=0.75),
        product=pick("product", default=0.85),
        ambiguity=pick("ambiguity", default=0.05),
    )


@dataclass
class Match:
    """One resolution attempt's outcome. ``row`` is the master-data row when resolved."""

    role: str
    resolved: bool
    method: str
    score: float
    row: Optional[Dict[str, Any]] = None
    closest: Optional[Dict[str, Any]] = None
    reason: str = ""
    candidates: List[Tuple[float, Dict[str, Any]]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "role": self.role,
            "resolved": self.resolved,
            "method": self.method,
            "score": round(self.score, 3),
        }
        if self.row is not None:
            out["id"] = self.row.get("id")
            out["name"] = self.row.get("name") or _address_line(self.row)
        if self.closest is not None:
            out["closest"] = {
                "id": self.closest.get("id"),
                "name": self.closest.get("name") or _address_line(self.closest),
            }
        if self.reason:
            out["reason"] = self.reason
        return out


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise(text: Any) -> str:
    return " ".join(_NON_ALNUM.sub(" ", str(text or "").lower()).split())


def score_text(a: Any, b: Any) -> float:
    """0..1. The mean of token-sort and token-set ratios on normalised text; 0 for a blank."""
    left, right = normalise(a), normalise(b)
    if not left or not right:
        return 0.0
    return (
        fuzz.token_sort_ratio(left, right) + fuzz.token_set_ratio(left, right)
    ) / 200.0


def _best(
    scored: Sequence[Tuple[float, Dict[str, Any]]], threshold: float, ambiguity: float
) -> Tuple[Optional[Dict[str, Any]], float, Optional[Dict[str, Any]], str]:
    """``(row, score, closest, reason)`` -- row is None when below threshold or ambiguous."""
    ranked = sorted(scored, key=lambda pair: pair[0], reverse=True)
    if not ranked:
        return None, 0.0, None, "no candidates"
    top_score, top = ranked[0]
    if top_score < threshold:
        return (
            None,
            top_score,
            top,
            f"best score {top_score:.2f} is under {threshold:.2f}",
        )
    if len(ranked) > 1 and top_score - ranked[1][0] < ambiguity:
        return (
            None,
            top_score,
            top,
            f"two candidates within {ambiguity:.2f} of each other "
            f"({top_score:.2f} and {ranked[1][0]:.2f})",
        )
    return top, top_score, None, ""


def resolve_customer(
    master: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    sender: str,
    customer_name: Optional[str],
    thresholds: Thresholds,
) -> Match:
    """Sender domain, then the sender as a known contact, then the name on the document."""
    customers = [dict(c) for c in master.get("customer", [])]
    domain = sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""
    if domain:
        for customer in customers:
            if str(customer.get("email_domain", "")).lower() == domain:
                return Match("customer", True, "sender_domain", 1.0, row=customer)
        for contact in master.get("contact", []):
            if str(contact.get("email", "")).lower() == sender.lower():
                account = next(
                    (c for c in customers if c.get("id") == contact.get("account_id")),
                    None,
                )
                if account is not None:
                    return Match("customer", True, "contact_email", 1.0, row=account)
    if customer_name:
        scored = [(score_text(customer_name, c.get("name")), c) for c in customers]
        row, score, closest, reason = _best(
            scored, thresholds.name, thresholds.ambiguity
        )
        if row is not None:
            return Match("customer", True, "name", score, row=row)
        return Match(
            "customer",
            False,
            "name",
            score,
            closest=closest,
            reason=reason,
            candidates=scored,
        )
    return Match(
        "customer",
        False,
        "sender_domain",
        0.0,
        reason=f"{domain or 'sender'} matches no customer or contact and no name to match",
    )


def _address_line(row: Mapping[str, Any]) -> str:
    return ", ".join(
        str(row[k]) for k in ("street", "city", "state", "postcode") if row.get(k)
    )


_STREET_NUMBER = re.compile(r"^\s*(\d+[a-z]?)\b", re.IGNORECASE)
_POSTCODE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def resolve_ship_to(
    master: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    account_id: Any,
    address: Optional[str],
    thresholds: Thresholds,
) -> Match:
    """Among the customer's own sites only: exact normalised street, then postcode plus
    street number, then a fuzzy score over the whole address. One site and no address on
    the document resolves to that site -- there is nothing to choose between."""
    sites = [
        dict(s) for s in master.get("ship_to", []) if s.get("account_id") == account_id
    ]
    if not sites:
        return Match(
            "ship_to", False, "sites", 0.0, reason="customer has no ship-to sites"
        )
    if not address:
        if len(sites) == 1:
            return Match("ship_to", True, "only_site", 1.0, row=sites[0])
        return Match(
            "ship_to", False, "sites", 0.0, reason="no ship-to address on the document"
        )
    wanted = normalise(address)
    # The site's whole street line at the head of the document's address: "9 pier lane
    # unit b ..." matches "9 Pier Lane, Unit B" and, as a prefix, "9 Pier Lane" too -- the
    # longest street wins, so the unit the document names is the one chosen.
    exact = sorted(
        (
            s
            for s in sites
            if normalise(s.get("street"))
            and wanted.startswith(normalise(s.get("street")))
        ),
        key=lambda s: len(normalise(s.get("street"))),
        reverse=True,
    )
    if exact:
        return Match("ship_to", True, "exact_street", 1.0, row=exact[0])
    postcode = _POSTCODE.search(address)
    number = _STREET_NUMBER.match(address)
    if postcode and number:
        keyed = [
            s
            for s in sites
            if str(s.get("postcode", "")).startswith(postcode.group(1))
            and normalise(s.get("street")).startswith(number.group(1).lower() + " ")
        ]
        if len(keyed) == 1:
            return Match("ship_to", True, "postcode_street_number", 1.0, row=keyed[0])
        if len(keyed) > 1:
            # Same building, several sites (units), and the document names none of them.
            names = "; ".join(_address_line(s) for s in keyed)
            return Match(
                "ship_to",
                False,
                "postcode_street_number",
                1.0,
                closest=keyed[0],
                reason=f"{len(keyed)} sites within the same number and postcode ({names}); the document does not say which",
                candidates=[(1.0, s) for s in keyed],
            )
    scored = [(score_text(address, _address_line(s)), s) for s in sites]
    row, score, closest, reason = _best(
        scored, thresholds.address, thresholds.ambiguity
    )
    if row is not None:
        return Match("ship_to", True, "address_text", score, row=row)
    return Match(
        "ship_to",
        False,
        "address_text",
        score,
        closest=closest,
        reason=reason,
        candidates=scored,
    )


def resolve_product(
    master: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    customer_domain: str,
    customer_part_number: Optional[str],
    supplier_product_code: Optional[str],
    description: Optional[str],
    thresholds: Thresholds,
) -> Match:
    """The customer's own part number, then our product code, then the description against
    the name and each alias."""
    products = [dict(p) for p in master.get("product", [])]
    if customer_part_number:
        part = normalise(customer_part_number)
        for product in products:
            parts = product.get("part_numbers") or {}
            if normalise(parts.get(customer_domain)) == part and part:
                return Match("product", True, "customer_part_number", 1.0, row=product)
    if supplier_product_code:
        code = normalise(supplier_product_code)
        for product in products:
            if normalise(product.get("code")) == code:
                return Match("product", True, "product_code", 1.0, row=product)
    if description:
        scored = [
            (
                max(
                    [score_text(description, product.get("name"))]
                    + [
                        score_text(description, alias)
                        for alias in product.get("aliases") or []
                    ]
                ),
                product,
            )
            for product in products
        ]
        row, score, closest, reason = _best(
            scored, thresholds.product, thresholds.ambiguity
        )
        if row is not None:
            return Match("product", True, "description", score, row=row)
        return Match(
            "product",
            False,
            "description",
            score,
            closest=closest,
            reason=reason,
            candidates=scored,
        )
    return Match(
        "product",
        False,
        "description",
        0.0,
        reason="no part number, code or description",
    )
