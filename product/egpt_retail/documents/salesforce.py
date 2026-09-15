"""Salesforce: the master data the Resolve stage reads and the objects the Write stage
creates (K15, PA01/PA02).

A thin REST client -- client-credentials OAuth against the org's My Domain, the same flow
``cookbook/94_retail/PA01_salesforce_master_data.py`` proved -- and two functions on top of
it: ``load_master_data`` produces the normalised rows ``documents.matching`` reads, and
``write_record_tree`` creates the tree ``documents.mapping`` built. Both have async variants
via ``asyncio.to_thread``; the client itself is synchronous ``httpx``.

The org's modelling choices are read back here exactly as PA01 wrote them: ship-to sites are
child Accounts (``ParentId``), customer part numbers are ``domain=part;...`` pairs on
``Product2.Customer_Part_Number__c``, the selling unit is ``UoM: DR`` in
``Product2.Description`` and aliases follow it as ``Aliases: a|b``. A deployment whose org
models these differently changes ``load_master_data``, and nothing downstream.

Credentials come from the write target's configured source (``login_url``, ``client_id``,
``client_secret``) or, per field, from the ``SF_*`` environment ``salesforce.env`` carries.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence

import httpx

__all__ = [
    "API_VERSION",
    "SalesforceClient",
    "client_from",
    "load_master_data",
    "aload_master_data",
    "write_record_tree",
    "awrite_record_tree",
    "delete_record",
    "adelete_record",
]

API_VERSION = "v67.0"


def soql_str(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


class SalesforceClient:
    def __init__(
        self,
        login_url: str,
        client_id: str,
        client_secret: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 60.0,
    ) -> None:
        if not (login_url and client_id and client_secret):
            raise ValueError("Salesforce needs login_url, client_id and client_secret")
        self._http = httpx.Client(transport=transport, timeout=timeout)
        token = self._http.post(
            login_url.rstrip("/") + "/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if token.status_code >= 300:
            raise RuntimeError(
                f"Salesforce token request failed {token.status_code}: {token.text[:300]}"
            )
        body = token.json()
        self.instance_url: str = body["instance_url"].rstrip("/")
        self.base = f"{self.instance_url}/services/data/{API_VERSION}"
        self._http.headers.update(
            {
                "Authorization": f"Bearer {body['access_token']}",
                "Content-Type": "application/json",
            }
        )

    def query(self, soql: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        response = self._http.get(f"{self.base}/query", params={"q": soql})
        response.raise_for_status()
        body = response.json()
        out.extend(body["records"])
        while not body.get("done", True):
            next_url = body["nextRecordsUrl"]
            if not next_url.startswith("http"):
                next_url = self.instance_url + next_url
            response = self._http.get(next_url)
            response.raise_for_status()
            body = response.json()
            out.extend(body["records"])
        return out

    def create(self, sobject: str, record: Mapping[str, Any]) -> str:
        response = self._http.post(f"{self.base}/sobjects/{sobject}", json=dict(record))
        if response.status_code >= 300:
            raise RuntimeError(
                f"create {sobject} failed {response.status_code}: {response.text[:300]}"
            )
        return str(response.json()["id"])

    def update(self, sobject: str, record_id: str, record: Mapping[str, Any]) -> None:
        response = self._http.patch(
            f"{self.base}/sobjects/{sobject}/{record_id}", json=dict(record)
        )
        if response.status_code >= 300:
            raise RuntimeError(
                f"update {sobject} {record_id} failed {response.status_code}: {response.text[:300]}"
            )

    def delete(self, sobject: str, record_id: str) -> None:
        response = self._http.delete(f"{self.base}/sobjects/{sobject}/{record_id}")
        if response.status_code >= 300 and response.status_code != 404:
            raise RuntimeError(
                f"delete {sobject} {record_id} failed {response.status_code}: {response.text[:300]}"
            )

    def close(self) -> None:
        self._http.close()


def client_from(
    value: Any, *, transport: Optional[httpx.BaseTransport] = None
) -> SalesforceClient:
    """A client from a write-target slot value (mapping) with ``SF_*`` filling gaps."""
    raw: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    env = os.environ
    return SalesforceClient(
        str(
            raw.get("login_url") or raw.get("my_domain") or env.get("SF_LOGIN_URL", "")
        ),
        str(
            raw.get("client_id")
            or raw.get("consumer_key")
            or env.get("SF_CLIENT_ID", "")
        ),
        str(
            raw.get("client_secret")
            or raw.get("consumer_secret")
            or env.get("SF_CLIENT_SECRET", "")
        ),
        transport=transport,
    )


def _parse_description(text: Optional[str]) -> Dict[str, Any]:
    """``UoM: DR; Aliases: MEK 55 gal drum|MEK drum`` -> uom and aliases."""
    out: Dict[str, Any] = {"uom": None, "aliases": []}
    for part in str(text or "").split(";"):
        key, _, value = part.partition(":")
        key = key.strip().lower()
        if key == "uom":
            out["uom"] = value.strip() or None
        elif key == "aliases":
            out["aliases"] = [a.strip() for a in value.split("|") if a.strip()]
    return out


def _parse_parts(text: Optional[str]) -> Dict[str, str]:
    """``bristolcoatings.example=306727;lycomingfab.example=MRO-000601`` -> domain -> part."""
    parts: Dict[str, str] = {}
    for pair in str(text or "").split(";"):
        domain, _, part = pair.partition("=")
        if domain.strip() and part.strip():
            parts[domain.strip().lower()] = part.strip()
    return parts


def load_master_data(
    client: SalesforceClient,
    *,
    roles: Optional[Sequence[str]] = None,
    history_days: int = 400,
) -> Dict[str, Any]:
    """The normalised master data for the roles asked for (all five by default), plus the
    standard pricebook id the Order needs. Rows are exactly the shape ``documents.matching``
    documents."""
    wanted = set(
        roles or ("customer", "contact", "ship_to", "product", "order_history")
    )
    master: Dict[str, Any] = {}

    if wanted & {"customer", "ship_to", "contact", "order_history"}:
        accounts = client.query(
            "SELECT Id, Name, ParentId, Email_Domain__c, ShippingStreet, ShippingCity, "
            "ShippingState, ShippingPostalCode, ShippingCountry FROM Account "
            "WHERE Email_Domain__c != null"
        )
        parents = [a for a in accounts if not a.get("ParentId")]
        if "customer" in wanted:
            master["customer"] = [
                {
                    "id": a["Id"],
                    "name": a["Name"],
                    "email_domain": str(a["Email_Domain__c"]).lower(),
                }
                for a in parents
            ]
        if "ship_to" in wanted:
            sites: List[Dict[str, Any]] = []
            for a in accounts:
                account_id = a.get("ParentId") or a["Id"]
                if a.get("ShippingStreet"):
                    sites.append(
                        {
                            "id": a["Id"],
                            "account_id": account_id,
                            "name": a["Name"],
                            "street": a.get("ShippingStreet"),
                            "city": a.get("ShippingCity"),
                            "state": a.get("ShippingState"),
                            "state_name": a.get("ShippingState"),
                            "postcode": a.get("ShippingPostalCode"),
                            "country": a.get("ShippingCountry"),
                        }
                    )
            master["ship_to"] = sites
    if "contact" in wanted:
        master["contact"] = [
            {
                "id": c["Id"],
                "email": str(c["Email"]).lower(),
                "account_id": c["AccountId"],
                "name": c["Name"],
            }
            for c in client.query(
                "SELECT Id, Name, Email, AccountId FROM Contact WHERE Email != null"
            )
        ]
    if "product" in wanted:
        entries = {
            e["Product2Id"]: e
            for e in client.query(
                "SELECT Id, Product2Id, UnitPrice FROM PricebookEntry WHERE Pricebook2.IsStandard = true AND IsActive = true"
            )
        }
        products: List[Dict[str, Any]] = []
        for p in client.query(
            "SELECT Id, Name, ProductCode, IsActive, Description, Restricted__c, Customer_Part_Number__c FROM Product2"
        ):
            described = _parse_description(p.get("Description"))
            entry = entries.get(p["Id"])
            products.append(
                {
                    "id": p["Id"],
                    "code": p.get("ProductCode"),
                    "name": p["Name"],
                    "uom": described["uom"],
                    "aliases": described["aliases"],
                    "restricted": bool(p.get("Restricted__c")),
                    "active": bool(p.get("IsActive")),
                    "part_numbers": _parse_parts(p.get("Customer_Part_Number__c")),
                    "pricebook_entry_id": entry["Id"] if entry else None,
                    "unit_price": entry.get("UnitPrice") if entry else None,
                }
            )
        master["product"] = products
        standard = client.query("SELECT Id FROM Pricebook2 WHERE IsStandard = true")
        master["pricebook_id"] = standard[0]["Id"] if standard else None
    if "order_history" in wanted:
        since = (date.today() - timedelta(days=history_days)).isoformat()
        master["order_history"] = [
            {
                "account_id": i["Order"]["AccountId"],
                "po_number": i["Order"].get("PoNumber"),
                "product_id": i.get("Product2Id"),
                "quantity": i.get("Quantity"),
                "date": i["Order"].get("EffectiveDate"),
                "order_id": i["OrderId"],
            }
            for i in client.query(
                "SELECT OrderId, Product2Id, Quantity, Order.AccountId, Order.PoNumber, Order.EffectiveDate "
                f"FROM OrderItem WHERE Order.EffectiveDate >= {since}"
            )
        ]
    return master


async def aload_master_data(
    client: SalesforceClient,
    *,
    roles: Optional[Sequence[str]] = None,
    history_days: int = 400,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        load_master_data, client, roles=roles, history_days=history_days
    )


def write_record_tree(
    client: SalesforceClient, tree: Mapping[str, Any]
) -> Dict[str, Any]:
    """Create the parent, then each child with the parent's id, then finish the status.
    Returns the ids written, parent first, in the order a rollback must reverse."""
    parent_id = client.create(str(tree["object"]), tree["fields"])
    written: List[Dict[str, Any]] = [{"object": tree["object"], "id": parent_id}]
    for child in tree.get("children") or []:
        fields = dict(child["fields"])
        fields[child["parent_field"]] = parent_id
        child_id = client.create(str(child["object"]), fields)
        written.append(
            {"object": child["object"], "id": child_id, "line_no": child.get("line_no")}
        )
    finish = tree.get("finish_status")
    if finish and finish != tree["fields"].get("Status"):
        client.update(str(tree["object"]), parent_id, {"Status": finish})
    return {
        "id": parent_id,
        "url": f"{client.instance_url}/{parent_id}",
        "written": written,
        "status": finish,
    }


async def awrite_record_tree(
    client: SalesforceClient, tree: Mapping[str, Any]
) -> Dict[str, Any]:
    return await asyncio.to_thread(write_record_tree, client, tree)


def delete_record(client: SalesforceClient, sobject: str, record_id: str) -> None:
    client.delete(sobject, record_id)


async def adelete_record(
    client: SalesforceClient, sobject: str, record_id: str
) -> None:
    await asyncio.to_thread(delete_record, client, sobject, record_id)
