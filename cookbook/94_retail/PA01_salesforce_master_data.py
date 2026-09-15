"""PA01 -- load Northwind Trade Supply's master data into the Salesforce trial org.

The Order Creation Automation use case (Process Automation, document-to-record template)
resolves an emailed purchase order against customer, ship-to, product and order-history master
data, and creates the sales order in Salesforce. This script puts that master data into the
trial org so the use case has something to resolve against and somewhere to write.

The data is the same fictitious retail distributor the interface's simulation carries
(``frontend/src/domain/use-case-simulation.js``): six trade customers with email domains, their
ship-to sites, twenty-five products with standard prices, customer part numbers, and six months
of order history. Keeping the two in step is deliberate -- the simulation on the Simulate step
and the live run against this org must agree about which email is a duplicate, a first-time
product, a quantity anomaly.

Idempotent: every record is upserted on a natural key (email domain, product code, PO number),
so re-running restores the org rather than duplicating it. ``--reset`` deletes the Northwind
records first (never Salesforce's own sample accounts).

Modelling choices, stated so nobody re-derives them:

* **Ship-to sites are child Accounts** (``ParentId``), one per site, each carrying a
  ``ShippingAddress``. The standard ``Address``/``Location`` objects exist in the org but have
  no standard link to an Account; the account hierarchy does, and it is what the Location
  concept in the semantic layer binds to.
* **Customer part numbers** live on ``Product2.Customer_Part_Number__c`` as
  ``domain=part;domain=part`` pairs -- one text field, no junction object, good enough for a
  demo and honest about being an approximation.
* **Unit of measure**: the org's ``QuantityUnitOfMeasure`` picklist carries only ``Each``, so
  the selling unit goes in ``Product2.Description`` as ``UoM: DR`` and the resolver reads it
  from there.
* **History orders are Activated** so they count as real history; the use case creates new
  orders as Draft.

Run:

    set -a && source salesforce.env && set +a
    .venv/bin/python cookbook/94_retail/PA01_salesforce_master_data.py [--reset]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests

API = "v67.0"

# ── The data ───────────────────────────────────────────────────────────────────────────────

ACCOUNTS = [
    {"key": "A-001", "name": "Bristol Coatings Ltd", "domain": "bristolcoatings.example", "billing": ("386 Metacom Avenue", "Bristol", "RI", "02809"),
     "sites": [("386 Metacom Avenue", "Bristol", "RI", "02809"), ("12 Wharf Road", "Warren", "RI", "02885")],
     "contacts": [("Dana", "Purcell", "purchasing@bristolcoatings.example")],
     "history": {"NW-110": [40, 40, 60], "NW-112": [8, 8, 12]}, "parts": {"306727": "NW-110"}},
    {"key": "A-002", "name": "Lycoming Fabrication Inc", "domain": "lycomingfab.example", "billing": ("652 Oliver Street", "Williamsport", "PA", "17701"),
     "sites": [("652 Oliver Street", "Williamsport", "PA", "17701")],
     "contacts": [("Alison", "Allison", "aallison@lycomingfab.example")],
     "history": {"NW-201": [165, 165, 110], "NW-105": [24, 24]}, "parts": {"MRO-000601": "NW-201"}},
    {"key": "A-003", "name": "Harbor Marine Services", "domain": "harbormarine.example", "billing": ("9 Pier Lane", "New Bedford", "MA", "02740"),
     "sites": [("9 Pier Lane", "New Bedford", "MA", "02740"), ("9 Pier Lane, Unit B", "New Bedford", "MA", "02740")],
     "contacts": [("Marco", "Silva", "orders@harbormarine.example")],
     "history": {"NW-118": [12, 12, 24], "NW-130": [200, 200]}, "parts": {}},
    {"key": "A-004", "name": "Greenfield Property Group", "domain": "greenfieldpg.example", "billing": ("44 Elm Street", "Hartford", "CT", "06103"),
     "sites": [("44 Elm Street", "Hartford", "CT", "06103")],
     "contacts": [("Priya", "Nair", "buyer@greenfieldpg.example")],
     "history": {"NW-105": [10, 12], "NW-118": [6]}, "parts": {}},
    {"key": "A-005", "name": "Summit Roofing Co", "domain": "summitroofing.example", "billing": ("1500 Industrial Parkway", "Springfield", "MA", "01104"),
     "sites": [("1500 Industrial Parkway", "Springfield", "MA", "01104")],
     "contacts": [("Tom", "Reyes", "ap@summitroofing.example")],
     "history": {"NW-140": [30, 30, 30, 45]}, "parts": {"SR-7788": "NW-140"}},
    {"key": "A-006", "name": "Northeast Auto Body", "domain": "neautobody.example", "billing": ("77 Route 9", "Nashua", "NH", "03060"),
     "sites": [("77 Route 9", "Nashua", "NH", "03060")],
     "contacts": [("Lena", "Hoff", "shop@neautobody.example")],
     "history": {"NW-112": [4, 4, 6], "NW-201": [55]}, "parts": {}},
]

# code, name, uom, unit price, restricted
PRODUCTS = [
    ("NW-105", "Interior Latex Paint, Eggshell White, 5 gal", "EA", 68.00, False),
    ("NW-106", "Interior Latex Paint, Flat White, 5 gal", "EA", 62.00, False),
    ("NW-107", "Exterior Acrylic Paint, Base Tint, 5 gal", "EA", 89.00, False),
    ("NW-108", "Primer Sealer, Water-based, 5 gal", "EA", 54.00, False),
    ("NW-110", "Xylene Solvent, 55 gal drum", "DR", 412.00, False),
    ("NW-111", "Mineral Spirits, 5 gal", "EA", 46.00, False),
    ("NW-112", "Epoxy Floor Coating Kit, 2-part, 3 gal", "EA", 189.00, False),
    ("NW-113", "Polyurethane Clear Coat, Satin, 1 gal", "EA", 58.00, False),
    ("NW-118", "Marine Antifouling Paint, 1 gal", "EA", 74.00, False),
    ("NW-119", "Marine Varnish, Spar, 1 qt", "EA", 29.00, False),
    ("NW-125", "Masking Tape, 2 in x 60 yd, case of 24", "CS", 61.00, False),
    ("NW-126", "Drop Cloth, Canvas, 9 x 12 ft", "EA", 24.00, False),
    ("NW-130", "Galvanised Deck Screws #10 x 2½, box 1000", "BX", 38.00, False),
    ("NW-131", "Stainless Deck Screws #10 x 2½, box 1000", "BX", 92.00, False),
    ("NW-132", "Concrete Anchors, ½ in x 4 in, box 50", "BX", 47.00, False),
    ("NW-140", "Roofing Membrane Adhesive, 5 gal", "EA", 112.00, False),
    ("NW-141", "Roofing Sealant, 10 oz tube, case of 12", "CS", 78.00, False),
    ("NW-142", "EPDM Membrane, 10 x 20 ft", "EA", 245.00, False),
    ("NW-150", "Methyl Ethyl Ketone, 55 gal drum", "DR", 498.00, True),
    ("NW-151", "Muriatic Acid, 1 gal, case of 4", "CS", 36.00, True),
    ("NW-152", "Lacquer Thinner, 5 gal", "EA", 71.00, True),
    ("NW-153", "Sodium Hypochlorite 12.5%, 5 gal", "EA", 33.00, True),
    ("NW-201", "Acetone C-P, 55 gal drum", "DR", 389.00, False),
    ("NW-202", "Isopropyl Alcohol 99%, 5 gal", "EA", 84.00, False),
    ("NW-203", "Denatured Alcohol, 1 gal, case of 4", "CS", 52.00, False),
]

# ── Salesforce client, stdlib + requests only ──────────────────────────────────────────────


class Sf:
    def __init__(self) -> None:
        env = {k: os.environ[k] for k in ("SF_LOGIN_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET")}
        tok = requests.post(
            env["SF_LOGIN_URL"] + "/services/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": env["SF_CLIENT_ID"], "client_secret": env["SF_CLIENT_SECRET"]},
            timeout=30,
        )
        tok.raise_for_status()
        body = tok.json()
        self.base = f"{body['instance_url']}/services/data/{API}"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {body['access_token']}", "Content-Type": "application/json"})

    def query(self, soql: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        r = self.s.get(f"{self.base}/query", params={"q": soql}, timeout=60)
        r.raise_for_status()
        body = r.json()
        out.extend(body["records"])
        while not body["done"]:
            r = self.s.get(body["nextRecordsUrl"] if body["nextRecordsUrl"].startswith("http") else self.base.rsplit("/services", 1)[0] + body["nextRecordsUrl"], timeout=60)
            r.raise_for_status()
            body = r.json()
            out.extend(body["records"])
        return out

    def create(self, obj: str, rec: Dict[str, Any]) -> str:
        r = self.s.post(f"{self.base}/sobjects/{obj}", data=json.dumps(rec), timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"create {obj} failed {r.status_code}: {r.text[:300]} :: {rec}")
        return r.json()["id"]

    def update(self, obj: str, rid: str, rec: Dict[str, Any]) -> None:
        r = self.s.patch(f"{self.base}/sobjects/{obj}/{rid}", data=json.dumps(rec), timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"update {obj} {rid} failed {r.status_code}: {r.text[:300]}")

    def delete(self, obj: str, rid: str) -> None:
        r = self.s.delete(f"{self.base}/sobjects/{obj}/{rid}", timeout=60)
        if r.status_code >= 300 and r.status_code != 404:
            raise RuntimeError(f"delete {obj} {rid} failed {r.status_code}: {r.text[:300]}")

    def upsert(self, obj: str, where: str, rec: Dict[str, Any], immutable: tuple = ()) -> str:
        """Insert, or update everything but the fields Salesforce refuses to change after insert."""
        rows = self.query(f"SELECT Id FROM {obj} WHERE {where} LIMIT 1")
        if rows:
            self.update(obj, rows[0]["Id"], {k: v for k, v in rec.items() if k not in immutable})
            return rows[0]["Id"]
        return self.create(obj, rec)


#: The org has state/country picklists on; they take full names, not codes.
STATE_NAMES = {"RI": "Rhode Island", "PA": "Pennsylvania", "MA": "Massachusetts", "CT": "Connecticut", "NH": "New Hampshire"}


def soql_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


# ── Loading ────────────────────────────────────────────────────────────────────────────────


def reset(sf: Sf) -> None:
    """Delete every Northwind record, children before parents. Salesforce's own samples stay."""
    domains = ",".join(soql_str(a["domain"]) for a in ACCOUNTS)
    parents = sf.query(f"SELECT Id FROM Account WHERE Email_Domain__c IN ({domains})")
    ids = [p["Id"] for p in parents]
    if ids:
        idlist = ",".join(soql_str(i) for i in ids)
        for o in sf.query(f"SELECT Id FROM Order WHERE AccountId IN ({idlist})"):
            sf.delete("Order", o["Id"])  # OrderItems cascade
        for c in sf.query(f"SELECT Id FROM Account WHERE ParentId IN ({idlist})"):
            sf.delete("Account", c["Id"])
        for i in ids:
            sf.delete("Account", i)
    codes = ",".join(soql_str(p[0]) for p in PRODUCTS)
    for p in sf.query(f"SELECT Id FROM Product2 WHERE ProductCode IN ({codes})"):
        sf.delete("Product2", p["Id"])  # PricebookEntries cascade
    print("reset: Northwind records removed")


def load(sf: Sf, seed: int = 7) -> Dict[str, Any]:
    rng = random.Random(seed)
    std = sf.query("SELECT Id, IsActive FROM Pricebook2 WHERE IsStandard = true")[0]
    if not std["IsActive"]:
        sf.update("Pricebook2", std["Id"], {"IsActive": True})
    pricebook_id = std["Id"]

    # Products, with a standard price each and the customer part numbers that point at them.
    parts_by_code: Dict[str, List[str]] = {}
    for a in ACCOUNTS:
        for part, code in a["parts"].items():
            parts_by_code.setdefault(code, []).append(f"{a['domain']}={part}")
    product_ids: Dict[str, str] = {}
    for code, name, uom, price, restricted in PRODUCTS:
        pid = sf.upsert("Product2", f"ProductCode = {soql_str(code)}", {
            "Name": name, "ProductCode": code, "IsActive": True, "Family": "Northwind",
            "Description": f"UoM: {uom}", "QuantityUnitOfMeasure": "Each",
            "Restricted__c": restricted, "Customer_Part_Number__c": ";".join(parts_by_code.get(code, [])),
        })
        product_ids[code] = pid
        sf.upsert("PricebookEntry", f"Product2Id = {soql_str(pid)} AND Pricebook2Id = {soql_str(pricebook_id)}",
                  {"Product2Id": pid, "Pricebook2Id": pricebook_id, "UnitPrice": price, "IsActive": True}, immutable=("Product2Id", "Pricebook2Id"))
    pbe = {r["Product2Id"]: r["Id"] for r in sf.query(f"SELECT Id, Product2Id FROM PricebookEntry WHERE Pricebook2Id = {soql_str(pricebook_id)}")}

    # Accounts, sites, contacts.
    account_ids: Dict[str, str] = {}
    site_ids: Dict[str, List[str]] = {}
    contact_ids: Dict[str, str] = {}
    for a in ACCOUNTS:
        b = a["billing"]
        aid = sf.upsert("Account", f"Email_Domain__c = {soql_str(a['domain'])} AND ParentId = null", {
            "Name": a["name"], "Email_Domain__c": a["domain"], "Industry": "Retail", "Type": "Customer - Direct",
            "BillingStreet": b[0], "BillingCity": b[1], "BillingState": STATE_NAMES[b[2]], "BillingPostalCode": b[3], "BillingCountry": "United States",
            "ShippingStreet": a["sites"][0][0], "ShippingCity": a["sites"][0][1], "ShippingState": STATE_NAMES[a["sites"][0][2]], "ShippingPostalCode": a["sites"][0][3], "ShippingCountry": "United States",
        })
        account_ids[a["key"]] = aid
        site_ids[a["key"]] = [aid]
        for street, city, state, postcode in a["sites"][1:]:
            sid = sf.upsert("Account", f"ParentId = {soql_str(aid)} AND ShippingPostalCode = {soql_str(postcode)} AND ShippingCity = {soql_str(city)}", {
                "Name": f"{a['name']} — {city} site", "ParentId": aid, "Email_Domain__c": a["domain"], "Type": "Ship-to site",
                "ShippingStreet": street, "ShippingCity": city, "ShippingState": STATE_NAMES[state], "ShippingPostalCode": postcode, "ShippingCountry": "United States",
            })
            site_ids[a["key"]].append(sid)
        for first, last, email in a["contacts"]:
            contact_ids[email] = sf.upsert("Contact", f"Email = {soql_str(email)}", {"FirstName": first, "LastName": last, "Email": email, "AccountId": aid, "Title": "Buyer"})

    # Six months of history: one Activated order per historical quantity, dated back in time.
    today = date.today()
    orders = 0
    for a in ACCOUNTS:
        aid = account_ids[a["key"]]
        n = 0
        for code, qtys in a["history"].items():
            for qty in qtys:
                n += 1
                po = f"HIST-{a['key']}-{n:03d}"
                when = today - timedelta(days=rng.randint(20, 180))
                existing = sf.query(f"SELECT Id FROM Order WHERE AccountId = {soql_str(aid)} AND PoNumber = {soql_str(po)}")
                if existing:
                    continue
                oid = sf.create("Order", {
                    "AccountId": aid, "Pricebook2Id": pricebook_id, "EffectiveDate": when.isoformat(), "Status": "Draft",
                    "PoNumber": po, "PoDate": when.isoformat(), "Description": "Northwind history (PA01 loader)",
                })
                price = next(p[3] for p in PRODUCTS if p[0] == code)
                sf.create("OrderItem", {"OrderId": oid, "PricebookEntryId": pbe[product_ids[code]], "Quantity": qty, "UnitPrice": price, "ServiceDate": (when + timedelta(days=10)).isoformat()})
                sf.update("Order", oid, {"Status": "Activated"})
                orders += 1
    return {"products": len(product_ids), "accounts": len(account_ids), "sites": sum(len(v) for v in site_ids.values()), "contacts": len(contact_ids), "orders_created": orders}


def summary(sf: Sf) -> Dict[str, Any]:
    domains = ",".join(soql_str(a["domain"]) for a in ACCOUNTS)
    return {
        "accounts": len(sf.query(f"SELECT Id FROM Account WHERE Email_Domain__c IN ({domains})")),
        "contacts": len(sf.query(f"SELECT Id FROM Contact WHERE Account.Email_Domain__c IN ({domains})")),
        "products": len(sf.query("SELECT Id FROM Product2 WHERE Family = 'Northwind'")),
        "restricted": len(sf.query("SELECT Id FROM Product2 WHERE Family = 'Northwind' AND Restricted__c = true")),
        "orders": len(sf.query(f"SELECT Id FROM Order WHERE Account.Email_Domain__c IN ({domains})")),
        "order_items": len(sf.query(f"SELECT Id FROM OrderItem WHERE Order.Account.Email_Domain__c IN ({domains})")),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="PA01_salesforce_master_data")
    p.add_argument("--reset", action="store_true", help="delete Northwind records before loading")
    args = p.parse_args(argv)
    missing = [k for k in ("SF_LOGIN_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET") if not os.environ.get(k)]
    if missing:
        print("missing env:", ", ".join(missing), "-- set -a && source salesforce.env && set +a")
        return 2
    sf = Sf()
    if args.reset:
        reset(sf)
    print("loaded:", json.dumps(load(sf)))
    print("org now holds:", json.dumps(summary(sf)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
