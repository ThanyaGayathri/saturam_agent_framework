"""Executor registry -- the collision it prevents, shown against the real Registry.

Executors resolve by the Python function's __name__ over a flat list, first match wins, and
nothing warns on a duplicate. This demonstrates that against the framework's own Registry,
then shows the check catching it before it can reach a running deployment.

Run:
    .venv/bin/python cookbook/94_retail/K00_executor_registry.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.registry.registry import Registry  # noqa: E402
from egpt.workflow.types import StepInput, StepOutput  # noqa: E402
from egpt_retail.executors.registry import (  # noqa: E402
    InvalidExecutorRegistry,
    assert_registry_valid,
    check_registry,
    executor_names,
)

print("Executor registry -- P0 convention")
print("=" * 78)


# Two lanes, each writing a deterministic step, neither aware of the other's name.
def _lane_k_version():
    def check_freshness(step_input: StepInput) -> StepOutput:
        return StepOutput(content="Lane K: gold rows stamped within 24h")

    return check_freshness


def _lane_r_version():
    def check_freshness(step_input: StepInput) -> StepOutput:
        return StepOutput(content="Lane R: silver load completed tonight")

    return check_freshness


lane_k = _lane_k_version()
lane_r = _lane_r_version()

print()
print("1. The collision, against the framework's own Registry")
print("-" * 78)
registry = Registry(functions=[lane_k, lane_r])
print("  registered names:", executor_names(registry.functions))
resolved = registry.get_function("check_freshness")
assert resolved is not None
print("  get_function('check_freshness') ->", resolved(StepInput()).content)
print("  it is Lane K's:", resolved is lane_k)
print("  Lane R's is unreachable, and nothing warned.")

print()
print("2. What a stored workflow holds")
print("-" * 78)
print("  Step.to_dict stores executor_ref = executor.__name__, so the string")
print("  'check_freshness' is cemented into every stored workflow that uses it.")
print("  A rename therefore breaks all of them at once; a duplicate silently")
print("  reroutes them to whichever function was registered first.")

print()
print("3. The check")
print("-" * 78)
problems = check_registry(registry.functions)
for problem in problems:
    print("  ", problem)

collision_caught = False
try:
    assert_registry_valid(registry.functions)
except InvalidExecutorRegistry as exc:
    collision_caught = True
    print()
    print("  assert_registry_valid refuses to start:")
    print("   ", str(exc).splitlines()[0])
if not collision_caught:
    raise SystemExit(
        "A duplicate executor name was accepted -- the check does not work."
    )
if not any(problem.kind == "duplicate-name" for problem in problems):
    raise SystemExit("The duplicate was not reported as a duplicate-name problem.")

print()
print("4. Other shapes it catches")
print("-" * 78)


def takes_two(step_input: StepInput, extra: str) -> StepOutput:
    return StepOutput(content="")


def wrong_parameter_name(payload: StepInput) -> StepOutput:
    return StepOutput(content="")


def _private_looking(step_input: StepInput) -> StepOutput:
    return StepOutput(content="")


for problem in check_registry(
    [takes_two, wrong_parameter_name, _private_looking, lambda x: x]
):
    print("  ", problem)

print()
print("5. A conforming set passes")
print("-" * 78)


def record_provenance(step_input: StepInput) -> StepOutput:
    return StepOutput(content="provenance recorded")


async def evaluate_alert(step_input: StepInput) -> StepOutput:
    """Async is fine: the engine detects it, and it stays one name."""
    return StepOutput(content="alert evaluated")


good = [record_provenance, evaluate_alert]
assert_registry_valid(good)
print("  ", executor_names(good), "-> accepted")
print("   one executor, one name -- an async twin would double a namespace that")
print("   cannot survive ambiguity, for a choice nobody makes.")

malformed = check_registry(
    [takes_two, wrong_parameter_name, _private_looking, lambda x: x]
)
expected_kinds = {"signature", "parameter-name", "private-name", "unnamed"}
found_kinds = {problem.kind for problem in malformed}
if not expected_kinds <= found_kinds:
    raise SystemExit(
        f"Malformed shapes not all caught: missing {sorted(expected_kinds - found_kinds)}"
    )

print()
print("=" * 78)
print("Convention is enforceable. Wire assert_registry_valid into the app factory.")
