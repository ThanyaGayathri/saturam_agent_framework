"""Contract tests for the Enterprise GPT API -- G18's "contract tests against the API written in P0".

They run against the stub, which is the contract executing. Nothing here needs a service.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from egpt_retail.api.spec import load_bundle
from egpt_retail.api.stub import create_app, mint_token

SECRET = "contract-tests-only"


@pytest.fixture(scope="session")
def bundle():
    return load_bundle()


@pytest.fixture(scope="session")
def client():
    """No scope gating: the examples themselves are under test."""
    return TestClient(create_app())


@pytest.fixture(scope="session")
def gated_client():
    """Scope gating on, so 200 versus 403 is under test."""
    return TestClient(create_app(jwt_secret=SECRET))


@pytest.fixture(scope="session")
def unloaded_client():
    """An unloaded semantic reader: every semantic read is the 503 outcome."""
    return TestClient(create_app(fixture_path=None))


def bearer(*scopes: str) -> dict:
    return {"Authorization": f"Bearer {mint_token(SECRET, scopes)}"}
