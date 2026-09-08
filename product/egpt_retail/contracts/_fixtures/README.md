# Semantic read API fixtures

The stub ships with **no fixture**. Every read raises `FixtureNotLoaded` naming the fix,
rather than returning an empty result that a screen would render as if it worked.

That is deliberate. Seeding the stub from the POC's synthetic ecommerce payloads would have
handed Lane R plausible retail-shaped data that is not the customer's, and wiring built
against `products` and `order_items` would need re-doing when the real harvest lands. The
half that actually blocks Lane R is signatures, types and the semantics of an empty result,
and they have all three today.

## Writing a fixture

JSON mirroring the dataclasses in `egpt_retail/contracts/semantic.py`:

```json
{
  "snapshot": {"version": "2026-09-07.1", "harvested_at": "2026-09-07T02:00:00Z"},
  "tables": [
    {
      "name": "quote_requests",
      "schema": "silver",
      "row_count": 310,
      "concepts": ["QuoteRequest"],
      "columns": [
        {"name": "contact_id", "data_type": "text", "is_key": true},
        {"name": "email", "data_type": "text", "is_personal_data": true},
        {"name": "brand", "data_type": "text", "sampled_values": ["Blodgett", "Follett"]}
      ]
    }
  ],
  "concepts": [
    {
      "name": "QuoteRequest",
      "description": "A request from a prospective buyer for a price on a product.",
      "synonyms": ["RFQ", "quote"],
      "sql_filter": "qr.status = 'submitted'",
      "personal_data_fields": ["email"],
      "compliance_tags": ["pii"],
      "business_area": "sales"
    }
  ],
  "join_edges": [
    {"left_table": "quote_requests", "right_table": "deals",
     "on": "quote_requests.email = deals.quote_request_email",
     "cardinality": "many-to-one"}
  ]
}
```

Edges are undirected for traversal: declare each join once and the stub walks it either way.

`status` defaults to `accepted` on every object. Set it to `proposed` to exercise the
review path — those rows are then invisible unless a caller passes `include_proposed=True`.

## When the real data lands

Build the fixture from the harvest, not by hand. Until K4 exists, one written from the
client sample files is the closest thing available — and it must use their real table and
column names, so that what Lane R wires against is what they will get.
