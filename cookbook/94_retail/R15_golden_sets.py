"""R15 -- golden sets and the publish gate (D14, G13).

The delivery plan: a golden question set per agent skill, scored with the existing eval suite
and run on publish; rehearsal review by a person still gates activation. G13's fuller framing
is that this is not only the RFQ use case -- every agent skill in ``product/egpt_retail/
skills/`` gets one. See ``egpt_retail/evals/golden_sets.py`` for why these nine and not others.

This script:

1. Enumerates the real skills (no hypothetical ones).
2. Runs every skill's golden set live and records a rehearsal review row for each score.
3. Shows the deterministic gate (``check_golden_gate``) blocking an unreviewed score and
   allowing a reviewed, passing one -- for ``join_path_explainer``.
4. The Verify scenario itself: deliberately weakens ``join_path_explainer``'s prompt (the exact
   rule that forbids inventing a join for a null path) **in memory only** -- the checked-in
   ``join_path_explainer.md`` is read once for its text and never written -- re-runs the
   golden set against an agent built from the weakened text, and checks the score actually
   dropped and the gate actually caught it. A ``finally`` block then confirms the file on disk
   still matches what was read at the start, rather than writing the weakened text over it and
   restoring it afterwards (the original approach's failure mode: a SIGKILL, OOM or power loss
   between the write and the restore, or two people running this cookbook at once, could leave
   a corrupted production prompt on disk). If the weakened prompt does not produce a measurable
   drop, this script exits non-zero: per the package's own evidence bar, a golden set that
   cannot catch this is too easy and has to be strengthened before R15 is done.

Needs a Postgres for the rehearsal-review table (self-provisioning, like K7's ``ProposalStore``)
and a model. Azure is used if configured (matching every other cookbook in this repository);
otherwise this falls back to a local Ollama model -- still a real, live model call, not a mock,
chosen because this environment has no Azure/OpenAI credentials configured. Both paths exercise
the exact same golden-set and gate code.

Run:
    docker run -d --name egpt-retail-r15-postgres -e POSTGRES_DB=ai -e POSTGRES_USER=ai \\
      -e POSTGRES_PASSWORD=ai -p 5834:5432 agnohq/pgvector:18
    EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5834/ai \\
      .venvs/demo/bin/python -m egpt_retail.entrypoints migrate

    # Azure (matches every other R-cookbook):
    set -a && source azure_key.env && set +a
    EGPT_RETAIL_R15_DSN=postgresql://ai:ai@localhost:5834/ai \\
      .venvs/demo/bin/python cookbook/94_retail/R15_golden_sets.py

    # or, with no Azure credentials, a local Ollama model (`ollama pull qwen2.5-coder:7b`):
    EGPT_RETAIL_R15_DSN=postgresql://ai:ai@localhost:5834/ai \\
      .venvs/demo/bin/python cookbook/94_retail/R15_golden_sets.py
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt.agent import Agent  # noqa: E402
from egpt.workflow.types import StepInput, StepOutput  # noqa: E402
from egpt_retail.evals import golden_sets as gs  # noqa: E402
from egpt_retail.evals.golden import (  # noqa: E402
    GoldenSet,
    golden_pass_rate,
    run_golden_set,
    skill_prompt_version,
)
from egpt_retail.evals.rehearsal import (  # noqa: E402
    RehearsalReview,
    RehearsalReviewStore,
)
from egpt_retail.executors.golden_gate import check_golden_gate  # noqa: E402
from egpt_retail.skills.join_path_explainer import JoinExplanation  # noqa: E402

CONTROL_PLANE_DSN = os.environ.get(
    "EGPT_RETAIL_R15_DSN", "postgresql://ai:ai@localhost:5834/ai"
)
REVIEWER = "nandhakumar@saturam.com"


#: Each golden-set run (one skill, or the weakened/restored re-runs) drives its own
#: `asyncio.run()` (`egpt.eval.suite.run_cases`). An async model client's transport is bound to
#: the event loop it was first used on, so a single `Model` instance reused across several of
#: those `asyncio.run()` calls breaks the second time with "Event loop is closed". The fix is a
#: factory: a fresh `Model` per run, never one instance shared across runs. Construction is pure
#: (no I/O), so this is cheap.
def _select_model():
    """Returns (factory, label). Call the factory fresh for every `asyncio.run()`-scoped use --
    never reuse one `Model` instance across two golden-set runs."""
    if os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get(
        "AZURE_OPENAI_DEPLOYMENT"
    ):
        from egpt.integrations.models.azure import AzureOpenAI

        deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        return (lambda: AzureOpenAI(id=deployment)), f"Azure OpenAI ({deployment})"
    from egpt.integrations.models.ollama import Ollama

    model_id = os.environ.get("EGPT_RETAIL_GOLDEN_MODEL", "qwen2.5-coder:7b")
    return (
        (lambda: Ollama(id=model_id)),
        f"Ollama ({model_id}, local -- no AZURE_OPENAI_API_KEY/AZURE_OPENAI_DEPLOYMENT set)",
    )


#: The local model needs more than the suite's 120s default for the longer, vocabulary-rendered
#: prompts (enrichment_proposer, kpi_suggestion, use_case_suggestion). Azure is comfortably
#: faster; the same bound is used for both so one script has one number to reason about.
CASE_TIMEOUT_SECONDS = 240


def _run_gate(
    identifier: str,
    version: str,
    rate: float,
    total_cases: int,
    review: Optional[RehearsalReview],
) -> StepOutput:
    step_input = StepInput(
        additional_data={
            "skill_identifier": identifier,
            "skill_version": version,
            "golden_pass_rate": rate,
            "total_cases": total_cases,
            "review": review,
        }
    )
    return asyncio.run(check_golden_gate(step_input))


def _print_gate(gate: StepOutput) -> None:
    print(
        f"    allowed={gate.content['allowed']}  blocked_by={gate.content['blocked_by']}"
    )
    for check in gate.content["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"      {mark}  {check['name']:<20} {check['reason']}")


def _print_suite(result: Any) -> None:
    for case_result in result.results:
        mark = "OK  " if case_result.passed else "FAIL"
        detail = case_result.error or (case_result.judge_reason or "")
        print(f"      {mark} {case_result.name}: {detail[:140]}")


print("R15 -- golden sets and the publish gate")
print("=" * 78)

model_factory, model_label = _select_model()
print()
print(f"Model under test and judge: {model_label}")

# --- 1. Enumerate the real agent skills ---------------------------------------------------

print()
print("1. The real agent skills in product/egpt_retail/skills/")
print("-" * 78)
print(
    "Scope, per G13: every agent-backed skill that exists today, not only the RFQ use case."
    " step_prompt (K15) is excluded -- it authors other skills' prompts at compile time and"
    " has no worked answer a golden question could check."
)
for identifier, (_, _, prompt_file) in gs.SKILL_BUILDERS.items():
    print(f"  {identifier:<24} prompt={prompt_file}")

# --- 2. Baseline: every skill's golden set, scored live -----------------------------------

print()
print(
    "2. Baseline: every skill's golden set, scored live, one rehearsal review row each"
)
print("-" * 78)

connection = psycopg.connect(CONTROL_PLANE_DSN)
connection.autocommit = False
store = RehearsalReviewStore(connection)
store.ensure_schema()

# A clean slate for a repeatable demo: record_run() now preserves a prior decision when a
# re-run reproduces the identical (pass_rate, total_cases) -- correct (a CI re-run must not
# wipe out who approved this exact score), but it means a *repeat run of this very script*
# could find join_path_explainer already approved from last time if the local judge happens
# to land on the same discretised pass rate twice, and step 3 below would then not
# demonstrate the "before review: blocked" state it exists to show. Clearing this script's
# own rows first keeps the demo deterministic regardless of what an earlier run left behind.
with connection.cursor() as cursor:
    cursor.execute(
        "DELETE FROM retail_evals.rehearsal_review WHERE skill_identifier = ANY(%s)",
        (list(gs.SKILL_BUILDERS.keys()),),
    )
connection.commit()

baseline: dict = {}
for identifier, (build_agent, build_cases, prompt_file) in gs.SKILL_BUILDERS.items():
    agent = build_agent(model_factory())
    cases = build_cases(agent)
    prompt_text = (gs.SKILLS_DIR / prompt_file).read_text()
    version = skill_prompt_version(prompt_text)
    golden = GoldenSet(skill_identifier=identifier, skill_version=version, cases=cases)
    result = run_golden_set(
        golden, judge_model=model_factory(), default_timeout=CASE_TIMEOUT_SECONDS
    )
    rate = golden_pass_rate(result)
    baseline[identifier] = (version, rate, result)
    # golden.skill_identifier/skill_version, not the identifier/version loop variables --
    # the GoldenSet's own attribution fields are what gets recorded, not a hand-carried copy
    # of them that merely happens to agree by construction.
    review = store.record_run(
        golden.skill_identifier,
        golden.skill_version,
        pass_rate=rate,
        total_cases=result.total,
    )
    print(
        f"  {identifier:<24} v{version}  {result.passed}/{result.total} passed ({rate:.0%})"
        f"  review={review.status.value}"
    )
    _print_suite(result)

# --- 3. The gate: unreviewed blocks, reviewed-and-passing allows --------------------------

print()
print("3. The publish gate for join_path_explainer's baseline score")
print("-" * 78)

jp_version, jp_rate, jp_result = baseline["join_path_explainer"]

print("  before review:")
review = store.get("join_path_explainer", jp_version)
gate = _run_gate("join_path_explainer", jp_version, jp_rate, jp_result.total, review)
_print_gate(gate)
assert gate.content["allowed"] is False, "an unreviewed score must never publish"

approved = store.approve(
    "join_path_explainer",
    jp_version,
    REVIEWER,
    note="Baseline reproduces K14_join_path_explainer.py's own hand-run checks.",
)
print(f"  after {REVIEWER} approves:")
gate = _run_gate("join_path_explainer", jp_version, jp_rate, jp_result.total, approved)
_print_gate(gate)
if jp_rate >= 0.8:
    assert gate.content["allowed"] is True, "a passing, reviewed score should publish"

# --- 4. Verify: weaken the prompt, publish, watch the gate, restore -----------------------

print()
print(
    "4. Verify: weaken join_path_explainer's prompt, publish, watch the gate, restore"
)
print("-" * 78)

JOIN_MD = gs.SKILLS_DIR / "join_path_explainer.md"
original_text = JOIN_MD.read_text()

# The exact rule this agent exists to hold (join_path_explainer.md, "Specific to this agent"):
# never invent a join for a null path because the tables "look" joinable. Weakened into the
# kind of well-intentioned "be more helpful" edit that actually produces this failure mode --
# not a strawman deletion, the near-miss the skill's own worked example already names.
ORIGINAL_RULE = (
    "- `verdict` mirrors the input and nothing else. `path` present means `joinable`; `path` null\n"
    '  means `not_joinable`. You never emit `joinable` for a null path because the tables "look"\n'
    "  joinable, and never emit `not_joinable` for a present path because the route looks long."
)
WEAKENED_RULE = (
    "- `verdict` should reflect whether a join is meaningfully possible. If `path` is null but "
    "the table and column names suggest an obvious connection, use your judgment: explaining a "
    "plausible join is more useful to the person asking than a flat refusal."
)
if ORIGINAL_RULE not in original_text:
    print(
        "  The rule text this scenario targets has changed since this script was written."
    )
    connection.close()
    raise SystemExit(1)

weakened_text = original_text.replace(ORIGINAL_RULE, WEAKENED_RULE)
weakened_text = weakened_text.replace("version:       1.0.0", "version:       1.1.0")
assert weakened_text != original_text and "1.1.0" in weakened_text

exit_code = 0
try:
    # The weakened prompt lives only in memory -- join_path_explainer.md, the checked-in
    # production skill file, is never written. An earlier revision of this scenario wrote the
    # weakened text to disk and restored it in a `finally`, which works but carries a narrow,
    # real risk (SIGKILL/OOM/power loss between the write and the restore, or two people
    # running this cookbook concurrently) of leaving a corrupted production prompt on disk.
    # Nothing below actually needs the on-disk file mutated -- the agent is built directly
    # from `weakened_text` -- so the `finally` block instead proves the file was never
    # touched, rather than writing over it and restoring it.
    print(
        "  prompt weakened in memory only (not written to disk); version bumped 1.0.0 -> 1.1.0"
    )

    weakened_agent = Agent(
        name="join_path_explainer",
        model=model_factory(),
        instructions=weakened_text,
        output_schema=JoinExplanation,
    )
    weakened_cases = gs.join_path_explainer_golden_cases(weakened_agent)
    weakened_version = skill_prompt_version(weakened_text)
    weakened_golden = GoldenSet(
        skill_identifier="join_path_explainer",
        skill_version=weakened_version,
        cases=weakened_cases,
    )
    weakened_result = run_golden_set(
        weakened_golden,
        judge_model=model_factory(),
        default_timeout=CASE_TIMEOUT_SECONDS,
    )
    weakened_rate = golden_pass_rate(weakened_result)

    print()
    print(
        f"  weakened v{weakened_version}: {weakened_result.passed}/{weakened_result.total} passed "
        f"({weakened_rate:.0%})  --  baseline was v{jp_version} {jp_rate:.0%}"
    )
    _print_suite(weakened_result)

    weakened_review = store.record_run(
        weakened_golden.skill_identifier,
        weakened_golden.skill_version,
        pass_rate=weakened_rate,
        total_cases=weakened_result.total,
    )
    print()
    print(
        "  publish attempt on the weakened prompt (fresh version -> review reopens to pending):"
    )
    gate = _run_gate(
        "join_path_explainer",
        weakened_version,
        weakened_rate,
        weakened_result.total,
        weakened_review,
    )
    _print_gate(gate)

    score_dropped = weakened_rate < jp_rate
    caught_at_publish = not gate.content["allowed"]
    print()
    print(
        f"  score dropped (attributable to the edit): {score_dropped} ({jp_rate:.0%} -> {weakened_rate:.0%})"
    )
    print(f"  caught at publish (gate blocked):          {caught_at_publish}")
    if not score_dropped:
        print()
        print(
            "  The golden set did not catch the weakened prompt. Per this package's own evidence"
        )
        print(
            "  bar, that means the golden set is too easy and must be strengthened -- not that"
        )
        print("  the gate is fine as shipped.")
        exit_code = 1
finally:
    # Proof the checked-in file was never touched, rather than a write-then-restore.
    untouched = JOIN_MD.read_text() == original_text
    print()
    print(
        f"  checked-in prompt file untouched on disk: {'OK' if untouched else 'FAIL'}"
    )
    if not untouched:
        exit_code = 1

# --- 5. Confirm recovery: the restored prompt passes again --------------------------------

if exit_code == 0:
    print()
    print("5. Confirm recovery: the restored prompt, re-scored")
    print("-" * 78)
    restored_agent = Agent(
        name="join_path_explainer",
        model=model_factory(),
        instructions=original_text,
        output_schema=JoinExplanation,
    )
    restored_cases = gs.join_path_explainer_golden_cases(restored_agent)
    restored_golden = GoldenSet(
        skill_identifier="join_path_explainer",
        skill_version=jp_version,
        cases=restored_cases,
    )
    restored_result = run_golden_set(
        restored_golden,
        judge_model=model_factory(),
        default_timeout=CASE_TIMEOUT_SECONDS,
    )
    restored_rate = golden_pass_rate(restored_result)
    print(
        f"  restored v{jp_version}: {restored_result.passed}/{restored_result.total} passed ({restored_rate:.0%})"
    )
    _print_suite(restored_result)
    restored_review = store.record_run(
        restored_golden.skill_identifier,
        restored_golden.skill_version,
        pass_rate=restored_rate,
        total_cases=restored_result.total,
    )
    print(
        f"  rehearsal review reopened to {restored_review.status.value} for the restored score -- a person"
    )
    print(
        "  must confirm it again; the earlier approval of this same version does not carry over."
    )

connection.close()

print()
print("=" * 78)
if exit_code == 0:
    print(
        "R15 holds: the golden set caught the weakened prompt, and the gate blocked publish."
    )
else:
    print("R15 did not hold -- see above.")
raise SystemExit(exit_code)
