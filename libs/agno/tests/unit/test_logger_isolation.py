"""Regression test for the suite-wide autouse fixture in ``tests/conftest.py`` (aicoe-tracker#184).

``use_team_logger()``/``use_workflow_logger()`` reassign the module-global ``egpt.utils.log.logger``
(and ``set_log_level_to_debug()``/``set_log_level_to_info()`` reassign ``debug_on``/``debug_level``,
plus call ``logging.getLogger(...).setLevel(...)`` directly on the underlying stdlib logger, the
same way) and never restore them, so a test that drives a live ``Team`` run leaks them into every
test that runs after it. ``restore_the_global_logger`` (``tests/conftest.py``) snapshots and
restores each global around every test, so no test observes a leak from an earlier one.

A pair of order-dependent tests in *this* file would only prove the fixture works when nothing
reshuffles or isolates them -- xdist's default ``--dist load`` scheduling, ``pytest --lf``, or a
bare ``-k`` selecting only the second test can put them on different workers or run one without
the other, and CI's own ``-n auto --dist loadfile`` avoiding that today is not something this test
file states or depends on explicitly. Using ``pytester`` (pytest's own plugin for testing
fixtures/plugins) instead: it runs the *actual* fixture, unmodified, from ``tests/conftest.py``
inside a nested pytest process, so the ordering guarantee under test is real pytest fixture
teardown semantics rather than this file's own test-collection order -- the same pass/fail outcome
regardless of how the outer suite invokes pytest.
"""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

_CONFTEST_PATH = Path(__file__).resolve().parents[1] / "conftest.py"


def test_restore_the_global_logger_survives_a_leaking_test(pytester: pytest.Pytester) -> None:
    """Runs the real ``tests/conftest.py`` fixture, unmodified, against two tests in a nested
    pytest session: the first simulates what a live ``Team.run()`` with ``debug_mode=True`` does
    to ``logger``/``debug_on``/``debug_level``/the stdlib ``"egpt-team"`` logger's level, the
    second asserts nothing leaked into it. Fails if ``restore_the_global_logger`` is deleted,
    disabled, or any one of its four restores is silently dropped.
    """
    pytester.makeconftest(_CONFTEST_PATH.read_text(encoding="utf-8"))
    pytester.makepyfile("""
        import logging

        import egpt.utils.log as _log

        def test_a_leaks_the_run_path_globals():
            _log.use_team_logger()
            _log.set_log_level_to_debug(source_type="team", level=2)
            assert _log.logger.name == "egpt-team"
            assert _log.debug_on is True
            assert _log.debug_level == 2
            assert logging.getLogger("egpt-team").level == logging.DEBUG

        def test_b_nothing_leaked_from_the_prior_test():
            assert _log.logger is _log.agent_logger, "the global logger leaked across a test boundary"
            assert _log.debug_on is False, "debug_on leaked across a test boundary"
            assert _log.debug_level == 1, "debug_level leaked across a test boundary"
            assert logging.getLogger("egpt-team").level == logging.INFO, (
                "the stdlib egpt-team logger's level leaked across a test boundary"
            )
    """)

    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")

    result.assert_outcomes(passed=2)
