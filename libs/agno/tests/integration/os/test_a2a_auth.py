"""S1: the A2A interface does not authenticate its own requests, so it must sit
behind the central AuthMiddleware. Enabling ``authorization`` therefore protects the
A2A execution/discovery endpoints too, instead of leaving them anonymous.

Self-authenticating interfaces (Slack/Telegram/WhatsApp verify a signing secret in
their own routers) keep their ``authenticates_own_requests = True`` marker and stay
excluded from the central layer.
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from egpt.agent.agent import Agent
from egpt.db.in_memory import InMemoryDb
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.interfaces.a2a import A2A

JWT_SECRET = "test-secret-for-a2a-auth"
TEST_OS_ID = "a2a-auth-os"
AGENT_ID = "a2a-agent"

CARD_PATH = f"/a2a/agents/{AGENT_ID}/.well-known/agent-card.json"


def _token(scopes=None):
    payload = {
        "sub": "user-1",
        "scopes": scopes if scopes is not None else ["agents:read", "agents:run"],
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture
def a2a_client():
    agent = Agent(id=AGENT_ID, name="A2A Agent", db=InMemoryDb())
    agent_os = EgptOS(
        id=TEST_OS_ID,
        agents=[agent],
        a2a_interface=True,
        authorization=True,
        authorization_config=AuthorizationConfig(verification_keys=[JWT_SECRET], algorithm="HS256"),
    )
    return TestClient(agent_os.get_app())


class TestA2ARequiresAuth:
    def test_a2a_card_rejects_anonymous_request(self, a2a_client):
        # Previously reachable with no credential; now behind the auth layer.
        resp = a2a_client.get(CARD_PATH)
        assert resp.status_code == 401, resp.text

    def test_a2a_card_allows_authenticated_request(self, a2a_client):
        resp = a2a_client.get(CARD_PATH, headers={"Authorization": f"Bearer {_token()}"})
        assert resp.status_code == 200, resp.text

    def test_a2a_message_send_rejects_anonymous_request(self, a2a_client):
        # 401 fires in the middleware, before the route parses the body.
        resp = a2a_client.post(f"/a2a/agents/{AGENT_ID}/v1/message:send", json={})
        assert resp.status_code == 401, resp.text


class TestInterfaceSelfAuthMarkers:
    """The marker that decides whether an interface is excluded from the central
    auth layer. A2A must NOT self-authenticate (Slack/Telegram/WhatsApp set the
    marker True in their own modules; asserting those here would require their
    optional SDKs)."""

    def test_a2a_does_not_self_authenticate(self):
        assert A2A.authenticates_own_requests is False

    def test_base_interface_defaults_to_not_self_authenticating(self):
        from egpt.os.interfaces.base import BaseInterface

        assert BaseInterface.authenticates_own_requests is False
