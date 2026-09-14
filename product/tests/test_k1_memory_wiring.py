"""K1: memory scope and wiring (G1, G2). No database, no model -- these guard the seams."""

from __future__ import annotations

import asyncio

import pytest
from egpt.memory.startup import MemoryNotWired, assert_memory_wired
from egpt.os.middleware.fixed_tenant import FixedTenantMiddleware
from egpt.providers.model_provider import ProviderMode
from fastapi import Request

from egpt_retail.app import factory as factory_module
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


# ---------------------------------------------------------------------------
# _ensure_control_plane_schemas -- round-2 review on #229/B1: KpiRegistryStore's
# table must be provisioned at boot alongside Playground's and Dashboard's, or a
# fresh deployment's first GET /kpis hits a raw "relation does not exist" 500
# before the offline refresh job has ever run. #235/F2 added ProposalStore's own
# table to the same boot-time provisioning for the same reason: the review
# screen's first GET /semantic/proposals must not be the first thing to find out
# `semantic.proposal` does not exist yet. #254 added SuggestionRunStore's table
# for the same reason again: the first POST /kpis/suggestions must not be the
# first thing to find out `semantic.kpi_suggestion_run` does not exist yet.
# ---------------------------------------------------------------------------


def test_ensure_control_plane_schemas_provisions_all_six_stores(monkeypatch):
    calls: list = []

    class _FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr("psycopg.connect", lambda dsn: _FakeConnection())

    def _fake_store(name):
        class _FakeStore:
            def __init__(self, connection):
                assert isinstance(connection, _FakeConnection)

            def ensure_schema(self):
                calls.append(name)

        return _FakeStore

    monkeypatch.setattr(factory_module, "PlaygroundStore", _fake_store("playground"))
    monkeypatch.setattr(factory_module, "DashboardStore", _fake_store("dashboard"))
    monkeypatch.setattr(factory_module, "KpiRegistryStore", _fake_store("kpi"))
    monkeypatch.setattr(factory_module, "ProposalStore", _fake_store("proposal"))
    monkeypatch.setattr(
        factory_module, "SuggestionRunStore", _fake_store("suggestion_run")
    )
    monkeypatch.setattr(
        factory_module,
        "DashboardSuggestionRunStore",
        _fake_store("dashboard_suggestion_run"),
    )

    factory_module._ensure_control_plane_schemas(
        RetailSettings(retail_db_url="postgresql+psycopg://u:p@127.0.0.1:1/db")
    )

    assert calls == [
        "playground",
        "dashboard",
        "kpi",
        "proposal",
        "suggestion_run",
        "dashboard_suggestion_run",
    ]


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


def test_lifespan_closes_the_provider_when_the_wiring_check_refuses(monkeypatch):
    """The provider is built before the check runs, so a refusal must not leak it.

    ``assert_memory_wired`` used to sit *outside* the ``try``, which meant a ``MemoryNotWired``
    raised from it skipped ``finally``'s ``aclose()`` and leaked the pgvector engine on every
    refused boot. The one pre-existing lifespan test passes ``retail_customer_slug=None``, for
    which no provider is built at all -- ``provider is None`` and the ``finally`` branch is
    never entered either way, so it cannot see this. This one builds a real (fake) provider and
    fails the check for an *unrelated* reason: an agent with neither id nor name.
    """

    class _FakeProvider:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    class _AnonymousAgent:
        id = None
        name = None
        memory_provider = None

    provider = _FakeProvider()
    monkeypatch.setattr(
        "egpt_retail.app.factory.abuild_memory_provider",
        _make_async(provider),
    )

    lifespan = memory_lifespan_for(
        RetailSettings(retail_customer_slug="middleby"), [_AnonymousAgent()]
    )

    async def _boot():
        async with lifespan(app=None, agent_os=None):
            pass

    with pytest.raises(MemoryNotWired) as excinfo:
        asyncio.run(_boot())
    assert "neither id nor name" in str(excinfo.value), (
        "the unrelated fault, not the slug"
    )
    assert provider.closed, "the provider built before the refusal must still be closed"


def _make_async(result):
    async def _builder(_settings):
        return result

    return _builder


def test_assert_memory_wired_reports_every_fault_at_once():
    class _A:
        def __init__(self, id=None, memory_provider=None):
            self.id, self.name, self.memory_provider = id, None, memory_provider

    with pytest.raises(MemoryNotWired) as excinfo:
        assert_memory_wired([_A(memory_provider=object()), _A(id="b")], tenant_id="")
    faults = [
        ln for ln in str(excinfo.value).splitlines() if ln.strip().startswith("- ")
    ]
    # Count alone lets a pair of offsetting regressions (one real fault silently stops being
    # detected while an unrelated false one starts) keep the total at 3 and pass undetected --
    # assert on which three, not just how many.
    #
    # Per line, not against the joined block: substring checks over `"\n".join(faults)` pass
    # when the fault text and the agent it names come from *different* lines, so swapping which
    # message carries `missing` and which carries `unstable` -- an operator being told the wrong
    # agent has the wrong problem -- went undetected (review round 2 of PR #204). The agent
    # label is asserted immediately after its own colon for the same reason.
    assert len(faults) == 3
    tenant_line, provider_line, identity_line = faults
    assert "no customer identifier is configured" in tenant_line
    assert "1 agent(s) have no memory provider: b." in provider_line
    assert (
        "1 agent(s) write memory but have neither id nor name: <agent #0>."
        in identity_line
    )


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


def test_middleware_reads_the_claim_name_live_on_every_request(monkeypatch):
    """The middleware is constructed once per process (``build_agent_os``), so a claim name
    resolved in ``__init__`` is frozen for the process lifetime while ``resolve_tenant_id``
    re-reads the setting on the very same request -- the two-copies drift this middleware's
    own docstring cites (PR #53, PR #121). Set the setting *after* construction, as a
    configuration change or a later test would, and dispatch must pick it up."""
    monkeypatch.delenv("EGPT_TENANT_CLAIM", raising=False)
    middleware = FixedTenantMiddleware(app=None, tenant_id="middleby")

    async def _next(_):
        return None

    monkeypatch.setenv("EGPT_TENANT_CLAIM", "tid")
    request = _Request()
    asyncio.run(middleware.dispatch(request, _next))
    assert request.state.claims == {"tid": "middleby"}, (
        "a claim resolved at construction would still say tenant_id here"
    )

    # And back again: nothing is memoised on the first read either.
    monkeypatch.setenv("EGPT_TENANT_CLAIM", "org_id")
    second = _Request()
    asyncio.run(middleware.dispatch(second, _next))
    assert second.state.claims == {"org_id": "middleby"}


def test_an_explicit_claim_name_is_not_overridden_by_the_setting(monkeypatch):
    """The live read is the *default*. A caller that names the claim explicitly has pinned it,
    and an environment change must not move it underneath them."""
    middleware = FixedTenantMiddleware(
        app=None, tenant_id="middleby", tenant_id_claim="pinned"
    )

    async def _next(_):
        return None

    monkeypatch.setenv("EGPT_TENANT_CLAIM", "tid")
    request = _Request()
    asyncio.run(middleware.dispatch(request, _next))
    assert request.state.claims == {"pinned": "middleby"}


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


def test_factory_places_tenant_middleware_inside_egptos_stack_with_real_auth_on(
    monkeypatch, tmp_path
):
    """The same ordering hazard, proven with JWT auth actually enabled.

    The test above proves the ordering against the real assembly with auth off; the
    synthetic ``_ReplacingAuth`` test above proves the direction of the hazard. Neither runs
    both together: real `EgptOS`/`build_agent_os`, with a real `AuthMiddleware` verifying a
    real token, which is exactly the path whose `request.state.claims` replacement
    (`os/middleware/jwt.py:1111`) this PR's tenant-middleware placement exists to survive.
    """
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    from egpt_retail.app.devauth import mint_token, write_dev_jwks

    private_path, jwks_path = write_dev_jwks(tmp_path / "auth")
    private_pem = private_path.read_text()
    audience = "api://egpt-retail"
    token = mint_token(private_pem, scopes=["config:read"], audience=audience)

    monkeypatch.setenv("EGPT_RETAIL_CUSTOMER_SLUG", "middleby")
    monkeypatch.setenv("EGPT_RETAIL_AUTH_JWKS_FILE", str(jwks_path))
    monkeypatch.setenv("EGPT_RETAIL_AUTH_AUDIENCE", audience)
    settings = RetailSettings()
    assert settings.authorization_enabled, (
        "the JWKS file above must actually turn auth on"
    )
    app = build_agent_os(settings).get_app()

    @app.get("/__k1_probe_authed")
    async def probe(request: Request):
        return JSONResponse(
            {
                "tenant": getattr(request.state, "tenant_id", None),
                "claim": (getattr(request.state, "claims", None) or {}).get(
                    "tenant_id"
                ),
            }
        )

    response = TestClient(app).get(
        "/__k1_probe_authed", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"tenant": "middleby", "claim": "middleby"}


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
