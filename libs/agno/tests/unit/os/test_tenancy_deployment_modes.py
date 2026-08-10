"""Which deployment shapes actually turn tenancy on, and who gets refused (EGPT-2.4).

`tenancy_enabled` reads `app.state.tenant_id_claim`, which `EgptOS` stamps eagerly so the very
first request is gated. Nothing pinned *which configurations produce a non-None claim*, and the
enforcement table in `docs/tenancy.md` guessed wrong: it called the security-key row
"unreachable in practice", reasoning that the key path only runs when JWT is unconfigured and
tenancy could not be on there. Setting `tenant_id_claim` without `authorization=True` does
exactly that (PR #55 review), so the row is reachable.

The refusal itself is deliberate and stays -- the key is an operator break-glass carrying no
tenant, and exempting it would serve its holder every tenant's rows. These tests pin the whole
matrix so the table is checked by the suite rather than by reasoning.
"""

import time

import pytest
from fastapi.testclient import TestClient

from egpt.db.schemas.service_accounts import ServiceAccount
from egpt.integrations.db.sqlite import SqliteDb
from egpt.os.app import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.service_accounts import generate_token

SECURITY_KEY = "test-security-key-for-tenancy-modes"
JWT_SECRET = "test-secret-key-for-tenancy-deployment-modes"
TENANT = "acme"


@pytest.fixture(autouse=True)
def security_key(monkeypatch):
    monkeypatch.setenv("EGPT_OS_SECURITY_KEY", SECURITY_KEY)


def build(tmp_path, name, **kwargs):
    db = SqliteDb(db_file=str(tmp_path / f"{name}.db"))
    return db, EgptOS(db=db, id="tenancy-modes", **kwargs).get_app()


def mint(db, tenant, scopes):
    token, token_hash, prefix = generate_token()
    db.create_service_account(
        ServiceAccount(
            id=f"sa-{tenant}",
            name=f"bot-{tenant}",
            token_hash=token_hash,
            token_prefix=prefix,
            scopes=list(scopes),
            created_at=int(time.time()),
            tenant_id=tenant,
        ).to_dict()
    )
    return token


def get_components(app, token):
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        return client.get("/components", headers={"Authorization": f"Bearer {token}"})


class TestTenancyIsOffWithoutAClaim:
    def test_a_security_key_deployment_stamps_no_claim(self, tmp_path):
        """The reported repro set `app.state.tenant_id_claim` by hand. EgptOS does not: with no
        AuthorizationConfig there is no claim to stamp, so tenancy stays off."""
        _, app = build(tmp_path, "seckey")
        assert app.state.tenant_id_claim is None

    def test_and_the_security_key_is_served(self, tmp_path):
        _, app = build(tmp_path, "seckey_serves")
        assert get_components(app, SECURITY_KEY).status_code == 200


class TestAClaimWithoutJwtTurnsTenancyOn:
    """The reachable configuration: a tenant claim with no verification key. `authorization`
    stays falsy, so the security-key branch is live *and* the claim is stamped."""

    @pytest.fixture
    def deployment(self, tmp_path):
        return build(tmp_path, "claim_no_jwt", authorization_config=AuthorizationConfig(tenant_id_claim="tid"))

    def test_the_claim_is_stamped(self, deployment):
        _, app = deployment
        assert app.state.tenant_id_claim == "tid"

    def test_the_security_key_is_refused(self, deployment):
        """Fail closed. The key carries no tenant, and unscoped is the widest tenancy there is."""
        _, app = deployment
        response = get_components(app, SECURITY_KEY)
        assert response.status_code == 403
        assert "no tenant identity" in response.json()["detail"]

    def test_a_tenanted_pat_is_served(self, deployment):
        """Why this warns instead of raising: PAT-only tenancy is a working deployment, so
        refusing to construct would break it."""
        db, app = deployment
        token = mint(db, TENANT, ["components:read"])
        assert get_components(app, token).status_code == 200

    def test_an_untenanted_pat_is_refused(self, deployment):
        db, app = deployment
        token = mint(db, None, ["components:read"])
        assert get_components(app, token).status_code == 403

    def test_the_operator_is_warned_at_startup(self, tmp_path, caplog):
        """The 403 blames the credential for what is usually a forgotten authorization=True."""
        build(tmp_path, "claim_warns", authorization_config=AuthorizationConfig(tenant_id_claim="tid"))
        assert any("tenant_id_claim is set but no JWT verification key" in r.message for r in caplog.records)

    def test_a_claim_with_jwt_does_not_warn(self, tmp_path, caplog):
        build(
            tmp_path,
            "claim_jwt_quiet",
            authorization=True,
            authorization_config=AuthorizationConfig(
                verification_keys=[JWT_SECRET], algorithm="HS256", verify_audience=False, tenant_id_claim="tid"
            ),
        )
        assert not any("tenant_id_claim is set but no JWT verification key" in r.message for r in caplog.records)


class TestAClaimWithJwt:
    """The intended configuration. The security key is not reachable here at all: with JWT
    configured the middleware decodes the credential as a token, so the key fails as a
    malformed JWT before any tenancy gate runs."""

    @pytest.fixture
    def app(self, tmp_path):
        _, app = build(
            tmp_path,
            "claim_with_jwt",
            authorization=True,
            authorization_config=AuthorizationConfig(
                verification_keys=[JWT_SECRET], algorithm="HS256", verify_audience=False, tenant_id_claim="tid"
            ),
        )
        return app

    def test_the_security_key_is_not_a_jwt(self, app):
        assert get_components(app, SECURITY_KEY).status_code == 401

    def test_a_jwt_without_the_claim_is_refused(self, app):
        import jwt as pyjwt  # noqa: PLC0415

        token = pyjwt.encode(
            {"sub": "u1", "scopes": ["components:read"], "exp": int(time.time()) + 600}, JWT_SECRET, algorithm="HS256"
        )
        assert get_components(app, token).status_code == 403

    def test_a_jwt_carrying_the_claim_is_served(self, app):
        import jwt as pyjwt  # noqa: PLC0415

        token = pyjwt.encode(
            {"sub": "u1", "tid": TENANT, "scopes": ["components:read"], "exp": int(time.time()) + 600},
            JWT_SECRET,
            algorithm="HS256",
        )
        assert get_components(app, token).status_code == 200
