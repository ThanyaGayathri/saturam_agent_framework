"""K24: a template drafted from the customer's own sample against the shipped mapping.
The shipped purchase-order mapping reads ``delivery_instructions`` and ``line.line_due_date``;
a template drafted from a customer's PO has ``notes`` and ``requested_delivery_date``
instead. The platform names what the mapping reads that the template never extracts,
offers a shipped mapping only when it fits, suggests one over the drafted names otherwise,
and knows ``part_no`` for what it is.
"""

from __future__ import annotations

from egpt_retail.documents.mapping import (
    SHIPPED_MAPPINGS,
    mapping_from,
    stale_sources,
    suggest_mapping,
    suggest_objects,
)
from egpt_retail.documents.roles import resolution_sources
from egpt_retail.documents.templates import template_from

DRAFTED = {
    "id": "e02_purchase_order",
    "label": "E-02 purchase order",
    "record": "order",
    "header": [
        "po_number*",
        "po_date*",
        "customer_name*",
        "requested_delivery_date",
        "buyer_email",
        "ship_to_address*",
        "bill_to_address",
        "currency*",
        "payment_terms",
        "total",
        "notes",
    ],
    "lines": [
        "line_no*",
        "part_no",
        "description*",
        "quantity*",
        "unit_of_measure",
        "unit_price",
        "line_total",
    ],
}

ORDER = {
    "name": "Order",
    "fields": [
        {
            "name": "AccountId",
            "type": "reference",
            "reference_to": ["Account"],
            "required": True,
        },
        {"name": "PoNumber", "label": "PO Number", "type": "string", "required": False},
        {"name": "PoDate", "label": "PO Date", "type": "date"},
        {
            "name": "EffectiveDate",
            "label": "Order Start Date",
            "type": "date",
            "required": True,
        },
        {
            "name": "Description",
            "label": "Description",
            "type": "textarea",
            "length": 255,
        },
        {"name": "Status", "type": "picklist", "required": True},
    ],
}
ORDER_ITEM = {
    "name": "OrderItem",
    "fields": [
        {
            "name": "OrderId",
            "type": "reference",
            "reference_to": ["Order"],
            "required": True,
        },
        {
            "name": "PricebookEntryId",
            "type": "reference",
            "reference_to": ["PricebookEntry"],
            "required": True,
        },
        {"name": "Quantity", "label": "Quantity", "type": "double", "required": True},
        {
            "name": "UnitPrice",
            "label": "Unit Price",
            "type": "currency",
            "required": True,
        },
        {"name": "ServiceDate", "label": "Date", "type": "date"},
        {
            "name": "Description",
            "label": "Line Description",
            "type": "string",
            "length": 255,
        },
    ],
}
OBJECTS = [
    {"name": "Order", "label": "Order"},
    {"name": "OrderItem", "label": "Order Product"},
    {"name": "Account", "label": "Account"},
]


class TestStaleSources:
    def test_the_shipped_mapping_fits_the_shipped_template(self):
        assert (
            stale_sources(
                SHIPPED_MAPPINGS["order_orderitem"], template_from("purchase_order")
            )
            == []
        )

    def test_the_shipped_mapping_names_what_a_drafted_template_lacks(self):
        stale = stale_sources(
            SHIPPED_MAPPINGS["order_orderitem"], template_from(DRAFTED)
        )
        assert {s["source"] for s in stale} == {
            "delivery_instructions",
            "line.line_due_date",
            "due_date",
        }
        assert {(s["object"], s["target"]) for s in stale} == {
            ("Order", "Description"),
            ("OrderItem", "ServiceDate"),
        }
        assert not any(s["required"] for s in stale)

    def test_resolved_roles_run_values_and_literals_are_never_stale(self):
        stale = stale_sources(SHIPPED_MAPPINGS["opportunity"], template_from(DRAFTED))
        assert {s["source"] for s in stale} == {"due_date", "delivery_instructions"}


class TestSuggestObjects:
    def test_a_fitting_shipped_mapping_is_offered_as_is(self):
        rows = suggest_objects(template_from("purchase_order"), OBJECTS)
        order = next(r for r in rows if r["object"] == "Order")
        assert order["child"] == "OrderItem"
        assert order["shipped_mapping"] == "order_orderitem"

    def test_a_shipped_mapping_that_does_not_fit_is_withheld_with_the_reason(self):
        rows = suggest_objects(template_from(DRAFTED), OBJECTS)
        order = next(r for r in rows if r["object"] == "Order")
        assert order["child"] == "OrderItem"
        assert order["shipped_mapping"] is None
        assert (
            "delivery_instructions" in order["why"]
            and "line.line_due_date" in order["why"]
        )


class TestSuggestMappingOverDraftedNames:
    def test_the_drafted_names_land_on_the_order_fields(self):
        template = template_from(DRAFTED)
        fields = {
            "header": [n for n, _ in template.header],
            "lines": [n for n, _ in template.lines],
        }
        out = suggest_mapping(fields, ORDER, ORDER_ITEM)
        by = {r["target"]: r["source"] for r in out["fields"]}
        assert by["PoNumber"] == ["po_number"]
        assert by["PoDate"] == ["po_date"]
        assert by["Description"] == ["notes"]
        child = {r["target"]: r["source"] for r in out["child"]["fields"]}
        assert child["ServiceDate"] == [
            "requested_delivery_date"
        ]  # the header's, per line
        assert child["Description"] == ["line.description"]
        assert child["UnitPrice"] == ["line.unit_price"]
        assert stale_sources(mapping_from(out), template) == []


class TestPartNumbers:
    def test_part_no_is_the_line_product_code(self):
        sources = resolution_sources(template_from(DRAFTED))
        assert sources["product"]["supplier_product_code"] == "part_no"
        assert sources["product"]["description"] == "description"

    def test_item_no_and_part_are_too(self):
        for name in ("item_no", "part", "item_number", "part_number"):
            template = template_from(
                {
                    "label": "t",
                    "header": ["po_number*"],
                    "lines": [name, "description*"],
                }
            )
            assert (
                resolution_sources(template)["product"]["supplier_product_code"] == name
            )
