"""#228/G1's backend proof: a real RS256 token, through the real `AuthMiddleware`, at a real
product route -- not a synthetic probe.

`test_r2_auth.py` exercises `devauth`/settings/`build_agent_os` construction in isolation.
`test_k1_memory_wiring.py`'s `..._with_real_auth_on` test proves middleware *ordering* (tenant
vs. auth) against a route it adds itself. Neither sends a token at an existing router and checks
that authorization actually gates it -- which is the specific claim #228 needs settled before
the Entra/IdP decision: does this deployment's JWT wiring, as built, actually enforce 401 (no
token) / 403 (wrong scope) / 200 (right scope) end to end, and does `_owner`'s per-caller
ownership isolation hold through the real middleware, not just in the DB-free unit tests
`test_r13_dashboards.py` already has for `_owner`'s pure logic.

`dashboards_router` is the target, not `kpis.py`: it's on `main` today, `kpis.py` (#229-#234)
is not. Only Postgres is needed -- `dashboards.py`'s routes open their own control-plane
connection per request and never touch Neo4j for an empty-tiles dashboard.

Locally, against the compose stack's Postgres:

    EGPT_INTEGRATION_POSTGRES=1 pytest product/tests/test_r2_auth_live.py

Everything written here is named ``livetest_auth__...`` and removed at the end of each test.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest
from starlette.testclient import TestClient

from egpt_retail.app.db import psycopg_dsn
from egpt_retail.app.devauth import mint_token, write_dev_jwks
from egpt_retail.app.factory import build_agent_os
from egpt_retail.app.settings import RetailSettings
from egpt_retail.dashboards.store import DashboardStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("EGPT_INTEGRATION_POSTGRES"),
    reason="needs EGPT_INTEGRATION_POSTGRES=1 with a live control-plane Postgres",
)

AUDIENCE = "api://egpt-retail"
NAME_PREFIX = "livetest_auth__"


@pytest.fixture
def control_plane() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(psycopg_dsn(RetailSettings().retail_db_url))
    DashboardStore(conn).ensure_schema()
    yield conn
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM dashboards.dashboard WHERE name LIKE %s", (f"{NAME_PREFIX}%",)
        )
    conn.commit()
    conn.close()


@pytest.fixture
def auth_client(monkeypatch, tmp_path, control_plane: Any) -> Iterator[dict]:
    """A `TestClient` over the real `build_agent_os` app, real auth on, real dashboards
    router mounted -- and a `mint(...)` helper closed over the matching private key.

    `TestClient` is used without a `with` block (no lifespan entered), matching
    `test_k1_memory_wiring.py`'s precedent: the memory lifespan needs Postgres pgvector
    provisioning this test has no reason to pay for, and the routes under test open their own
    connection per request regardless of lifespan.
    """
    private_path, jwks_path = write_dev_jwks(tmp_path / "auth")
    private_pem = private_path.read_text()
    monkeypatch.setenv("EGPT_RETAIL_AUTH_JWKS_FILE", str(jwks_path))
    monkeypatch.setenv("EGPT_RETAIL_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.delenv("EGPT_RETAIL_CUSTOMER_SLUG", raising=False)
    settings = RetailSettings()
    assert settings.authorization_enabled
    app = build_agent_os(settings).get_app()

    def mint(scopes: list, subject: str = "livetest_auth__alice") -> str:
        return mint_token(private_pem, scopes, subject=subject, audience=AUDIENCE)

    yield {"client": TestClient(app), "mint": mint}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_no_token_is_refused_before_it_reaches_the_route(auth_client: dict) -> None:
    response = auth_client["client"].get("/dashboards")
    assert response.status_code == 401


def test_a_token_missing_the_required_scope_is_refused(auth_client: dict) -> None:
    token = auth_client["mint"](["config:read"])  # real token, wrong scope
    response = auth_client["client"].get("/dashboards", headers=_bearer(token))
    assert response.status_code == 403


def test_a_correctly_scoped_token_reads_through_to_the_real_store(
    auth_client: dict,
) -> None:
    token = auth_client["mint"](["dashboards:read"])
    response = auth_client["client"].get("/dashboards", headers=_bearer(token))
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_write_scope_is_required_to_create_even_with_read_granted(
    auth_client: dict,
) -> None:
    token = auth_client["mint"](["dashboards:read"])
    response = auth_client["client"].post(
        "/dashboards",
        json={"name": f"{NAME_PREFIX}should_not_be_created"},
        headers=_bearer(token),
    )
    assert response.status_code == 403


def test_create_then_read_round_trips_through_real_auth_for_the_same_subject(
    auth_client: dict,
) -> None:
    client, mint = auth_client["client"], auth_client["mint"]
    write_token = mint(["dashboards:write"], subject="livetest_auth__alice")
    created = client.post(
        "/dashboards",
        json={"name": f"{NAME_PREFIX}alices_board"},
        headers=_bearer(write_token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["owner"] == "livetest_auth__alice"

    read_token = mint(["dashboards:read"], subject="livetest_auth__alice")
    listed = client.get("/dashboards", headers=_bearer(read_token))
    assert any(d["name"] == f"{NAME_PREFIX}alices_board" for d in listed.json()), (
        listed.json()
    )


def test_a_different_subjects_token_cannot_see_another_callers_private_dashboard(
    auth_client: dict,
) -> None:
    """The real-middleware counterpart to `test_r13_dashboards.py`'s DB-free `_owner` tests:
    proves the ownership boundary holds when `user_id` comes from an actually-decoded JWT
    `sub` claim, not a hand-built fake `Request`.
    """
    client, mint = auth_client["client"], auth_client["mint"]
    write_token = mint(["dashboards:write"], subject="livetest_auth__alice")
    created = client.post(
        "/dashboards",
        json={"name": f"{NAME_PREFIX}alices_private_board"},
        headers=_bearer(write_token),
    )
    assert created.status_code == 201, created.text

    bobs_read_token = mint(["dashboards:read"], subject="livetest_auth__bob")
    listed = client.get("/dashboards", headers=_bearer(bobs_read_token))
    assert all(
        d["name"] != f"{NAME_PREFIX}alices_private_board" for d in listed.json()
    ), listed.json()

    bobs_get = client.get(
        f"/dashboards/{created.json()['id']}", headers=_bearer(bobs_read_token)
    )
    assert bobs_get.status_code == 404, "not visible, not merely unlisted"


def test_a_token_with_no_subject_claim_is_refused_by_the_real_owner_check(
    auth_client: dict,
) -> None:
    """`mint_token` always sets `sub` (its default is `"alice"`, not optional), so an empty
    string is used to stand in for "decodes fine, carries no usable subject" --
    `AuthMiddleware` sets `request.state.user_id` straight from the claim with no extra
    validation of its own, so this reaches exactly the branch `_owner` refuses on (a falsy
    `user_id`), through the real middleware rather than a hand-built fake `Request`.
    """
    client, mint = auth_client["client"], auth_client["mint"]
    token = mint(["dashboards:read"], subject="")
    response = client.get("/dashboards", headers=_bearer(token))
    assert response.status_code == 401
