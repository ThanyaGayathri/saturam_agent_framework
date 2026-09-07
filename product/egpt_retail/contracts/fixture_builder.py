"""Build the contract fixture the phase-zero stub server serves.

Lane K's semantic read stub ships with **no fixture**, deliberately: seeding it from the POC's
synthetic ecommerce payloads would have taught Lane R shapes that are not the customer's.
Lane R's R3 needs the stub to serve *something* over HTTP so a screen can be pointed at the
contract, and client sample data is PII that was not vendored. So this fixture is derived
from what is ours: the shipped vocabularies (``ontology/vocabularies.json``, a product asset
under D9) supply the concepts' business areas, and the vendored KPI corpus
(``_fixtures/poc_kpis.json``) supplies the table names its formulas read and the metrics each
concept reaches. Nouns that belong to the product, never to a customer.

**Contract-shaped, not customer-shaped. Replace at K5.** Every example payload in the API
contract (``api/openapi/paths/semantic.yaml``) is drawn from this file, and the stub server
loads this same file, so "the stub moves with the contract" (change protocol rule 4) is
mechanical: regenerate, and both move together.

Two deliberate inclusions, so the contract's review path and three-outcome rule have live
examples rather than described ones:

* one **proposed** table (``web_sessions``) and one proposed column
  (``customers.email``, flagged personal data), invisible unless ``include_proposed`` is set;
* one pair of tables with **no** join path (``supplier_deliveries`` and ``product_reviews``),
  so ``find_join_path`` returning ``None`` is exercisable, distinct from the raise.

Additive under change-protocol rule 1: it adds a file, and it changes nothing about the stub
or the contract. Recorded in ``contracts/CHANGELOG.md``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from egpt_retail.contracts.vocabulary import load_vocabulary_set

__all__ = [
    "CONTRACT_FIXTURE_PATH",
    "POC_KPIS_PATH",
    "SNAPSHOT_VERSION",
    "build_contract_fixture",
    "abuild_contract_fixture",
    "write_contract_fixture",
    "awrite_contract_fixture",
    "fixture_matches_committed",
]

_FIXTURES_DIR = Path(__file__).resolve().parent / "_fixtures"
CONTRACT_FIXTURE_PATH = _FIXTURES_DIR / "r3_contract.json"
POC_KPIS_PATH = _FIXTURES_DIR / "poc_kpis.json"

#: Fixed, so the fixture is deterministic and the drift check can compare bytes. A real
#: harvest stamps its own; this one exists to make the contract executable.
SNAPSHOT_VERSION = "r3-contract.1"
HARVESTED_AT = "2026-09-07T02:00:00Z"

#: The concept each POC corpus key stands for, and the business area it belongs to. Areas
#: are validated against the shipped ``business_area`` vocabulary at build time, so a term
#: that is not retail-general cannot enter the fixture by accident.
_CONCEPTS: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    # (concept, business_area, description, synonyms)
    (
        "Customer",
        "customer",
        "A person or account that has placed at least one order.",
        ("buyer", "account"),
    ),
    (
        "CustomerSegment",
        "customer",
        "A named group of customers sharing a defining attribute, used to compare behaviour across groups.",
        ("segment", "cohort"),
    ),
    (
        "Order",
        "sales",
        "A completed purchase, with its line items, placed through any channel.",
        ("purchase", "transaction"),
    ),
    (
        "Product",
        "product",
        "An individual item for sale, identified by a SKU.",
        ("SKU", "item"),
    ),
    (
        "ProductCategory",
        "product",
        "A node of the product hierarchy under which products are grouped for range and margin decisions.",
        ("category", "range"),
    ),
    (
        "StockLevel",
        "inventory",
        "A band describing how much of a product is on hand relative to its reorder point. "
        "Used to decide what to reorder and what is overstocked.",
        ("inventory level", "stock position"),
    ),
    (
        "Supplier",
        "supply_chain",
        "A vendor that delivers products against purchase orders, measured on whether it delivers what was ordered when promised.",
        ("vendor",),
    ),
)

#: POC corpus key -> concept name above. The corpus uses ``Category`` for the product hierarchy.
_CORPUS_KEY_TO_CONCEPT = {
    "CustomerSegment": "CustomerSegment",
    "Product": "Product",
    "Category": "ProductCategory",
    "Supplier": "Supplier",
    "StockLevel": "StockLevel",
}

#: The columns the corpus formulas read, per table, plus each table's key. Declared rather
#: than parsed out of the SQL, because a regex over ``alias.column`` would silently miss a
#: column the day a formula is rewritten, and a fixture that quietly loses a column is the
#: failure mode the drift check exists to catch.
_TABLES: Mapping[str, Dict[str, Any]] = {
    "orders": {
        "description": "One row per order placed.",
        "concepts": ["Order"],
        "row_count": 12480,
        "columns": [
            ("order_id", "text", {"nullable": False, "is_key": True}),
            ("customer_id", "text", {"nullable": False}),
            (
                "order_date",
                "date",
                {"nullable": False, "sampled_values": ["2026-08-30", "2026-09-01"]},
            ),
            ("total_amount", "numeric", {}),
            ("channel", "text", {"sampled_values": ["web", "store"]}),
        ],
    },
    "order_items": {
        "description": "One row per line of an order.",
        "concepts": ["Order", "Product"],
        "row_count": 41255,
        "columns": [
            ("order_item_id", "text", {"nullable": False, "is_key": True}),
            ("order_id", "text", {"nullable": False}),
            ("product_id", "text", {"nullable": False}),
            ("quantity", "integer", {"nullable": False}),
            ("unit_price", "numeric", {"nullable": False}),
        ],
    },
    "customers": {
        "description": "One row per customer.",
        "concepts": ["Customer", "CustomerSegment"],
        "row_count": 3120,
        "columns": [
            ("customer_id", "text", {"nullable": False, "is_key": True}),
            ("segment", "text", {"sampled_values": ["loyal", "new", "lapsed"]}),
            ("age", "integer", {}),
            # An enrichment proposal awaiting review: flagged personal data, not yet confirmed.
            ("email", "text", {"is_personal_data": True, "status": "proposed"}),
        ],
    },
    "products": {
        "description": "One row per product.",
        "concepts": ["Product", "ProductCategory"],
        "row_count": 860,
        "columns": [
            ("product_id", "text", {"nullable": False, "is_key": True}),
            ("name", "text", {"nullable": False}),
            ("category_id", "text", {}),
            ("price", "numeric", {}),
            ("cost", "numeric", {}),
        ],
    },
    "inventory": {
        "description": "One row per product with its stock position.",
        "concepts": ["StockLevel", "Product"],
        "row_count": 860,
        "columns": [
            ("product_id", "text", {"nullable": False, "is_key": True}),
            ("qty_on_hand", "integer", {"nullable": False}),
            ("reorder_point", "integer", {}),
            ("stock_level", "text", {"sampled_values": ["low", "ok", "high"]}),
        ],
    },
    "supplier_deliveries": {
        "description": "One row per delivery against a purchase order.",
        "concepts": ["Supplier"],
        "row_count": 2210,
        "columns": [
            ("delivery_id", "text", {"nullable": False, "is_key": True}),
            ("supplier_id", "text", {"nullable": False}),
            ("expected_date", "date", {}),
            ("actual_date", "date", {}),
            ("status", "text", {"sampled_values": ["on_time", "late", "cancelled"]}),
        ],
    },
    "product_reviews": {
        "description": "One row per customer review of a product.",
        "concepts": ["Product"],
        "row_count": 5031,
        "columns": [
            ("review_id", "text", {"nullable": False, "is_key": True}),
            ("product_id", "text", {"nullable": False}),
            (
                "rating",
                "integer",
                {"nullable": False, "sampled_values": ["1", "3", "5"]},
            ),
        ],
    },
    # A whole table still awaiting review -- invisible unless include_proposed is set.
    "web_sessions": {
        "description": "One row per web session, from the analytics connector.",
        "concepts": [],
        "row_count": None,
        "status": "proposed",
        "columns": [
            ("session_id", "text", {"nullable": False, "is_key": True}),
            ("customer_id", "text", {}),
        ],
    },
}

#: Declared once; the stub walks each either way. supplier_deliveries and product_reviews
#: are deliberately left disconnected from each other.
_JOIN_EDGES: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "customers",
        "orders",
        "customers.customer_id = orders.customer_id",
        "one-to-many",
    ),
    ("orders", "order_items", "orders.order_id = order_items.order_id", "one-to-many"),
    (
        "order_items",
        "products",
        "order_items.product_id = products.product_id",
        "many-to-one",
    ),
    (
        "products",
        "inventory",
        "products.product_id = inventory.product_id",
        "one-to-one",
    ),
    (
        "products",
        "product_reviews",
        "products.product_id = product_reviews.product_id",
        "one-to-many",
    ),
)

#: The scoping predicate each concept carries, as a PREDICATE parameter would interpolate it.
_SQL_FILTERS = {
    "CustomerSegment": "c.segment = {segment_name}",
    "StockLevel": "i.stock_level = {stock_level_name}",
    "Supplier": "sd.supplier_id = {supplier_id}",
    "Product": "p.product_id = {product_id}",
    "ProductCategory": "p.category_id = {category_id}",
}

_QUESTION_PATTERNS = {
    "StockLevel": ["what needs reordering?", "which products are overstocked?"],
    "Supplier": ["which suppliers are late?", "who should we dual-source?"],
    "CustomerSegment": ["how do segments compare on basket size?"],
}


def _metrics_by_concept(corpus: Mapping[str, Any]) -> Dict[str, List[str]]:
    """``<concept_lower>__<name>`` per concept, exactly as ``kpi.kpi_identifier`` forms them."""
    metrics: Dict[str, List[str]] = {}
    for corpus_key, formulas in corpus.items():
        concept = _CORPUS_KEY_TO_CONCEPT.get(corpus_key)
        if concept is None:
            continue
        metrics[concept] = [f"{concept.lower()}__{f['name']}" for f in formulas]
    return metrics


def _corpus_tables(corpus: Mapping[str, Any]) -> set:
    tables: set = set()
    for formulas in corpus.values():
        for formula in formulas:
            tables.update(t.lower() for t in formula.get("source_tables", ()))
    return tables


def build_contract_fixture(
    corpus_path: Path = POC_KPIS_PATH,
    vocabulary_core_path: Path | None = None,
) -> Dict[str, Any]:
    """Assemble the fixture payload. Pure apart from reading the two inputs."""
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    vocabularies = (
        load_vocabulary_set(core_path=vocabulary_core_path)
        if vocabulary_core_path
        else load_vocabulary_set()
    )

    # Every accepted table the corpus reads must be in the fixture, or the KPI examples in
    # the contract would not be feasible against the stub by construction.
    declared = {
        name
        for name, table in _TABLES.items()
        if table.get("status", "accepted") == "accepted"
    }
    missing = _corpus_tables(corpus) - declared
    if missing:
        raise ValueError(
            f"corpus reads tables the fixture does not declare: {sorted(missing)}"
        )

    metrics = _metrics_by_concept(corpus)

    tables = []
    for name, table in _TABLES.items():
        columns = []
        for column_name, data_type, extra in table["columns"]:
            column: Dict[str, Any] = {"name": column_name, "data_type": data_type}
            column.update(extra)
            columns.append(column)
        entry: Dict[str, Any] = {
            "name": name,
            "schema": "silver",
            "row_count": table["row_count"],
            "description": table["description"],
            "concepts": list(table["concepts"]),
            "columns": columns,
        }
        if table.get("status"):
            entry["status"] = table["status"]
        tables.append(entry)

    concepts = []
    for concept, area, description, synonyms in _CONCEPTS:
        vocabularies.validate("business_area", area)
        entry = {
            "name": concept,
            "description": description,
            "synonyms": list(synonyms),
            "question_patterns": _QUESTION_PATTERNS.get(concept, []),
            "sql_filter": _SQL_FILTERS.get(concept, ""),
            "personal_data_fields": ["email"] if concept == "Customer" else [],
            "compliance_tags": ["pii"] if concept == "Customer" else [],
            "metrics": metrics.get(concept, []),
            "business_area": area,
        }
        concepts.append(entry)

    join_edges = [
        {"left_table": left, "right_table": right, "on": on, "cardinality": cardinality}
        for left, right, on, cardinality in _JOIN_EDGES
    ]

    return {
        "_comment": [
            "Contract-shaped, not customer-shaped. Replace at K5.",
            "Generated by egpt_retail.contracts.fixture_builder from ontology/vocabularies.json",
            "and contracts/_fixtures/poc_kpis.json. Do not edit by hand; regenerate.",
        ],
        "snapshot": {
            "version": SNAPSHOT_VERSION,
            "harvested_at": HARVESTED_AT,
            "source": "warehouse",
        },
        "vocabulary_version": vocabularies.version,
        "tables": tables,
        "concepts": concepts,
        "join_edges": join_edges,
    }


async def abuild_contract_fixture(
    corpus_path: Path = POC_KPIS_PATH,
    vocabulary_core_path: Path | None = None,
) -> Dict[str, Any]:
    """Async variant. The reads happen off the event loop."""
    return await asyncio.to_thread(
        build_contract_fixture, corpus_path, vocabulary_core_path
    )


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_contract_fixture(path: Path = CONTRACT_FIXTURE_PATH) -> Path:
    """Build and write the committed fixture. Returns the path written."""
    path.write_text(_dump(build_contract_fixture()), encoding="utf-8")
    return path


async def awrite_contract_fixture(path: Path = CONTRACT_FIXTURE_PATH) -> Path:
    """Async variant."""
    return await asyncio.to_thread(write_contract_fixture, path)


def fixture_matches_committed(
    path: Path = CONTRACT_FIXTURE_PATH,
) -> Tuple[bool, str | None]:
    """Whether the committed fixture equals a fresh build. Used by the drift check."""
    if not path.exists():
        return False, f"{path} does not exist; run ./scripts/gen_api_models.sh"
    if path.read_text(encoding="utf-8") != _dump(build_contract_fixture()):
        return False, f"{path} is stale; run ./scripts/gen_api_models.sh"
    return True, None
