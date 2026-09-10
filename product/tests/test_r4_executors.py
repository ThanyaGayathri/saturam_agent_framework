"""R4 without a warehouse -- the executors are registrable and the workflow is wired.

None of these calls an executor: they all do I/O. What is checkable without a database is
that the six conform to the registry convention, that the document listing them has not
rotted, and that the workflow is assembled with the error handling the design depends on.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from egpt.workflow.types import OnError

from egpt_retail.app.components import (
    LANDING_TO_SILVER_WORKFLOW_ID,
    build_registry,
    build_workflows,
)
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.registry import assert_registry_valid, check_registry
from egpt_retail.executors.warehouse import WAREHOUSE_EXECUTORS, selected_paths

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)


def test_the_six_executors_satisfy_the_registry_convention() -> None:
    assert check_registry(list(WAREHOUSE_EXECUTORS)) == ()
    assert len(WAREHOUSE_EXECUTORS) == 6


def test_every_executor_is_async_under_one_name() -> None:
    """The registry's exemption: an I/O executor is `async def`, with no sync twin.

    The flat namespace cannot survive `check_x` and `acheck_x` both existing, and the
    workflow engine -- the only caller -- handles both forms already.
    """
    for executor in WAREHOUSE_EXECUTORS:
        assert inspect.iscoroutinefunction(executor), executor.__name__
        assert not executor.__name__.startswith("a"), (
            f"{executor.__name__} looks like an async twin; executors take one name"
        )


def test_every_executor_takes_exactly_step_input() -> None:
    for executor in WAREHOUSE_EXECUTORS:
        parameters = list(inspect.signature(executor).parameters)
        assert parameters == ["step_input"], (executor.__name__, parameters)


def test_the_registry_document_lists_every_executor() -> None:
    """A registry nobody updates is one people stop reading -- the document says so itself."""
    body = _REGISTRY_DOC.read_text(encoding="utf-8")
    for executor in WAREHOUSE_EXECUTORS:
        assert f"`{executor.__name__}`" in body, (
            f"{executor.__name__} is registered in code but missing from "
            "EXECUTOR_REGISTRY.md's list"
        )


def test_the_worked_collision_example_is_still_unused() -> None:
    """`check_freshness` is the document's own example of a silent collision."""
    assert "check_freshness" not in {e.__name__ for e in WAREHOUSE_EXECUTORS}


def test_build_registry_registers_and_validates_them() -> None:
    """The registry also holds R7's three (see test_r7_executors.py) -- this only pins that
    the six warehouse names are a subset, not the whole set, so R4's own test doesn't have to
    know about every other package that registers executors."""
    registry = build_registry()
    names = {function.__name__ for function in registry.functions}
    assert {executor.__name__ for executor in WAREHOUSE_EXECUTORS} <= names
    assert_registry_valid(registry.functions)


def test_the_workflow_has_the_six_steps_in_order() -> None:
    settings = RetailSettings()
    workflows = build_workflows(settings, build_control_plane_db(settings))
    # R5 registers nightly-refresh beside it; this test is about the hand-run chain alone.
    workflow = next(w for w in workflows if w.id == LANDING_TO_SILVER_WORKFLOW_ID)
    assert [step.executor.__name__ for step in workflow.steps] == [
        executor.__name__ for executor in WAREHOUSE_EXECUTORS
    ]


def test_every_step_fails_rather_than_skipping() -> None:
    """Step.on_error defaults to OnError.skip, whatever the comment above it says.

    Left at the default, a step that raised would be skipped and the run would carry on to
    the next one -- and, once R5 appends a gold step, on to rebuilding gold from a load that
    never completed. The gates never raise, so this only bites on an infrastructure failure,
    which is exactly when it matters.
    """
    settings = RetailSettings()
    workflow = build_workflows(settings, build_control_plane_db(settings))[0]
    for step in workflow.steps:
        assert step.on_error == OnError.fail, step.name


def test_the_verdict_step_is_last_because_it_is_the_one_that_blocks() -> None:
    settings = RetailSettings()
    workflow = build_workflows(settings, build_control_plane_db(settings))[0]
    assert workflow.steps[-1].executor.__name__ == "decide_load_verdict"


def test_an_explicit_empty_path_list_means_zero_files_not_everything() -> None:
    """`[]` is falsy, and a truthiness test turned "load nothing" into "load everything".

    A caller restricting a run to zero files instead got an unrestricted full load. The
    distinction lives in a pure helper so it can be pinned here rather than only through a
    live warehouse.
    """
    assert selected_paths({"paths": []}) == []
    assert selected_paths({}) is None
    assert selected_paths({"paths": None}) is None
    assert selected_paths({"paths": ["raw/a.ndjson"]}) == ["raw/a.ndjson"]
