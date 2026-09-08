"""K14, second half -- an agent Lane R wrote from the skill template alone.

The template's verification is two-sided. Lane K wrote a trivial agent against it and ran
it (K14_skill_template.py). The other half is: hand the template to Lane R and have them
write one without asking a question. This is that agent, and every question that came up
while writing it is in TEST_LOG.md rather than resolved in conversation, because the
questions are the finding.

The agent explains a join path. Its input already contains the answer -- a deterministic
lookup found a path or did not -- so the one thing it must never do is disagree with that
input. Four checks, in order of what they would cost if wrong:

1. The prompt and the code agree about the closed set.
2. The output parses into the declared schema, and `verdict` equals the deterministic rule.
3. The null-path case: the agent says not_joinable and invents no join.
4. The refusal: a missing input yields `unknown` and a `notes`, not a guess.

Run:
    set -a && source azure_key.env && set +a
    .venv/bin/python cookbook/94_retail/K14_join_path_explainer.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.agent import Agent  # noqa: E402
from egpt.integrations.models.azure import AzureOpenAI  # noqa: E402
from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH  # noqa: E402
from egpt_retail.contracts.semantic_stub import StubSemanticRead  # noqa: E402
from egpt_retail.skills.join_path_explainer import (  # noqa: E402
    JoinExplanation,
    JoinVerdict,
    closed_sets_match_prompt,
    verdict_for_path,
)

SKILLS_DIR = Path(__file__).resolve().parents[2] / "product" / "egpt_retail" / "skills"
PROMPT = (SKILLS_DIR / "join_path_explainer.md").read_text()

print("K14 (second half) -- join path explainer, written from the template alone")
print("=" * 78)

# --- 1. Prompt and code agree about the closed set --------------------------------------

print()
print("1. Closed set enumerated in the prompt")
print("-" * 78)
matches = closed_sets_match_prompt()
for field_name, ok in matches.items():
    print(
        f"  {'OK ' if ok else 'DRIFT'}  {field_name}: {', '.join(m.value for m in JoinVerdict)}"
    )
if not all(matches.values()):
    print("  The prompt and the schema disagree. Fix before running the agent.")
    raise SystemExit(1)

# --- The inputs come from the same fixture the stub serves -------------------------------

reader = StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)
snapshot = reader.snapshot()


def join_path_payload(from_table: str, to_table: str) -> dict:
    path = reader.find_join_path(from_table, to_table)
    if path is None:
        return {"path": None}
    return {
        "path": {
            "from_table": path.from_table,
            "to_table": path.to_table,
            "hops": path.hops,
            "intermediate_tables": list(path.intermediate_tables),
            "edges": [
                {
                    "left_table": e.left_table,
                    "right_table": e.right_table,
                    "on": e.on,
                    "cardinality": e.cardinality,
                }
                for e in path.edges
            ],
        }
    }


def prompt_for(from_table: str, to_table: str, join_path: object) -> str:
    return (
        f"from_table: {from_table}\n"
        f"to_table: {to_table}\n"
        f"snapshot: {json.dumps({'version': snapshot.version})}\n"
        f"join_path: {json.dumps(join_path) if join_path is not None else '(missing)'}"
    )


if not os.environ.get("AZURE_OPENAI_API_KEY"):
    print()
    print("AZURE_OPENAI_API_KEY is not set. Source azure_key.env and re-run.")
    raise SystemExit(1)

# Built once and reused for every call below. Never construct an agent inside a loop.
agent = Agent(
    name="join_path_explainer",
    model=AzureOpenAI(id=os.environ["AZURE_OPENAI_DEPLOYMENT"]),
    instructions=PROMPT,
    output_schema=JoinExplanation,
)

# --- 2. A found path: the verdict mirrors the input, twice -----------------------------------

print()
print("2. A found path (customers -> products), run twice")
print("-" * 78)
found_input = join_path_payload("customers", "products")
expected = verdict_for_path(found_input["path"])
runs = []
for attempt in (1, 2):
    response = agent.run(prompt_for("customers", "products", found_input))
    result = response.content
    assert isinstance(result, JoinExplanation), (
        f"expected JoinExplanation, got {type(result)}"
    )
    runs.append(result)
    print(
        f"  run {attempt}: verdict={result.verdict.value} hops={result.hops} route={result.route}"
    )
    print(f"         {result.explanation[:160]}")
    for caveat in result.caveats:
        print(f"         caveat: {caveat[:140]}")

route_ok = all(
    r.route == ["customers", "orders", "order_items", "products"] for r in runs
)
verdict_ok = all(r.verdict is expected for r in runs)
hops_ok = all(r.hops == 3 for r in runs)
stable = runs[0].verdict is runs[1].verdict and runs[0].route == runs[1].route
print()
print(
    "   ",
    "OK " if verdict_ok else "FAIL",
    f"verdict equals the deterministic rule ({expected.value})",
)
print(
    "   ",
    "OK " if route_ok else "FAIL",
    "route is the four tables along the path, in order",
)
print("   ", "OK " if hops_ok else "FAIL", "hops copied unchanged")
print(
    "   ",
    "OK " if stable else "FAIL",
    "two runs on identical input agree on verdict and route",
)

# --- 3. A null path: not_joinable, and no invented join -------------------------------------

print()
print("3. A null path (supplier_deliveries -> product_reviews)")
print("-" * 78)
null_input = join_path_payload("supplier_deliveries", "product_reviews")
null_result = agent.run(
    prompt_for("supplier_deliveries", "product_reviews", null_input)
).content
assert isinstance(null_result, JoinExplanation)
print(
    f"  verdict={null_result.verdict.value} hops={null_result.hops} route={null_result.route}"
)
print(f"  {null_result.explanation[:160]}")
null_ok = (
    null_result.verdict is JoinVerdict.NOT_JOINABLE
    and null_result.route == []
    and null_result.hops == 0
)
# Structural, not lexical: the near-miss this check exists for is exactly "product_id"
# dressed up in a sentence that also contains "not" (which contains the substring "no"),
# e.g. "...on product_id, though this is not in the graph." -- a check gated on the
# presence of the bare substring "no" cannot fail on it. `caveats` is checked too: a
# caveat about cardinality or an intermediate table implies a route that was never there
# to have one. The tokens are the join columns this fixture's edges actually use
# (fixture_builder._JOIN_EDGES); none of them has any business appearing in an explanation
# of why *no* path exists.
_JOIN_COLUMN_TOKENS = ("product_id", "customer_id", "order_id")
invented = null_result.caveats != [] or any(
    token in null_result.explanation.lower() for token in _JOIN_COLUMN_TOKENS
)
print("   ", "OK " if null_ok else "FAIL", "not_joinable, empty route, zero hops")
print(
    "   ", "OK " if not invented else "FAIL", "did not invent a join from a column name"
)

# --- 4. A missing input: unknown, with notes -------------------------------------------------

print()
print("4. Refusal on a missing join_path")
print("-" * 78)
refusal = agent.run(prompt_for("customers", "products", None)).content
assert isinstance(refusal, JoinExplanation)
print(f"  verdict={refusal.verdict.value} notes={refusal.notes or '(empty)'}")
refused_ok = (
    refusal.verdict is JoinVerdict.UNKNOWN
    and bool(refusal.notes.strip())
    and refusal.route == []
)
print(
    "   ",
    "OK " if refused_ok else "FAIL",
    "unknown, empty route, and notes say what was missing",
)

print()
print("=" * 78)
checks = {
    "verdict equals the deterministic rule": verdict_ok,
    "route follows the path": route_ok,
    "hops copied": hops_ok,
    "two runs agree": stable,
    "null path is not_joinable with nothing invented": null_ok and not invented,
    "missing input refuses as unknown with notes": refused_ok,
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    print("Template did not hold for Lane R's agent:")
    for name in failed:
        print("  -", name)
    raise SystemExit(1)
print("Template holds. Lane R authored a conforming agent from the template alone.")
