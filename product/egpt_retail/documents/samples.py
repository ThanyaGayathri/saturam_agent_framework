"""The sample set the rehearsal runs over (K15, PA02): sixteen purchase-order emails for a
fictitious distributor, Northwind Trade Supply, with the master data they resolve against.

Mirrors ``frontend/src/domain/use-case-simulation.js`` email for email so the interface's
rehearsal and the platform's agree on what the sample set is: eight clean orders, one per
guardrail (duplicate PO, unknown sender, first-time product, quantity anomaly, due date,
restricted product), two resolution failures (product not found, ship-to ambiguous) and one
scanned copy. Each email carries a real PDF (``documents.pdf``) so the Extract stage's
reader runs for real; ``expected_extraction`` is what a correct extraction of that PDF
returns, which is what the offline rehearsal uses in place of a model call and what the
model's output is scored against when one is made.

Everything here is fictitious: ``.example`` domains, invented names, generated numbers.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional

from egpt_retail.documents.mailbox import Attachment, Message
from egpt_retail.documents.pdf import image_only_pdf, text_pdf

__all__ = [
    "OWN_DOMAIN",
    "SAMPLE_ACCOUNTS",
    "SAMPLE_PRODUCTS",
    "SAMPLE_EMAILS",
    "sample_master_data",
    "sample_messages",
    "expected_extraction",
    "expected_outcome",
]

OWN_DOMAIN = "northwind.example"

SAMPLE_ACCOUNTS: List[Dict[str, Any]] = [
    {
        "id": "A-001",
        "name": "Bristol Coatings Ltd",
        "domain": "bristolcoatings.example",
        "contacts": [("Dana Purcell", "purchasing@bristolcoatings.example")],
        "sites": [
            ("S-001", "386 Metacom Avenue", "Bristol", "RI", "02809"),
            ("S-002", "12 Wharf Road", "Warren", "RI", "02885"),
        ],
        "history": {"P-110": [40, 40, 60], "P-112": [8, 8, 12]},
        "parts": {"306727": "P-110"},
    },
    {
        "id": "A-002",
        "name": "Lycoming Fabrication Inc",
        "domain": "lycomingfab.example",
        "contacts": [("Alison Allison", "aallison@lycomingfab.example")],
        "sites": [("S-003", "652 Oliver Street", "Williamsport", "PA", "17701")],
        "history": {"P-201": [165, 165, 110], "P-105": [24, 24]},
        "parts": {"MRO-000601": "P-201"},
    },
    {
        "id": "A-003",
        "name": "Harbor Marine Services",
        "domain": "harbormarine.example",
        "contacts": [("Marco Silva", "orders@harbormarine.example")],
        "sites": [
            ("S-004", "9 Pier Lane", "New Bedford", "MA", "02740"),
            ("S-005", "9 Pier Lane, Unit B", "New Bedford", "MA", "02740"),
        ],
        "history": {"P-118": [12, 12, 24], "P-130": [200, 200]},
        "parts": {},
    },
    {
        "id": "A-004",
        "name": "Greenfield Property Group",
        "domain": "greenfieldpg.example",
        "contacts": [("Priya Nair", "buyer@greenfieldpg.example")],
        "sites": [("S-006", "44 Elm Street", "Hartford", "CT", "06103")],
        "history": {"P-105": [10, 12], "P-118": [6]},
        "parts": {},
    },
    {
        "id": "A-005",
        "name": "Summit Roofing Co",
        "domain": "summitroofing.example",
        "contacts": [("Tom Reyes", "ap@summitroofing.example")],
        "sites": [("S-007", "1500 Industrial Parkway", "Springfield", "MA", "01104")],
        "history": {"P-140": [30, 30, 30, 45]},
        "parts": {"SR-7788": "P-140"},
    },
    {
        "id": "A-006",
        "name": "Northeast Auto Body",
        "domain": "neautobody.example",
        "contacts": [("Lee Tran", "shop@neautobody.example")],
        "sites": [("S-008", "77 Route 9", "Nashua", "NH", "03060")],
        "history": {"P-112": [4, 4, 6], "P-201": [55]},
        "parts": {},
    },
]

#: id, code, name, uom, unit price, restricted, aliases
SAMPLE_PRODUCTS: List[Dict[str, Any]] = [
    {
        "id": "P-105",
        "code": "NW-105",
        "name": "Interior Latex Paint, Eggshell White, 5 gal",
        "uom": "EA",
        "unit_price": 118.0,
        "restricted": False,
        "aliases": [],
    },
    {
        "id": "P-110",
        "code": "NW-110",
        "name": "Xylene Solvent, 55 gal drum",
        "uom": "DR",
        "unit_price": 640.0,
        "restricted": False,
        "aliases": [],
    },
    {
        "id": "P-112",
        "code": "NW-112",
        "name": "Epoxy Floor Coating Kit, 2-part, 3 gal",
        "uom": "EA",
        "unit_price": 210.0,
        "restricted": False,
        "aliases": [],
    },
    {
        "id": "P-118",
        "code": "NW-118",
        "name": "Marine Antifouling Paint, 1 gal",
        "uom": "EA",
        "unit_price": 96.0,
        "restricted": False,
        "aliases": [],
    },
    {
        "id": "P-130",
        "code": "NW-130",
        "name": "Galvanised Deck Screws #10 x 2.5in, box 1000",
        "uom": "BX",
        "unit_price": 42.0,
        "restricted": False,
        "aliases": [],
    },
    {
        "id": "P-140",
        "code": "NW-140",
        "name": "Roofing Membrane Adhesive, 5 gal",
        "uom": "EA",
        "unit_price": 88.0,
        "restricted": False,
        "aliases": [],
    },
    {
        "id": "P-150",
        "code": "NW-150",
        "name": "Methyl Ethyl Ketone, 55 gal drum",
        "uom": "DR",
        "unit_price": 700.0,
        "restricted": True,
        "aliases": ["MEK 55 gal drum"],
    },
    {
        "id": "P-201",
        "code": "NW-201",
        "name": "Acetone C-P, 55 gal drum",
        "uom": "DR",
        "unit_price": 560.0,
        "restricted": False,
        "aliases": [],
    },
]


def _line(
    desc: str, qty: float, uom: str, part: str = "", price: Optional[float] = None
) -> Dict[str, Any]:
    return {"desc": desc, "qty": qty, "uom": uom, "part": part, "price": price}


#: Sixteen emails. ``expect`` is the outcome class the rehearsal must report; ``created``
#: means the record is built and written. ``ship_to`` is the address text as the PO shows it.
SAMPLE_EMAILS: List[Dict[str, Any]] = [
    {
        "id": "E-01",
        "from": "purchasing@bristolcoatings.example",
        "subject": "PO 27012128",
        "po": "27012128",
        "account": "A-001",
        "ship_to": "386 Metacom Avenue, Bristol, RI 02809",
        "due_days": 13,
        "lines": [
            _line("XYLENE SOLVENT 1 DRUM = 395LBS", 8, "DR", part="306727", price=640.0)
        ],
        "expect": "created",
    },
    {
        "id": "E-02",
        "from": "aallison@lycomingfab.example",
        "subject": "Purchase Order 177948",
        "po": "177948",
        "account": "A-002",
        "ship_to": "652 Oliver Street, Williamsport, PA 17701",
        "due_days": 14,
        "lines": [
            _line("ACETONE C-P (55 GAL DRUM)", 3, "DR", part="MRO-000601", price=560.0)
        ],
        "expect": "created",
    },
    {
        "id": "E-03",
        "from": "orders@harbormarine.example",
        "subject": "PO HM-4471",
        "po": "HM-4471",
        "account": "A-003",
        "ship_to": "9 Pier Lane, New Bedford, MA 02740",
        "due_days": 7,
        "lines": [
            _line("Marine antifouling paint 1 gal", 12, "EA", price=96.0),
            _line("Galv deck screws #10 x 2.5 box 1000", 2, "BX", price=42.0),
        ],
        "expect": "created",
    },
    {
        "id": "E-04",
        "from": "buyer@greenfieldpg.example",
        "subject": "PO GPG-2026-091",
        "po": "GPG-2026-091",
        "account": "A-004",
        "ship_to": "44 Elm Street, Hartford, CT 06103",
        "due_days": 10,
        "lines": [_line("Interior latex eggshell white 5gal", 12, "EA", price=118.0)],
        "expect": "created",
    },
    {
        "id": "E-05",
        "from": "ap@summitroofing.example",
        "subject": "PO 55120",
        "po": "55120",
        "account": "A-005",
        "ship_to": "1500 Industrial Parkway, Springfield, MA 01104",
        "due_days": 21,
        "lines": [
            _line(
                "Roofing membrane adhesive 5 gal", 30, "EA", part="SR-7788", price=88.0
            )
        ],
        "expect": "created",
    },
    {
        "id": "E-06",
        "from": "shop@neautobody.example",
        "subject": "Order NAB-311",
        "po": "NAB-311",
        "account": "A-006",
        "ship_to": "77 Route 9, Nashua, NH 03060",
        "due_days": 5,
        "lines": [_line("Epoxy floor coating kit 2-part 3 gal", 4, "EA", price=210.0)],
        "expect": "created",
    },
    {
        "id": "E-07",
        "from": "purchasing@bristolcoatings.example",
        "subject": "PO 27012140",
        "po": "27012140",
        "account": "A-001",
        "ship_to": "12 Wharf Rd, Warren, RI 02885",
        "due_days": 9,
        "lines": [_line("Epoxy floor coating kit", 10, "EA", price=210.0)],
        "expect": "created",
    },
    {
        "id": "E-08",
        "from": "aallison@lycomingfab.example",
        "subject": "Purchase Order 177962",
        "po": "177962",
        "account": "A-002",
        "ship_to": "652 Oliver St, Williamsport, PA 17701",
        "due_days": 30,
        "lines": [_line("Interior latex paint eggshell 5 gal", 24, "EA", price=118.0)],
        "expect": "created",
    },
    {
        "id": "E-09",
        "from": "purchasing@bristolcoatings.example",
        "subject": "PO 27012128 (resend)",
        "po": "27012128",
        "account": "A-001",
        "ship_to": "386 Metacom Avenue, Bristol, RI 02809",
        "due_days": 13,
        "lines": [
            _line("XYLENE SOLVENT 1 DRUM = 395LBS", 8, "DR", part="306727", price=640.0)
        ],
        "expect": "DUPLICATE_PO",
    },
    {
        "id": "E-10",
        "from": "j.rivera@gmail.example",
        "subject": "PO for paint",
        "po": "1001",
        "account": None,
        "customer_name": "J Rivera Painting",
        "ship_to": "5 Main St, Boston, MA 02110",
        "due_days": 6,
        "lines": [_line("Interior latex paint 5 gal", 2, "EA", price=118.0)],
        "expect": "UNKNOWN_SENDER",
    },
    {
        "id": "E-11",
        "from": "orders@harbormarine.example",
        "subject": "PO HM-4480",
        "po": "HM-4480",
        "account": "A-003",
        "ship_to": "9 Pier Lane, New Bedford, MA 02740",
        "due_days": 8,
        "lines": [_line("Roofing membrane adhesive 5 gal", 6, "EA", price=88.0)],
        "expect": "FIRST_TIME_PRODUCT",
    },
    {
        "id": "E-12",
        "from": "ap@summitroofing.example",
        "subject": "PO 55131",
        "po": "55131",
        "account": "A-005",
        "ship_to": "1500 Industrial Parkway, Springfield, MA 01104",
        "due_days": 14,
        "lines": [
            _line(
                "Roofing membrane adhesive 5 gal", 300, "EA", part="SR-7788", price=88.0
            )
        ],
        "expect": "QUANTITY_ANOMALY",
    },
    {
        "id": "E-13",
        "from": "buyer@greenfieldpg.example",
        "subject": "PO GPG-2026-088",
        "po": "GPG-2026-088",
        "account": "A-004",
        "ship_to": "44 Elm Street, Hartford, CT 06103",
        "due_days": -12,
        "lines": [_line("Interior latex eggshell white 5gal", 10, "EA", price=118.0)],
        "expect": "DUE_DATE_INVALID",
    },
    {
        "id": "E-14",
        "from": "shop@neautobody.example",
        "subject": "Order NAB-315",
        "po": "NAB-315",
        "account": "A-006",
        "ship_to": "77 Route 9, Nashua, NH 03060",
        "due_days": 7,
        "lines": [_line("MEK 55 gal drum", 1, "DR", price=700.0)],
        "expect": "RESTRICTED_PRODUCT",
    },
    {
        "id": "E-15",
        "from": "purchasing@bristolcoatings.example",
        "subject": "PO 27012151",
        "po": "27012151",
        "account": "A-001",
        "ship_to": "386 Metacom Avenue, Bristol, RI 02809",
        "due_days": 12,
        "lines": [_line("Solvent, drum", 2, "DR", price=640.0)],
        "expect": "PRODUCT_NOT_FOUND",
    },
    {
        "id": "E-16",
        "from": "orders@harbormarine.example",
        "subject": "PO HM-4492",
        "po": "HM-4492",
        "account": "A-003",
        "ship_to": "9 Pier Ln, New Bedford, MA 02740",
        "due_days": 10,
        "lines": [_line("Marine antifouling paint 1 gal", 12, "EA", price=96.0)],
        "expect": "SHIP_TO_AMBIGUOUS",
        "scanned": False,
    },
]
#: The scanned copy: E-08 arrives as an image-only PDF in the sample set (the interface's
#: sixteenth variant), so its expected outcome is SCANNED_DOCUMENT rather than created.
SCANNED_EMAIL_ID = "E-08"


def _account(email: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    return next((a for a in SAMPLE_ACCOUNTS if a["id"] == email.get("account")), None)


def sample_master_data(
    *, today: Optional[date] = None, exclude_history_for: Optional[str] = None
) -> Dict[str, Any]:
    """The normalised master data ``documents.matching`` and ``documents.guardrails`` read,
    for the sample accounts and products. History dates are spread over the last six
    months so the lookback guardrails have something to measure."""
    today = today or date.today()
    customer = [
        {"id": a["id"], "name": a["name"], "email_domain": a["domain"]}
        for a in SAMPLE_ACCOUNTS
    ]
    contact = [
        {
            "id": f"C-{a['id'][2:]}-{i}",
            "email": email,
            "account_id": a["id"],
            "name": name,
        }
        for a in SAMPLE_ACCOUNTS
        for i, (name, email) in enumerate(a["contacts"], start=1)
    ]
    ship_to = [
        {
            "id": sid,
            "account_id": a["id"],
            "name": a["name"],
            "street": street,
            "city": city,
            "state": state,
            "postcode": postcode,
            "country": "United States",
        }
        for a in SAMPLE_ACCOUNTS
        for sid, street, city, state, postcode in a["sites"]
    ]
    product = [
        {
            **p,
            "active": True,
            "pricebook_entry_id": "PBE-" + p["id"][2:],
            "part_numbers": {
                a["domain"]: part
                for a in SAMPLE_ACCOUNTS
                for part, pid in a["parts"].items()
                if pid == p["id"]
            },
        }
        for p in SAMPLE_PRODUCTS
    ]
    history: List[Dict[str, Any]] = []
    for a in SAMPLE_ACCOUNTS:
        n = 0
        for pid, quantities in a["history"].items():
            for quantity in quantities:
                n += 1
                history.append(
                    {
                        "account_id": a["id"],
                        "po_number": f"HIST-{a['id']}-{n:03d}",
                        "product_id": pid,
                        "quantity": quantity,
                        "date": (
                            today - timedelta(days=20 + (n * 37) % 160)
                        ).isoformat(),
                        "order_id": f"O-{a['id']}-{n:03d}",
                    }
                )
    # The duplicate-PO trap is E-09, a resend of E-01 in the same poll: the guardrail's
    # within-run check catches it. On different days the history row would.
    return {
        "customer": customer,
        "contact": contact,
        "ship_to": ship_to,
        "product": product,
        "order_history": history,
        "pricebook_id": "PB-STANDARD",
    }


def _po_lines(email: Mapping[str, Any], today: date) -> List[str]:
    account = _account(email)
    name = account["name"] if account else email.get("customer_name", "Unknown Buyer")
    po_date = today - timedelta(days=1)
    due = today + timedelta(days=int(email["due_days"]))
    lines = [
        name.upper(),
        "PURCHASE ORDER",
        f"PO Number: {email['po']}",
        f"PO Date: {po_date.isoformat()}",
        f"Requested Delivery Date: {due.isoformat()}",
        f"Buyer: {email['from']}",
        f"Ship To: {email['ship_to']}",
        f"Bill To: {email['ship_to']}",
        "Currency: USD    Payment Terms: Net 30",
        "",
        "Line  Part No.      Description                              Qty   UoM   Unit Price   Total",
    ]
    total = 0.0
    for index, line in enumerate(email["lines"], start=1):
        price = line["price"] or 0.0
        line_total = price * line["qty"]
        total += line_total
        lines.append(
            f"{index:<5} {line['part'] or '-':<13} {line['desc']:<40} {line['qty']:<5g} {line['uom']:<5} {price:>10.2f} {line_total:>9.2f}"
        )
    lines += [
        "",
        f"Order Total: {total:.2f} USD",
        "Deliver to receiving dock, weekdays 8-4.",
    ]
    return lines


def sample_messages(*, today: Optional[date] = None) -> List[Message]:
    """The sixteen emails as ``Message`` objects with their PDFs attached."""
    today = today or date.today()
    messages: List[Message] = []
    for email in SAMPLE_EMAILS:
        data = (
            image_only_pdf()
            if email["id"] == SCANNED_EMAIL_ID
            else text_pdf(_po_lines(email, today))
        )
        messages.append(
            Message(
                id=f"<{email['id'].lower()}@sample.{OWN_DOMAIN}>",
                sender=email["from"],
                subject=email["subject"],
                received_at=f"{today.isoformat()}T08:{int(email['id'][2:]):02d}:00+00:00",
                body_text="Please find our purchase order attached.",
                attachments=[
                    Attachment(f"{email['po']}.pdf", "application/pdf", len(data), data)
                ],
            )
        )
    return messages


def expected_extraction(
    email_id: str, *, today: Optional[date] = None
) -> Dict[str, Any]:
    """What a correct extraction of that email's PDF returns, in the purchase_order
    template's shape. The offline rehearsal's stand-in for the model call."""
    today = today or date.today()
    email = next(e for e in SAMPLE_EMAILS if e["id"] == email_id)
    account = _account(email)
    due = today + timedelta(days=int(email["due_days"]))
    lines = []
    for index, line in enumerate(email["lines"], start=1):
        lines.append(
            {
                "line_no": index,
                "customer_part_number": line["part"] or None,
                "supplier_product_code": None,
                "description": line["desc"],
                "quantity": line["qty"],
                "unit_of_measure": line["uom"],
                "unit_price": line["price"],
                "line_total": (line["price"] or 0.0) * line["qty"],
                "line_due_date": None,
                "line_notes": None,
            }
        )
    return {
        "po_number": email["po"],
        "po_date": (today - timedelta(days=1)).isoformat(),
        "customer_name": account["name"] if account else email.get("customer_name"),
        "buyer_name": None,
        "buyer_email": email["from"],
        "ship_to_address": email["ship_to"],
        "bill_to_address": email["ship_to"],
        "due_date": due.isoformat(),
        "currency": "USD",
        "payment_terms": "Net 30",
        "order_total": sum(item["line_total"] for item in lines),
        "delivery_instructions": "Deliver to receiving dock, weekdays 8-4.",
        "lines": lines,
    }


def expected_outcome(email_id: str) -> str:
    if email_id == SCANNED_EMAIL_ID:
        return "SCANNED_DOCUMENT"
    return str(next(e for e in SAMPLE_EMAILS if e["id"] == email_id)["expect"])
