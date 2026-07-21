"""Quarantine litellm unit tests from the PR gate.

The litellm optional extra is temporarily disabled in egpt[tests] pending PyPI
security review (see libs/agno/pyproject.toml).
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="Quarantined (EGPT-0.10): litellm extra disabled pending PyPI security review; run manually with egpt[litellm]",
)

pytest.importorskip("litellm")
