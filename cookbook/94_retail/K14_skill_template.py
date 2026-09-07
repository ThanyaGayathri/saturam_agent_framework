"""K14 -- the skill template, proven by running an agent written against it.

The template's own verification, from the delivery plan, is two-sided: write one trivial
agent against the template and run it, then hand the template to Lane R and have them write
one without asking a question. This script is the first half. The second half is a
conversation, not a script.

The agent is a synonym proposer: the smallest real agent in the system, and a strict subset
of K8's enrichment proposer. If the template cannot carry this one it cannot carry the four
Lane K agents either.

Three things are checked, in order of what they would cost if wrong:

1. The prompt and the code agree about every closed set. A model that can only see a field
   name invents a plausible value for it.
2. The output parses into the declared schema, with every enum value drawn from its set.
3. The agent refuses correctly. An agent that cannot express "I don't know" in its schema
   will express it as a confident wrong answer.

Run:
    set -a && source azure_key.env && set +a
    .venvs/demo/bin/python cookbook/94_retail/K14_skill_template.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.agent import Agent  # noqa: E402
from egpt.integrations.models.azure import AzureOpenAI  # noqa: E402
from egpt_retail.skills.synonym_proposer import (  # noqa: E402
    Confidence,
    ProposalKind,
    SynonymProposalSet,
    closed_sets_match_prompt,
    enum_members,
)

SKILLS_DIR = Path(__file__).resolve().parents[2] / "product" / "egpt_retail" / "skills"
PROMPT = (SKILLS_DIR / "EXAMPLE.md").read_text()

print("K14 -- skill template")
print("=" * 78)

# --- 1. Prompt and code agree about the closed sets ------------------------------------

print()
print("1. Closed sets enumerated in the prompt")
print("-" * 78)
matches = closed_sets_match_prompt()
for field_name, ok in matches.items():
    print(f"  {'OK ' if ok else 'DRIFT'}  {field_name}")
print("  kind       :", ", ".join(enum_members(ProposalKind)))
print("  confidence :", ", ".join(enum_members(Confidence)))
if not all(matches.values()):
    print()
    print("  The prompt and the schema disagree. Fix before running the agent.")
    raise SystemExit(1)

# --- 2. The agent produces a conforming output -----------------------------------------

if not os.environ.get("AZURE_OPENAI_API_KEY"):
    print()
    print("AZURE_OPENAI_API_KEY is not set. Source azure_key.env and re-run.")
    raise SystemExit(1)

# Built once and reused for both calls below. Never construct an agent inside a loop.
agent = Agent(
    name="synonym_proposer",
    model=AzureOpenAI(id=os.environ["AZURE_OPENAI_DEPLOYMENT"]),
    instructions=PROMPT,
    output_schema=SynonymProposalSet,
)

CONCEPT = "SupplierReliability"
DESCRIPTION = (
    "A measure of whether a supplier delivers what was ordered, when it was promised. "
    "Built from delivery outcomes over a trailing window. Used to decide which suppliers "
    "to keep, which to put on notice, and where to dual-source."
)

print()
print("2. A run against the template")
print("-" * 78)
print("  concept:", CONCEPT)

response = agent.run(
    f"concept_name: {CONCEPT}\n"
    f"concept_description: {DESCRIPTION}\n"
    f"existing_synonyms: ['on-time delivery rate']"
)
result = response.content
assert isinstance(result, SynonymProposalSet), (
    f"expected SynonymProposalSet, got {type(result)}"
)

print("  parsed into:", type(result).__name__)
print("  concept echoed:", result.concept)
print("  proposals:", len(result.proposals))
print()
for proposal in result.proposals:
    print(
        f"    [{proposal.kind.value:<17} {proposal.confidence.value:<6}] {proposal.value}"
    )
    print(f"       {proposal.rationale}")

print()
print("  Checks")
within_cap = len(result.proposals) <= 8
no_reproposal = all(
    "on-time delivery rate" != p.value.lower() for p in result.proposals
)
all_rationales = all(p.rationale.strip() for p in result.proposals)
print("   ", "OK " if within_cap else "FAIL", "at most eight proposals")
print(
    "   ", "OK " if no_reproposal else "FAIL", "did not re-propose an existing synonym"
)
print("   ", "OK " if all_rationales else "FAIL", "every proposal carries a rationale")
print(
    "    OK  every enum value is in its closed set (pydantic would have refused otherwise)"
)

# --- 3. The agent refuses correctly ----------------------------------------------------

print()
print("3. Refusal on an insufficient input")
print("-" * 78)
refusal = agent.run(
    f"concept_name: {CONCEPT}\nconcept_description: \nexisting_synonyms: []"
)
refused = refusal.content
assert isinstance(refused, SynonymProposalSet), (
    f"expected SynonymProposalSet, got {type(refused)}"
)
print("  proposals:", len(refused.proposals))
print("  notes:", refused.notes or "(empty)")
print()
empty = len(refused.proposals) == 0
explained = bool(refused.notes.strip())
print(
    "   ", "OK " if empty else "FAIL", "returned an empty list rather than padding it"
)
print("   ", "OK " if explained else "FAIL", "said what was missing")

print()
print("=" * 78)
if all([within_cap, no_reproposal, all_rationales, empty, explained]):
    print("Template holds. Output conformed to the declared schema on both runs.")
else:
    print("Template did not hold. See the FAIL lines above.")
