"""K1: memory scope and wiring (G1, G2). No database, no model -- these guard the seams."""

from __future__ import annotations

import asyncio

import pytest
from egpt.memory.startup import MemoryNotWired, assert_memory_wired
from egpt.os.middleware.fixed_tenant import FixedTenantMiddleware
from egpt.providers.model_provider import ProviderMode
from fastapi import Request

from egpt_retail.app.factory import build_agent_os, memory_lifespan_for
from egpt_retail.app.settings import RetailSettings
from egpt_retail.memory.config import build_memory_config


@pytest.mark.parametrize("mode", [ProviderMode.LOCAL, ProviderMode.FRONTIER])
def test_provider_mode_setting_reaches_memory_config(mode):
    """One setting, both subsystems. It was hardcoded to FRONTIER while knowledge read the
    setting, which would have given one deployment two vector spaces."""
    assert build_memory_config(RetailSettings(retail_provider_mode=mode)).mode is mode


def test_build_agent_os_is_pure_construction(monkeypatch):
    """Importing app.main calls the factory. It must not touch a database or need a slug."""
    monkeypatch.delenv("EGPT_RETAIL_CUSTOMER_SLUG", raising=False)
    monkeypatch.setenv(
        "EGPT_RETAIL_DB_URL", "postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )
    assert build_agent_os(RetailSettings()) is not None


def test_lifespan_refuses_to_start_without_a_customer_slug():
    """G1: an unconfigured deployment refuses at startup rather than serving memoryless."""

    class _Agent:
        id = "synonym_proposer"
        memory_provider = None

    lifespan = memory_lifespan_for(
        RetailSettings(retail_customer_slug=None), [_Agent()]
    )

    async def _boot():
        async with lifespan(app=None, agent_os=None):
            pass

    with pytest.raises(MemoryNotWired) as excinfo:
        asyncio.run(_boot())
    assert "no customer identifier is configured" in str(excinfo.value)


def test_assert_memory_wired_reports_every_fault_at_once():
    class _A:
        def __init__(self, id=None, memory_provider=None):
            self.id, self.name, self.memory_provider = id, None, memory_provider

    with pytest.raises(MemoryNotWired) as excinfo:
        assert_memory_wired([_A(memory_provider=object()), _A(id="b")], tenant_id="")
    faults = [
        ln for ln in str(excinfo.value).splitlines() if ln.strip().startswith("- ")
    ]
    assert len(faults) == 3


class _State:
    pass


class _Request:
    def __init__(self, **state):
        self.state = _State()
        for key, value in state.items():
            setattr(self.state, key, value)


def _dispatch(request):
    middleware = FixedTenantMiddleware(app=None, tenant_id="middleby")

    async def _next(_):
        return None

    asyncio.run(middleware.dispatch(request, _next))
    return request


def test_middleware_stamps_the_claim_the_run_path_reads():
    """build_run_context_for_request resolves through state.claims, not state.tenant_id.
    Stamping only the attribute would fix nothing on the path that matters."""
    request = _dispatch(_Request())
    assert request.state.tenant_id == "middleby"
    assert request.state.claims["tenant_id"] == "middleby"


def test_middleware_never_overrides_a_verified_claim():
    """A configured default that shadowed a real claim would be a cross-tenant read."""
    request = _dispatch(_Request(tenant_id="acme", claims={"tenant_id": "acme"}))
    assert request.state.tenant_id == "acme"
    assert request.state.claims["tenant_id"] == "acme"


def test_middleware_refuses_an_empty_tenant():
    with pytest.raises(ValueError):
        FixedTenantMiddleware(app=None, tenant_id="  ")


def test_fixed_tenant_survives_an_auth_that_replaces_claims():
    """The order that matters. AuthMiddleware assigns request.state.claims = payload -- a
    replace, not a merge -- and Starlette runs the last-added middleware first. So the tenant
    middleware must be INNER to auth: auth runs, replaces the dict, then the tenant fills the
    claim auth left absent. Outer, it would inject and then be wiped on every authenticated
    request. Modelled here with a replacing fake auth so it needs no JWT and no database."""
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    class _ReplacingAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.claims = {"sub": "alice"}  # exactly what jwt.py:1111 does
            return await call_next(request)

    async def probe(request):
        return JSONResponse(
            {
                "tenant": getattr(request.state, "tenant_id", None),
                "claims": request.state.claims,
            }
        )

    app = Starlette(routes=[Route("/probe", probe)])
    app.add_middleware(
        FixedTenantMiddleware, tenant_id="middleby"
    )  # added first -> inner
    app.add_middleware(
        _ReplacingAuth
    )  # added last -> outer, runs first, replaces claims
    body = TestClient(app).get("/probe").json()
    assert body["tenant"] == "middleby"
    assert body["claims"] == {"sub": "alice", "tenant_id": "middleby"}

    # And the wrong order, to prove the test can fail: tenant middleware outer.
    wrong = Starlette(routes=[Route("/probe", probe)])
    wrong.add_middleware(_ReplacingAuth)
    wrong.add_middleware(FixedTenantMiddleware, tenant_id="middleby")
    body = TestClient(wrong).get("/probe").json()
    assert "tenant_id" not in body["claims"], "outer placement should lose the claim"


def test_factory_places_tenant_middleware_inside_egptos_stack(monkeypatch):
    """Through the real EgptOS app: the tenant reaches request.state on a plain request. No
    lifespan is entered (TestClient without a context manager), so no database is touched."""
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    monkeypatch.setenv("EGPT_RETAIL_CUSTOMER_SLUG", "middleby")
    monkeypatch.delenv("EGPT_RETAIL_AUTH_JWKS_FILE", raising=False)
    app = build_agent_os(RetailSettings()).get_app()

    @app.get("/__k1_probe")
    async def probe(request: Request):
        return JSONResponse(
            {
                "tenant": getattr(request.state, "tenant_id", None),
                "claim": (getattr(request.state, "claims", None) or {}).get(
                    "tenant_id"
                ),
            }
        )

    body = TestClient(app).get("/__k1_probe").json()
    assert body == {"tenant": "middleby", "claim": "middleby"}


def test_no_slug_means_no_tenant_middleware(monkeypatch):
    """str(None) is "None". A tenant literally named None must never be stamped."""
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    monkeypatch.delenv("EGPT_RETAIL_CUSTOMER_SLUG", raising=False)
    monkeypatch.delenv("EGPT_RETAIL_AUTH_JWKS_FILE", raising=False)
    app = build_agent_os(RetailSettings()).get_app()

    @app.get("/__k1_probe")
    async def probe(request: Request):
        return JSONResponse({"tenant": getattr(request.state, "tenant_id", None)})

    assert TestClient(app).get("/__k1_probe").json() == {"tenant": None}
