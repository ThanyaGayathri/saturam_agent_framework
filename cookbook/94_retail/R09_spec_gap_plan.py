"""R9 -- spec extraction, the deterministic gap check, and what each sentence becomes.

The delivery plan's Verify lines: type a plain sentence describing a use case; then one naming
a metric that does not exist; then one ambiguous about whether it should act. Evidence: the
spec fills from plain language; the unknown metric becomes a question rather than an invented
field; the ambiguous sentence produces a clarifying question rather than a guess.

Two halves. The gap check is deterministic and runs here against the contract fixture with no
service: three hand-written specs (what a correct extraction of each sentence looks like) go
through `assess_gaps`, and the fourth Verify line -- the plan -- is R09_plan_view.py. The
extraction itself needs a model: with `AZURE_OPENAI_API_KEY` sourced, the three sentences are
sent to the spec extractor on the small tier and every closed-set value it returns is checked
against the lists that were rendered into its prompt -- coined values are the failure this
skill exists to prevent, and `validate_spec` is what finds one.

    .venv/bin/python cookbook/94_retail/R09_spec_gap_plan.py                 # gap check
    set -a && source azure_key.env && set +a
    EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT=<small deployment> \\
      .venv/bin/python cookbook/94_retail/R09_spec_gap_plan.py               # plus extraction
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt_retail.api.models._generated import GapCheck, Spec  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH  # noqa: E402
from egpt_retail.contracts.kpi import KpiDefinition  # noqa: E402
from egpt_retail.contracts.semantic_stub import StubSemanticRead  # noqa: E402
from egpt_retail.contracts.vocabulary import load_vocabulary_set  # noqa: E402
from egpt_retail.executors.gaps import assess_gaps  # noqa: E402
from egpt_retail.routing.providers import (  # noqa: E402
    build_tier_providers,
    route_agents,
)
from egpt_retail.skills.spec_extractor import (  # noqa: E402
    UseCaseSpec,
    build_spec_extractor,
    closed_sets_from,
    closed_sets_match_prompt,
    spec_prompt,
    validate_spec,
)

READER = StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)
SNAPSHOT = READER.snapshot().version
NOW = datetime.now(timezone.utc)

# A registry over the fixture's tables: what K11's store would hold in this deployment.
KPIS = {
    k.identifier: k
    for k in (
        KpiDefinition(
            identifier="supplier__on_time_rate_30d",
            name="On-time rate, 30 days",
            description="",
            sql="SELECT supplier_id, count(*) FROM supplier_deliveries GROUP BY 1",
            grain="supplier",
            business_area="supply_chain",
            source_tables=frozenset({"supplier_deliveries"}),
        ),
        KpiDefinition(
            identifier="brand__quote_requests",
            name="Quote requests by brand",
            description="",
            sql="SELECT brand, count(*) FROM orders GROUP BY 1",
            grain="brand",
            business_area="sales",
            source_tables=frozenset({"orders"}),
        ),
        KpiDefinition(
            identifier="product__avg_rating",
            name="Average rating",
            description="",
            sql="SELECT avg(rating) FROM product_reviews JOIN web_sessions ON true",
            grain="product",
            business_area="product",
            source_tables=frozenset({"product_reviews", "web_sessions"}),
        ),
    )
}
SETS = closed_sets_from(READER, KPIS.values(), load_vocabulary_set())
DRAFT = {"step8": {"schedule": "daily", "recipient": "sales-ops@example.com"}}

# The three sentences, and what a correct extraction of each looks like: closed-set values
# only, the unknown metric as a question, the action verb as a question.
SENTENCES = {
    "plain": (
        "Which brands' quote requests fell this month?",
        UseCaseSpec(
            use_case_id="uc_plain",
            question="Which brands' quote requests fell this month?",
            business_area="sales",
            entities=[{"concept": "Order", "role": "subject"}],
            kpi_identifiers=["brand__quote_requests"],
            grain="brand",
            time_window={"length": 1, "unit": "month"},
            semantic_snapshot=SNAPSHOT,
        ),
    ),
    "unknown metric": (
        "Which brands' quote requests fell this month, and their average basket size?",
        UseCaseSpec(
            use_case_id="uc_unknown",
            question="Which brands' quote requests fell this month, and their average basket size?",
            business_area="sales",
            entities=[{"concept": "Order", "role": "subject"}],
            kpi_identifiers=["brand__quote_requests"],
            grain="brand",
            time_window={"length": 1, "unit": "month"},
            questions=[
                "'average basket size' matches no registered KPI; which KPI, if any, do you mean?"
            ],
            semantic_snapshot=SNAPSHOT,
        ),
    ),
    "ambiguous about acting": (
        "Which brands' quote requests fell this month, and pause the underperforming ones?",
        UseCaseSpec(
            use_case_id="uc_ambiguous",
            question="Which brands' quote requests fell this month, and pause the underperforming ones?",
            business_area="sales",
            entities=[{"concept": "Order", "role": "subject"}],
            kpi_identifiers=["brand__quote_requests"],
            grain="brand",
            time_window={"length": 1, "unit": "month"},
            questions=[
                "'pause the underperforming ones' is an action; should this use case act, or only inform?"
            ],
            semantic_snapshot=SNAPSHOT,
        ),
    ),
}

checks = {}
print("R9 -- spec extraction and the deterministic gap check")
print("=" * 78)
print(
    f"semantic snapshot {SNAPSHOT}: {len(SETS.concepts)} concepts, {len(SETS.kpi_identifiers)} registered KPIs"
)
print(f"prompt enums match code: {closed_sets_match_prompt()}")
checks["the prompt enumerates its enums in full"] = all(
    closed_sets_match_prompt().values()
)

# --- 1. The gap check over the three hand-written specs --------------------------------------
print()
print(
    "1. The gap check (deterministic, no model) over what a correct extraction looks like"
)
print("-" * 78)
for label, (sentence, expected) in SENTENCES.items():
    payload = expected.to_contract()
    Spec.model_validate(payload)
    result = assess_gaps(
        payload, reader=READER, kpis=KPIS, draft=DRAFT, level=1, now=NOW
    )
    GapCheck.model_validate(result)
    print(f"  {label}: feasible={result['feasible']} questions={expected.questions}")
    for gap in result["gaps"]:
        print(f"      gap {gap['kind']:<18} {gap['subject']}: {gap['detail'][:70]}")
checks["a correct spec of each sentence is feasible and coins nothing"] = all(
    assess_gaps(
        e.to_contract(), reader=READER, kpis=KPIS, draft=DRAFT, level=1, now=NOW
    )["feasible"]
    and validate_spec(e, SETS) == ()
    for _, e in SENTENCES.values()
)
print("   ", "OK " if checks[list(checks)[-1]] else "FAIL", list(checks)[-1])

coined = UseCaseSpec(
    use_case_id="uc_bad",
    question="x",
    business_area="sales",
    entities=[{"concept": "Carrier", "role": "subject"}],
    kpi_identifiers=["brand__quote_requests", "brand__avg_basket_size"],
    grain="brand",
    semantic_snapshot=SNAPSHOT,
)
gaps = assess_gaps(
    coined.to_contract(),
    reader=READER,
    kpis=KPIS,
    draft={},
    autonomy="copilot",
    level=3,
    now=NOW,
)
print()
print(
    "  the near-miss (a coined KPI, an unknown concept, no schedule, Recommend shape):"
)
for gap in gaps["gaps"]:
    print(f"      gap {gap['kind']:<18} {gap['subject']}")
kinds = {g["kind"] for g in gaps["gaps"]}
checks[
    "a coined value is a gap, an unknown concept is a gap, the shape's slots are gaps"
] = {"blocked_kpi", "unknown_concept", "missing_parameter"} <= kinds and len(
    validate_spec(coined, SETS)
) == 2
print("   ", "OK " if checks[list(checks)[-1]] else "FAIL", list(checks)[-1])

# --- 2. The extraction, live ------------------------------------------------------------------
if not os.environ.get("AZURE_OPENAI_API_KEY"):
    print()
    print("=" * 78)
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("Offline half:", "PASS" if all(checks.values()) else "FAIL")
    print(
        "AZURE_OPENAI_API_KEY is not set; the extraction (section 2) is skipped. Source azure_key.env"
    )
    print("and set EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT to run it.")
    raise SystemExit(0 if all(checks.values()) else 1)

if "EGPT_RETAIL_AZURE_DEPLOYMENT" not in os.environ and os.environ.get(
    "AZURE_OPENAI_DEPLOYMENT"
):
    os.environ["EGPT_RETAIL_AZURE_DEPLOYMENT"] = os.environ["AZURE_OPENAI_DEPLOYMENT"]
settings = RetailSettings(
    retail_db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
)
agent = build_spec_extractor(SETS)
route_agents([agent], build_tier_providers(settings))

print()
print("2. The three sentences through the spec extractor (small tier)")
print("-" * 78)
for label, (sentence, expected) in SENTENCES.items():
    response = agent.run(spec_prompt(expected.use_case_id, sentence, SNAPSHOT))
    spec = response.content
    assert isinstance(spec, UseCaseSpec), f"{label}: got {type(spec)}"
    problems = validate_spec(spec, SETS)
    print(f"  {label} (served by {response.model}):")
    print(
        f"      area={spec.business_area} grain={spec.grain} kpis={spec.kpi_identifiers} entities={[(e.concept, e.role.value) for e in spec.entities]}"
    )
    print(
        f"      window={spec.time_window.model_dump() if spec.time_window else None} notes={spec.notes!r}"
    )
    for q in spec.questions:
        print(f"      question: {q}")
    if problems:
        print(f"      COINED: {problems}")
    checks[f"{label}: nothing coined"] = problems == ()
    checks[f"{label}: area and grain from the vocabulary, a registered KPI"] = (
        spec.business_area == "sales"
        and spec.grain == "brand"
        and spec.kpi_identifiers == ["brand__quote_requests"]
    )
    if label == "unknown metric":
        checks["unknown metric: a question, not an invented identifier"] = (
            bool(spec.questions) and "basket" in " ".join(spec.questions).lower()
        )
    if label == "ambiguous about acting":
        checks[
            "ambiguous: a clarifying question about acting, no delivery or action invented"
        ] = bool(spec.questions) and any(
            w in " ".join(spec.questions).lower()
            for w in ("act", "pause", "autonomy", "inform")
        )
    gap = assess_gaps(
        spec.to_contract(), reader=READER, kpis=KPIS, draft=DRAFT, level=1, now=NOW
    )
    print(
        f"      gap check: feasible={gap['feasible']} gaps={[g['kind'] for g in gap['gaps']]}"
    )

print()
print("=" * 78)
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print("Overall:", "PASS" if all(checks.values()) else "FAIL")
raise SystemExit(0 if all(checks.values()) else 1)
