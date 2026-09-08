"""The check that makes the executor naming convention enforceable.

Executors resolve by the Python function's ``__name__`` over a flat list
(``Registry.get_function``), first match wins, and nothing warns on a duplicate. Two people
building in parallel will eventually both write ``check_freshness``, and the symptom is not
an error -- it is one lane's workflow silently running the other lane's step.

So the convention needs a check rather than a paragraph. Run it before the registry is handed
to ``EgptOS``, next to K1's memory startup check, because both catch misconfigurations that
otherwise surface as a wrong answer rather than as a failure. **Wiring it there is P1 work**
-- this module provides the check; nothing calls it on a startup path yet.

Pure: it inspects callables already in memory. No I/O, so the sync/async rule does not apply.
"""

from __future__ import annotations

import inspect
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, List, Sequence, Tuple

__all__ = [
    "ExecutorProblem",
    "InvalidExecutorRegistry",
    "assert_registry_valid",
    "check_registry",
    "executor_names",
]

#: The parameter an executor receives. Checked by name rather than by type, because a module
#: importing ``StepInput`` only to satisfy a linter is worse than a string comparison here.
_EXPECTED_PARAMETER = "step_input"


@dataclass(frozen=True)
class ExecutorProblem:
    """One thing wrong with one executor, or with the set of them."""

    kind: str
    name: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.name}: {self.detail}"


class InvalidExecutorRegistry(ValueError):
    """The registry would resolve executors wrongly, or not at all."""

    def __init__(self, problems: Sequence[ExecutorProblem]) -> None:
        self.problems: Tuple[ExecutorProblem, ...] = tuple(problems)
        listed = "\n  ".join(str(problem) for problem in problems)
        super().__init__(
            f"{len(problems)} problem(s) with the executor registry:\n  {listed}\n"
            "Executors resolve by __name__ over a flat list, first match wins, and nothing "
            "warns on a duplicate -- so a collision runs the wrong step silently. "
            "See product/egpt_retail/contracts/EXECUTOR_REGISTRY.md."
        )


def executor_names(functions: Iterable[Callable[..., object]]) -> Tuple[str, ...]:
    """The names these executors will be stored and resolved under."""
    return tuple(getattr(function, "__name__", "<anonymous>") for function in functions)


def check_registry(
    functions: Sequence[Callable[..., object]],
) -> Tuple[ExecutorProblem, ...]:
    """Every problem with a set of executors, in the order they were registered.

    Returns findings rather than raising, so a caller can log them all at once. Use
    :func:`assert_registry_valid` on the startup path, where the right response is to refuse
    to start.
    """
    problems: List[ExecutorProblem] = []
    names = executor_names(functions)

    for name, count in Counter(names).items():
        if count > 1:
            problems.append(
                ExecutorProblem(
                    kind="duplicate-name",
                    name=name,
                    detail=(
                        f"registered {count} times. Registry.get_function returns the first "
                        "match, so the others are unreachable and no warning is emitted."
                    ),
                )
            )

    for function, name in zip(functions, names):
        if name == "<anonymous>" or name == "<lambda>":
            problems.append(
                ExecutorProblem(
                    kind="unnamed",
                    name=name,
                    detail="a lambda has no stable name, so no stored workflow can reference it.",
                )
            )
            continue

        if name.startswith("_"):
            problems.append(
                ExecutorProblem(
                    kind="private-name",
                    name=name,
                    detail="a leading underscore reads as private but is a public API here, since it is what a stored workflow stores.",
                )
            )

        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError):  # pragma: no cover -- builtins and C callables
            problems.append(
                ExecutorProblem(
                    kind="uninspectable",
                    name=name,
                    detail="signature could not be read.",
                )
            )
            continue

        parameters = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        required = [p for p in parameters if p.default is inspect.Parameter.empty]
        if len(required) != 1:
            problems.append(
                ExecutorProblem(
                    kind="signature",
                    name=name,
                    detail=(
                        f"takes {len(required)} required parameter(s); an executor takes exactly "
                        f"one, {_EXPECTED_PARAMETER}. The engine calls it with a StepInput and nothing else."
                    ),
                )
            )
        elif required[0].name != _EXPECTED_PARAMETER:
            problems.append(
                ExecutorProblem(
                    kind="parameter-name",
                    name=name,
                    detail=(
                        f"names its parameter {required[0].name!r}; the convention is "
                        f"{_EXPECTED_PARAMETER!r}, so every executor reads the same way."
                    ),
                )
            )

    return tuple(problems)


def assert_registry_valid(functions: Sequence[Callable[..., object]]) -> None:
    """Refuse to start on a registry that would resolve executors wrongly.

    **Not yet wired.** It belongs on the application-factory startup path, beside K1's memory
    provider check, and neither exists until P1 -- there is no factory in this repository to
    call it from today. Until then it is called only by
    ``cookbook/94_retail/K00_executor_registry.py``, so treat the guarantee as available
    rather than active: nothing currently stops a duplicate reaching a running deployment.

    A duplicate name is not a style problem. It means a stored workflow runs a step its author
    never wrote, and produces a plausible answer while doing so.
    """
    problems = check_registry(functions)
    if problems:
        raise InvalidExecutorRegistry(problems)
