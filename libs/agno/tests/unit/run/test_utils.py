"""Unit tests for egpt.run.utils — the sub-agent construction seam.

Mirrors tests/unit/tools/test_toolkit_layout.py's construct_toolkit pair
(test_toolkit_table_rows_resolve_without_a_shim, test_unknown_toolkit_name_is_rejected):
construct_runnable/get_runnable_class is the seam EGPT-1.7a's connector move pivots on, and
until now had no direct unit test — only an AST-only layout guard and a monkeypatched stub in
test_providers.py exercised it.
"""

import pytest

from egpt.agent import Agent
from egpt.run.utils import construct_runnable, get_runnable_class


def test_get_runnable_class_resolves_agent():
    assert get_runnable_class("agent") is Agent


def test_construct_runnable_builds_an_agent():
    runnable = construct_runnable("agent", name="probe")
    assert isinstance(runnable, Agent)
    assert runnable.name == "probe"


def test_unknown_runnable_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown runnable"):
        construct_runnable("definitely-not-a-runnable")
