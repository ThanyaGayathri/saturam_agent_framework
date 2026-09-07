"""K11 -- validate the KPI formula shape against real formulas.

Runs the nineteen formulas from the ecom_graphrag_poc semantic layer through the shape
agreed in P0 and prints every disagreement. A formula that will not construct as a
KpiDefinition is a defect in one of the two, and this is how we find out which.

Run:
    python cookbook/94_retail/K11_kpi_formula_shape.py
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt_retail.contracts.kpi_check import (  # noqa: E402
    Severity,
    check_corpus,
    corpus_size,
    formulas_clearing_the_shape,
    load_poc_corpus,
)

corpus = load_poc_corpus()
findings = check_corpus(corpus)
total = corpus_size(corpus)
clearing = formulas_clearing_the_shape(corpus)

print("KPI formula shape -- validation against the POC corpus")
print("=" * 78)
print("Formulas checked:", total, "across", len(corpus), "concepts")
print("Construct unchanged:", len(clearing), "of", total)
print("Findings:", len(findings))
print()

print("Findings by severity")
print("-" * 78)
for severity in Severity:
    hits = [f for f in findings if f.severity is severity]
    print(f"  {len(hits):>3}  {severity.value}")
    for rule, count in Counter(f.rule.value for f in hits).most_common():
        print(f"         {count:>3}  {rule}")
print()

print("Per formula")
print("-" * 78)
for concept, records in corpus.items():
    for record in records:
        name = record["name"]
        hits = [f for f in findings if f.concept == concept and f.kpi_name == name]
        incompatible = [h for h in hits if h.severity is Severity.INCOMPATIBLE]
        status = (
            "INCOMPATIBLE"
            if incompatible
            else ("METADATA" if any(h.blocks_construction for h in hits) else "OK")
        )
        print(f"\n  [{status}] {concept}.{name}")
        for hit in hits:
            print(f"      - {hit.rule.value}: {hit.detail}")
print()
print("=" * 78)
incompatible_formulas = {
    f"{f.concept}.{f.kpi_name}" for f in findings if f.severity is Severity.INCOMPATIBLE
}
if not incompatible_formulas:
    print("Every formula is expressible in the shape once metadata is filled in.")
else:
    print(
        len(incompatible_formulas),
        "of",
        total,
        "formulas are not expressible as written:",
    )
    for name in sorted(incompatible_formulas):
        print("   ", name)


# --- Migration and feasibility ---------------------------------------------------------

from egpt_retail.contracts.kpi import (  # noqa: E402  # noqa: E402
    KpiParameter,
    KpiRegistry,
    ParameterKind,
    assess_registry,
    enums_match_vocabulary,
)
from egpt_retail.contracts.kpi_check import migrate_corpus  # noqa: E402
from egpt_retail.contracts.vocabulary import (  # noqa: E402
    TermOrigin,
    load_vocabulary_set,
)

print()
print("Migration into the shape")
print("-" * 78)
try:
    registry = migrate_corpus(corpus)
except Exception as exc:  # noqa: BLE001 -- the point is to show which formula refused
    print("  FAILED:", exc)
    raise SystemExit(1)
print("  Constructed:", len(registry), "of", total, "KPI definitions")

vocabularies = load_vocabulary_set()
print()
print("  Shipped vocabularies, version", vocabularies.version)
for name in ("grain", "business_area"):
    vocab = vocabularies[name]
    print(
        f"    {name:<14} {len(vocab):>2} terms  ({len(vocab.of_origin(TermOrigin.OVERLAY))} from overlay)"
    )
drift = enums_match_vocabulary(vocabularies)
print("  Enums still agreeing with the vocabulary:", all(drift.values()), drift)

# The registry is where grain and business_area are actually checked.
validated = KpiRegistry.build(registry, vocabularies)
print("  Registry validated every term:", len(validated), "definitions")
print(
    "  Rowset (not a metric):",
    sum(1 for k in registry if k.result_shape.value == "rowset"),
)
print(
    "  Needing dialect translation:",
    sum(1 for k in registry if k.dialect.value != "postgres"),
)

# Feasibility: a set difference against the tables a semantic snapshot holds.
all_tables = frozenset(t for k in registry for t in k.source_tables)
print()
print("Feasibility -- full snapshot")
print("-" * 78)
verdicts = assess_registry(registry, all_tables)
for verdict in ("ready", "partial", "blocked"):
    print(
        f"  {sum(1 for v in verdicts.values() if v.verdict.value == verdict):>3}  {verdict}"
    )

dropped = "orders"
print()
print("Feasibility -- with", repr(dropped), "dropped from the snapshot")
print("-" * 78)
verdicts = assess_registry(registry, all_tables - {dropped})
for verdict in ("ready", "partial", "blocked"):
    hits = [k for k, v in verdicts.items() if v.verdict.value == verdict]
    print(f"  {len(hits):>3}  {verdict}")
    for name in sorted(hits)[:6]:
        if verdict != "ready":
            print(f"         {name}: {verdicts[name].rationale}")

# The two-missing case the package calls out explicitly.
two_missing = assess_registry(registry, all_tables - {"orders", "order_items"})
worst = max(two_missing.values(), key=lambda v: len(v.missing_tables))
print()
print("Two tables missing -- does it name both?")
print("-" * 78)
print(" ", worst.kpi_identifier, "->", sorted(worst.missing_tables))
# Assert, do not merely print: these cookbooks are this lane's only tests, so a check that
# prints a wrong answer and exits 0 is a check that will not be believed when it matters.
assert worst.missing_tables == {"orders", "order_items"}, (
    f"expected both missing tables named, got {sorted(worst.missing_tables)}"
)


# --- The overlay, which is why grain is not a Python enum -------------------------------
#
# A customer whose business has a grain the shipped ontology lacks must be able to add it
# without a release (D9, G10). This is that mechanism, end to end.

import json  # noqa: E402
import tempfile  # noqa: E402

from egpt_retail.contracts.kpi import KpiDefinition  # noqa: E402

print()
print("Customer overlay")
print("-" * 78)

overlay = {
    "vocabularies": {
        "grain": [
            {
                "value": "quote_request",
                "description": "One row per request for a quote.",
            },
            {
                "value": "rep_group",
                "description": "One row per sales representative group.",
            },
        ]
    }
}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
    json.dump(overlay, handle)
    overlay_path = Path(handle.name)

extended = load_vocabulary_set(overlay_path=overlay_path)
grain = extended["grain"]
print(
    "  grain terms:",
    len(grain),
    "->",
    len(grain.of_origin(TermOrigin.OVERLAY)),
    "from the overlay",
)
print("  added:", sorted(t.value for t in grain.of_origin(TermOrigin.OVERLAY)))

# A KPI at the customer's own grain now validates, without a code change.
customer_kpi = KpiDefinition(
    identifier="quote_requests_by_brand",
    name="quote_requests_by_brand",
    description="Quote requests received, by brand.",
    sql="SELECT COUNT(*) FROM quote_requests WHERE {brand_filter}",
    grain="quote_request",
    business_area="sales",
    source_tables=frozenset({"quote_requests"}),
    parameters=(
        KpiParameter(
            name="brand_filter",
            kind=ParameterKind.PREDICATE,
            description="Brand scope.",
        ),
    ),
)
KpiRegistry.build([customer_kpi], extended)
print("  A KPI at grain 'quote_request' validates:", customer_kpi.identifier)

# The same KPI against the shipped vocabulary alone must be refused.
try:
    KpiRegistry.build([customer_kpi], vocabularies)
except ValueError as exc:
    print("  Without the overlay it is refused:")
    print("   ", str(exc).split(".")[0])

# An overlay may add. It may not redefine.
conflicting = {
    "vocabularies": {
        "grain": [{"value": "supplier", "description": "Something else entirely."}]
    }
}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
    json.dump(conflicting, handle)
    conflict_path = Path(handle.name)
redefinition_refused = False
try:
    load_vocabulary_set(overlay_path=conflict_path)
except ValueError as exc:
    redefinition_refused = True
    print("  Redefining a core term is refused:", str(exc).split(".")[0])

overlay_path.unlink()
conflict_path.unlink()

if not redefinition_refused:
    raise SystemExit("G10 violated: an overlay redefining a core term was accepted.")
if "quote_request" not in extended["grain"].values:
    raise SystemExit("The overlay did not extend the grain vocabulary.")
if incompatible_formulas != {
    f"{f.concept}.{f.kpi_name}" for f in findings if f.severity is Severity.INCOMPATIBLE
}:
    raise SystemExit("Incompatible set changed between report and assertion.")
print()
print("All assertions passed.")
