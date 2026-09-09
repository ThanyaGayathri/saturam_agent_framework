"""R7's executors are registrable and coexist with R4's -- no database.

Mirrors test_r4_executors.py's own pattern for its three, plus the combined-set collision
check the delivery plan's registry convention exists to catch.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from egpt_retail.app.components import R7_EXECUTORS, build_registry
from egpt_retail.executors.registry import assert_registry_valid, check_registry
from egpt_retail.executors.warehouse import WAREHOUSE_EXECUTORS

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)


def test_the_three_executors_satisfy_the_registry_convention() -> None:
    assert check_registry(list(R7_EXECUTORS)) == ()
    assert len(R7_EXECUTORS) == 3


def test_check_preflight_is_async_since_it_reads_gold() -> None:
    """R7 shipped it as a plain def (no I/O). R5's gold_fresh reads the warehouse, so it is
    now under the registry's I/O exemption like record_provenance/deliver_in_app -- and still
    under one name."""
    from egpt_retail.executors.preflight import check_preflight

    assert inspect.iscoroutinefunction(check_preflight)
    assert check_preflight.__name__ == "check_preflight"


def test_record_provenance_and_deliver_in_app_are_async_under_one_name() -> None:
    from egpt_retail.executors.delivery import deliver_in_app
    from egpt_retail.executors.provenance import record_provenance

    for executor in (record_provenance, deliver_in_app):
        assert inspect.iscoroutinefunction(executor), executor.__name__
        assert not executor.__name__.startswith("a"), (
            f"{executor.__name__} looks like an async twin; executors take one name"
        )


def test_every_r7_executor_takes_exactly_step_input() -> None:
    for executor in R7_EXECUTORS:
        parameters = list(inspect.signature(executor).parameters)
        assert parameters == ["step_input"], (executor.__name__, parameters)


def test_the_registry_document_lists_every_r7_executor() -> None:
    body = _REGISTRY_DOC.read_text(encoding="utf-8")
    for executor in R7_EXECUTORS:
        assert f"`{executor.__name__}`" in body, (
            f"{executor.__name__} is registered in code but missing from EXECUTOR_REGISTRY.md's list"
        )


def test_r4_and_r7_executors_do_not_collide() -> None:
    """The combined set the registry actually holds -- this is the check
    EXECUTOR_REGISTRY.md's own convention exists to enforce before merge."""
    combined = list(WAREHOUSE_EXECUTORS) + list(R7_EXECUTORS)
    assert check_registry(combined) == ()
    names = [executor.__name__ for executor in combined]
    assert len(names) == len(set(names))


def test_build_registry_includes_all_three_r7_executors() -> None:
    registry = build_registry()
    names = {function.__name__ for function in registry.functions}
    assert {executor.__name__ for executor in R7_EXECUTORS} <= names
    assert_registry_valid(registry.functions)
