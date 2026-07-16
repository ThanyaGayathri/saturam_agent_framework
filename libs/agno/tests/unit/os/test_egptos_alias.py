"""Tests for deprecated AgentOS alias."""

from __future__ import annotations

import warnings


def test_agentos_alias_emits_deprecation_warning() -> None:
    import egpt.os as os_module

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        alias = os_module.AgentOS
        assert alias.__name__ == "EgptOS"

    assert any("AgentOS is deprecated" in str(item.message) for item in caught)


def test_egptos_is_primary_export() -> None:
    from egpt.os import EgptOS

    assert EgptOS.__name__ == "EgptOS"
