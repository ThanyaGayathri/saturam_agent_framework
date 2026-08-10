"""Service accounts carry a tenant (EGPT-2.4).

Before this, `authenticate_service_account_request` set `request.state.tenant_id = None`
unconditionally, with a comment naming this ticket as the owner. `None` is *unscoped* -- the
widest tenancy -- so a machine token was a cross-tenant credential by construction.

Covered here: the column round-trips through the dataclass, the mint rule cannot be used to
escalate across tenants, listing and revocation are tenant-scoped, and the tenant reaches
`request.state` on an authenticated request.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.db.schemas.service_accounts import SERVICE_ACCOUNT_MUTABLE_COLUMNS, ServiceAccount
from egpt.os.routers.service_accounts import get_service_accounts_router
from egpt.os.scopes import AgentOSScope
from egpt.os.service_accounts import (
    DEFAULT_SERVICE_ACCOUNT_SCOPES,
    ServiceAccountVerification,
    VerificationStatus,
    authenticate_service_account_request,
)
from egpt.os.settings import EgptServingSettings

OWNER = "acme"
INTRUDER = "globex"


def _account_dict(**overrides):
    now = int(time.time())
    data = {
        "id": "sa-1",
        "name": "claude-code",
        "user_id": "admin-user",
        "tenant_id": OWNER,
        "token_hash": "a" * 64,
        "token_prefix": "agno_pat_abc1234",
        "scopes": list(DEFAULT_SERVICE_ACCOUNT_SCOPES),
        "created_at": now,
        "expires_at": now + 90 * 86400,
        "last_used_at": None,
        "revoked_at": None,
        "created_by": "admin-user",
    }
    data.update(overrides)
    return data


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_service_accounts = MagicMock(return_value=([], 0))
    db.get_service_account = MagicMock(return_value=None)
    db.get_service_account_by_name = MagicMock(return_value=None)
    db.create_service_account = MagicMock(side_effect=lambda data: data)
    db.update_service_account = MagicMock(return_value=_account_dict(revoked_at=int(time.time())))
    return db


def build_client(mock_db, tenant_id=None, scopes=None) -> TestClient:
    """A principal with `scopes=None` is the OS security key: authenticated, no scope context.
    Passing a list models a JWT or PAT caller, which is what separates an operator from a
    merely untenanted credential."""
    app = FastAPI()

    @app.middleware("http")
    async def set_principal(request, call_next):
        request.state.authenticated = True
        request.state.user_id = "admin-user"
        request.state.tenant_id = tenant_id
        if scopes is not None:
            request.state.scopes = list(scopes)
        return await call_next(request)

    app.include_router(get_service_accounts_router(os_db=mock_db, settings=EgptServingSettings()))
    return TestClient(app)


# =============================================================================
# The model
# =============================================================================


class TestTenantRoundTrips:
    def test_to_dict_carries_the_tenant(self):
        account = ServiceAccount(id="sa-1", name="n", token_hash="h", token_prefix="p", tenant_id=OWNER)
        assert account.to_dict()["tenant_id"] == OWNER

    def test_from_dict_reads_the_tenant(self):
        """The filter in from_dict drops unknown keys, so a missing entry here would silently
        lose the tenant on every verification -- the account would read back as unscoped."""
        assert ServiceAccount.from_dict(_account_dict()).tenant_id == OWNER

    def test_a_row_written_before_this_change_is_unscoped(self):
        row = _account_dict()
        del row["tenant_id"]
        assert ServiceAccount.from_dict(row).tenant_id is None

    def test_the_tenant_is_immutable(self):
        """Re-tenanting a live token would rebind an already-issued credential, the same
        objection that keeps scopes and user_id out of the mutable set."""
        assert "tenant_id" not in SERVICE_ACCOUNT_MUTABLE_COLUMNS


# =============================================================================
# Minting
# =============================================================================


class TestMintingCannotCrossTenants:
    def test_the_callers_tenant_is_inherited_by_default(self, mock_db):
        response = build_client(mock_db, tenant_id=OWNER).post("/service-accounts", json={"name": "ci-bot"})
        assert response.status_code == 201
        assert mock_db.create_service_account.call_args.args[0]["tenant_id"] == OWNER
        assert response.json()["tenant_id"] == OWNER

    def test_a_tenanted_caller_cannot_mint_for_another_tenant(self, mock_db):
        response = build_client(mock_db, tenant_id=OWNER).post(
            "/service-accounts", json={"name": "ci-bot", "tenant_id": INTRUDER}
        )
        assert response.status_code == 403
        mock_db.create_service_account.assert_not_called()

    def test_a_tenanted_caller_may_name_its_own_tenant(self, mock_db):
        response = build_client(mock_db, tenant_id=OWNER).post(
            "/service-accounts", json={"name": "ci-bot", "tenant_id": OWNER}
        )
        assert response.status_code == 201

    def test_the_security_key_root_may_provision_for_any_tenant(self, mock_db):
        """Without this there is no way to create the first tenanted machine identity."""
        response = build_client(mock_db, tenant_id=None).post(
            "/service-accounts", json={"name": "ci-bot", "tenant_id": OWNER}
        )
        assert response.status_code == 201
        assert mock_db.create_service_account.call_args.args[0]["tenant_id"] == OWNER

    def test_an_unscoped_operator_mints_unscoped_by_default(self, mock_db):
        response = build_client(mock_db, tenant_id=None).post("/service-accounts", json={"name": "ci-bot"})
        assert response.status_code == 201
        assert mock_db.create_service_account.call_args.args[0]["tenant_id"] is None


class TestAnAbsentTenantIsNotAuthority:
    """Having no tenant is not evidence of privilege (PR #55 review).

    The guard used to be skipped entirely when the caller had no tenant, so an untenanted PAT
    holding `service_accounts:write` could mint itself a token stamped for any tenant and read
    that tenant's components -- the less identified caller getting the wider reach, and a
    two-request bypass of the isolation this ticket adds.
    """

    def test_an_untenanted_scoped_caller_cannot_name_a_tenant(self, mock_db):
        response = build_client(mock_db, tenant_id=None, scopes=DEFAULT_SERVICE_ACCOUNT_SCOPES).post(
            "/service-accounts", json={"name": "pivot", "tenant_id": OWNER}
        )
        assert response.status_code == 403
        mock_db.create_service_account.assert_not_called()

    def test_it_applies_to_the_service_accounts_scope_that_makes_it_reachable(self, mock_db):
        caller = [*DEFAULT_SERVICE_ACCOUNT_SCOPES, "service_accounts:write"]
        response = build_client(mock_db, tenant_id=None, scopes=caller).post(
            "/service-accounts", json={"name": "pivot", "tenant_id": OWNER}
        )
        assert response.status_code == 403

    def test_an_untenanted_scoped_caller_may_still_mint_unscoped(self, mock_db):
        """Only naming a tenant is restricted; ordinary minting is untouched."""
        response = build_client(mock_db, tenant_id=None, scopes=DEFAULT_SERVICE_ACCOUNT_SCOPES).post(
            "/service-accounts", json={"name": "ci-bot"}
        )
        assert response.status_code == 201
        assert mock_db.create_service_account.call_args.args[0]["tenant_id"] is None

    def test_an_admin_may_provision_for_any_tenant(self, mock_db):
        """The deliberate operator keeps the capability the accidental one loses."""
        response = build_client(mock_db, tenant_id=None, scopes=[AgentOSScope.ADMIN.value]).post(
            "/service-accounts", json={"name": "ci-bot", "tenant_id": OWNER}
        )
        assert response.status_code == 201
        assert mock_db.create_service_account.call_args.args[0]["tenant_id"] == OWNER


# =============================================================================
# Reading and revoking
# =============================================================================


class TestListAndRevokeAreScoped:
    def test_listing_passes_the_callers_tenant(self, mock_db):
        build_client(mock_db, tenant_id=OWNER).get("/service-accounts")
        assert mock_db.get_service_accounts.call_args.kwargs["tenant_id"] == OWNER

    def test_an_unscoped_operator_sees_every_account(self, mock_db):
        build_client(mock_db, tenant_id=None).get("/service-accounts")
        assert mock_db.get_service_accounts.call_args.kwargs["tenant_id"] is None

    def test_revoking_passes_the_callers_tenant(self, mock_db):
        mock_db.get_service_account = MagicMock(return_value=_account_dict())
        build_client(mock_db, tenant_id=OWNER).delete("/service-accounts/sa-1")
        assert mock_db.get_service_account.call_args.kwargs["tenant_id"] == OWNER

    def test_another_tenants_account_is_not_revocable(self, mock_db):
        """The scoped read returns None for a foreign account, which lands on the existing
        404 branch -- so the account is neither revealed nor revoked."""
        mock_db.get_service_account = MagicMock(return_value=None)
        response = build_client(mock_db, tenant_id=INTRUDER).delete("/service-accounts/sa-1")
        assert response.status_code == 404
        mock_db.update_service_account.assert_not_called()


# =============================================================================
# Authentication
# =============================================================================


class TestTenantReachesRequestState:
    """`request.state.tenant_id` was hardcoded to None for every service account. It now
    carries the account's own tenant, which is what makes a PAT a scoped credential."""

    @pytest.mark.parametrize("tenant", [OWNER, None])
    async def test_the_accounts_tenant_is_attached(self, tenant):
        account = ServiceAccount(
            id="sa-1", name="ci-bot", token_hash="h", token_prefix="p", scopes=[], tenant_id=tenant
        )
        verifier = MagicMock()
        verifier.verify = AsyncMock(
            return_value=ServiceAccountVerification(status=VerificationStatus.OK, account=account)
        )

        request = SimpleNamespace(
            client=None,
            state=SimpleNamespace(),
            method="GET",
            url=SimpleNamespace(path="/health"),  # unmapped, so the scope check is a no-op
        )

        error = await authenticate_service_account_request(
            request,
            "agno_pat_whatever",
            verifier=verifier,
            scope_mappings={},
            admin_scope=None,
        )

        assert error is None
        assert request.state.tenant_id == tenant
        # The rest of the identity still lands, so this is the real attach path.
        assert request.state.user_id == "sa:ci-bot"
