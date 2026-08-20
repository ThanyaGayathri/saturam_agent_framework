"""Unit coverage for the components-audit scope split's service-account bearer path (EGPT-2.6,
aicoe-tracker#170).

`test_components_rbac.py::build_client` stamps `request.state.scopes` directly through a
stand-in middleware and never calls `auth_dependency`/`_authenticate_service_account` -- so it
cannot prove that `auth.py`'s `components_audit_scope_enabled` forwarding into that function does
anything. This file drives a real PAT through the real dispatch chain instead, using `SqliteDb`
rather than Postgres so it runs in the PR-gating unit job -- unlike the Postgres-backed
integration coverage of the same dispatch in
`tests/integration/os/audit_postgres/test_lifecycle_audit.py`.

The deployment below deliberately configures NO `os_security_key` and NO JWT: either one would
install `AuthMiddleware`, whose own internal PAT dispatch authenticates the request and sets
`request.state.authenticated = True` -- which makes `auth_dependency` short-circuit
(`if getattr(request.state, "authenticated", False): return True`) before it ever reaches
`_authenticate_service_account`. Only a fully open instance (no middleware installed at all)
routes a PAT through this specific fallback, which is what makes `components_audit_scope_enabled`
forwarded on this line meaningful to test in isolation.
"""

import time

import pytest
from fastapi.testclient import TestClient

from egpt.db.schemas.service_accounts import ServiceAccount
from egpt.integrations.db.sqlite import SqliteDb
from egpt.os import EgptOS
from egpt.os.service_accounts import generate_token
from egpt.os.settings import EgptServingSettings

AGENT_CONFIG = {"model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}}


@pytest.fixture
def deployment(tmp_path):
    """A fully open EgptOS (no auth middleware at all) with the audit-scope split enabled, plus
    a created component. Exercises `auth.py`'s `_authenticate_service_account` no-middleware
    fallback specifically -- see module docstring."""
    db = SqliteDb(db_file=str(tmp_path / "audit-scope-sa.db"), id="os-db")
    app = EgptOS(
        db=db,
        id="audit-scope-service-account-test",
        settings=EgptServingSettings(components_audit_scope_enabled=True),
    ).get_app()
    client = TestClient(app)

    # No Authorization header needed: the instance has no auth configured at all, so gates
    # stay dormant for a plain request.
    created = client.post(
        "/components",
        json={
            "name": "agent-a",
            "component_id": "agent-a",
            "component_type": "agent",
            "config": AGENT_CONFIG,
            "stage": "draft",
        },
    )
    assert created.status_code == 201, created.text

    return {"db": db, "client": client}


def _mint(db, scopes):
    token, token_hash, prefix = generate_token()
    db.create_service_account(
        ServiceAccount(
            id=f"sa-{'-'.join(scopes)}",
            name="audit-scope-test-pat",
            token_hash=token_hash,
            token_prefix=prefix,
            scopes=list(scopes),
            created_at=int(time.time()),
        ).to_dict()
    )
    return token


class TestAuditScopeSplitViaRealServiceAccountDispatch:
    """aicoe-tracker#170, PR #106 review round 2: pins `auth.py`'s
    `components_audit_scope_enabled` forwarding into `_authenticate_service_account`.

    Verified this kills the mutation of hardcoding that forwarding to `False`:
    `test_components_audit_admits` then fails with 403 ("Required scope(s): components:read")
    instead of 200, because `authenticate_service_account_request`'s own internal scope check
    (not just the router-level `require_route_scopes` backstop) enforces per-route scopes here,
    and under the mutation it wrongly requires `components:read` -- which the audit-only PAT
    does not have. `test_components_read_alone_is_refused` alone does NOT kill this mutation:
    the router-level backstop is independently, correctly wired regardless of this line, so it
    refuses a components:read-only PAT either way. Both tests are kept because each is the one
    that would catch the bug in a different direction.
    """

    def test_components_read_alone_is_refused(self, deployment):
        pat = _mint(deployment["db"], scopes=["components:read"])

        response = deployment["client"].get("/components/agent-a/audit", headers={"Authorization": f"Bearer {pat}"})

        assert response.status_code == 403

    def test_components_audit_admits(self, deployment):
        pat = _mint(deployment["db"], scopes=["components:audit"])

        response = deployment["client"].get("/components/agent-a/audit", headers={"Authorization": f"Bearer {pat}"})

        assert response.status_code == 200
