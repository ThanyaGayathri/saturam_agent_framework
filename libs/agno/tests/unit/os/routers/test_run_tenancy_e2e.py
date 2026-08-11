"""The tenant reaches the model-provider seam over a real HTTP request (EGPT-3.6).

The unit tests for `build_run_context_for_request` prove the resolution rule; these prove the
*wiring* -- that a route actually builds a RunContext and that `arun` carries it far enough for the
seam to read it. Without this the plumbing could be correct and still deliver nothing to production
traffic, which is the failure mode that made EGPT-3.2's per-tenant resolution inert in the first
place.

Driven through `TestClient` against a real `EgptOS`, with a recording provider standing in for the
resolution stack: the assertion is about what the seam *sees*, not about what any model does.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from egpt.agent.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.os.app import EgptOS
from egpt.providers.model_provider import (
    BaseModelProvider,
    ModelResolveResult,
    ProviderMode,
)


class _RecordingProvider(BaseModelProvider):
    """Records the tenant on every request it is asked to resolve."""

    name = "recorder"

    def __init__(self) -> None:
        self.tenants: list = []

    def resolve_detailed(self, request):
        self.tenants.append(request.tenant_id)
        pytest.importorskip("openai", reason="the stand-in model is an OpenAI-schema client")
        from egpt.integrations.models.vllm.vllm import VLLM

        return ModelResolveResult(
            model=VLLM(id="qwen", base_url="http://vllm.internal:8000/v1", api_key="k"),
            mode=ProviderMode.LOCAL,
            provider_key="vllm",
            reason="test",
        )


def _build_os(provider):
    agent = Agent(id="a1", name="A", model=None)
    agent.model_provider = provider
    os_ = EgptOS(db=SqliteDb(db_file=tempfile.mktemp(suffix=".db")), agents=[agent])
    return os_.get_app()


def _with_verified_identity(app, *, user_id, claims):
    """Stamp what JWT middleware stamps, without standing up an IdP.

    `resolve_tenant_id` reads `request.state.claims` and `request.state.user_id`; a middleware that
    sets exactly those is a faithful stand-in for a verified caller and keeps the test free of key
    material.
    """

    @app.middleware("http")
    async def _verified(request, call_next):
        request.state.user_id = user_id
        request.state.claims = claims
        return await call_next(request)

    return app


class TestEveryDispatchPathCarriesTheContext:
    """Coverage note, stated rather than implied.

    The HTTP tests below exercise the plain non-streaming path only. Removing the `run_context`
    pass from *that* site fails them; removing it from the background or streaming site does not,
    because a `TestClient` request does not reach those. Rather than stand up SSE and background
    task infrastructure to assert one keyword, this checks structurally that every dispatch site
    which forwards identity also forwards the context -- which is the property that would actually
    regress if someone added a fifth path.
    """

    def test_no_identity_forwarding_call_site_omits_the_run_context(self):
        import re
        from pathlib import Path

        import egpt.os.routers.agents.router as agents_router
        import egpt.os.routers.teams.router as teams_router

        for module in (agents_router, teams_router):
            source = Path(module.__file__).read_text()
            # A dispatch site is an arun/run call that forwards `session_id=` and `user_id=`.
            for match in re.finditer(r"\.a?run\(\s*#?[^\n]*\n((?:\s+\w+=[^\n]*\n){2,20})", source):
                block = match.group(1)
                if "session_id=session_id" in block and "user_id=user_id" in block:
                    assert "run_context=" in block, (
                        f"a dispatch site in {module.__name__} forwards identity but not the "
                        f"RunContext, so its tenant would be dropped:\n{block}"
                    )


class TestTheVerifiedTenantReachesTheSeam:
    def test_a_verified_claim_is_delivered_over_http(self):
        pytest.importorskip("openai", reason="the stand-in model is an OpenAI-schema client")
        provider = _RecordingProvider()
        app = _with_verified_identity(_build_os(provider), user_id="u-verified", claims={"tenant_id": "acme"})

        response = TestClient(app).post(
            "/agents/a1/runs", data={"message": "hi", "stream": "false", "user_id": "u-verified"}
        )

        assert response.status_code == 200
        assert "acme" in provider.tenants, (
            "the verified tenant must reach the provider; without this the per-tenant policy "
            "lookup and the budget both see None on every HTTP request"
        )

    def test_an_unverified_form_user_id_delivers_no_tenant(self):
        """The spoof path, end to end. An unauthenticated caller putting a tenant-shaped value in
        the `user_id` form field must not have it become a tenant -- naming a tenant selects another
        tenant's backend, adapter and config blob."""
        pytest.importorskip("openai", reason="the stand-in model is an OpenAI-schema client")
        provider = _RecordingProvider()
        app = _build_os(provider)  # no verified identity stamped

        response = TestClient(app).post(
            "/agents/a1/runs",
            data={"message": "hi", "stream": "false", "user_id": "victim-tenant"},
        )

        assert response.status_code == 200
        assert set(provider.tenants) <= {None}, (
            f"an unverified form value must never become a tenant, saw {provider.tenants}"
        )
