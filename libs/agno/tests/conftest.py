"""Suite-wide test isolation for process-global state, scoped to `libs/agno/tests`.

Fixtures here are autouse and apply to every test under this package. `libs/egptctl/tests` and
`libs/agno_infra` are separate pytest roots and get no restore from this file.
"""

import pytest


@pytest.fixture(autouse=True)
def restore_the_global_logger(monkeypatch):
    """`Agent.run`/`Team.run`/workflow runs swap `egpt.utils.log` globals and never put them back.

    `use_agent_logger()`/`use_team_logger()`/`use_workflow_logger()` reassign the module-global
    `logger`, so any test that drives a live run leaves it pointing at `egpt-agent`, `egpt-team`,
    or `egpt-workflow` for every test that runs after it in the same worker process -- verified
    directly: a `Team.run()` leaves `egpt.utils.log.logger.name == "egpt-team"` with nothing to
    put it back. That is wrong on its own (code asking for "the" logger gets whichever component
    ran last, not what it configured), and is a live hazard for anything that later pins logger
    identity -- a `caplog.records[i].name` assertion, or a `logging.getLogger("egpt")` comparison.
    `set_log_level_to_debug()`/`set_log_level_to_info()`, called from the same run paths, mutate
    `debug_on`/`debug_level` the same way -- a run with `debug_mode=True` otherwise leaves DEBUG
    logging (and `log_debug()`'s gate) on for every later test on that worker. They also call
    `logging.getLogger(...).setLevel(...)` directly on the underlying `agent`/`team`/`workflow`
    stdlib loggers (`"egpt"`/`"egpt-team"`/`"egpt-workflow"`), a third leak this fixture restores
    alongside the other two. See `tests/unit/test_logger_isolation.py` for a direct regression
    test of this fixture.

    Snapshots and restores each global around every test *under `libs/agno/tests`* (this
    conftest's own scope -- `libs/egptctl/tests` and `libs/agno_infra` are separate pytest roots
    with no restore of their own), so the leak can no longer cross a test boundary within that
    tree, not just within whichever package happened to add a local copy of this fixture first.
    Function-scoped, so it cannot protect a module- or session-scoped fixture that itself drives a
    run and leaks into other modules -- no such fixture exists in this tree today, but one would
    escape this guard.
    """
    import logging

    import egpt.utils.log as _log

    monkeypatch.setattr(_log, "logger", _log.logger)
    monkeypatch.setattr(_log, "debug_on", _log.debug_on)
    monkeypatch.setattr(_log, "debug_level", _log.debug_level)

    for name in (_log.LOGGER_NAME, _log.TEAM_LOGGER_NAME, _log.WORKFLOW_LOGGER_NAME):
        stdlib_logger = logging.getLogger(name)
        monkeypatch.setattr(stdlib_logger, "level", stdlib_logger.level)
