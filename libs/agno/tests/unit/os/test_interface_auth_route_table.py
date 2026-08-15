"""Auth route-table regression guard for EGPT-1.7b (aicoe-tracker#158).

The issue's own review comments call out that green tests alone are not sufficient evidence
for this ticket: `authenticates_own_requests` still excluding the right prefixes from
`AuthMiddleware`, and `get_scope_mappings()` still gating every executing route, need an
explicit before/after comparison against `main`, since a silent regression here either breaks
the channel or opens an auth hole. This file is the standing half of that evidence -- it turns
what would otherwise be a one-time manual diff into a permanent CI-enforced assertion, so a
future regression is caught automatically rather than only during this ticket's review.

WhatsApp coverage (`test_self_authenticating_prefixes_are_excluded_from_auth`,
`test_self_authenticating_interfaces_carry_no_scope_mappings`, `test_route_table_snapshot`) runs
unconditionally, including inside the `unit-drivers-absent` CI job -- a **required** check that
uninstalls `slack_sdk`, whose charter is catching exactly this risk class (import-time
regressions from a cross-package module move). An earlier version of this file put
`pytest.importorskip("slack_sdk")` at module scope, which silently skipped all three WhatsApp
tests too whenever `slack_sdk` was absent -- caught in PR #82 review. Slack coverage
(`test_slack_prefix_also_excluded_when_configured`) needs `slack_sdk` to build the `Slack`
interface at all, so it is gated locally on just that one test instead.

Neither the WhatsApp nor Slack move touches `EgptOS._add_auth_middleware` (`egpt/os/app.py`),
`Whatsapp`/`Slack.authenticates_own_requests`, or `BaseInterface.get_scope_mappings`'s default
-- this test exists to prove that claim rather than assume it, by actually building an `EgptOS`
with the interface(s) mounted and inspecting the real `AuthMiddleware` instance FastAPI ends up
with, exactly the way `tests/unit/os/test_mcp_server.py::_parent_auth_middleware` does for the
JWT-constraint guards.

Before/after: run this file's `test_route_table_snapshot` (or a manual `os.get_routes()` +
`_auth_middleware(app).kwargs` capture) against a `main` worktree and against the branch under
review; the two should be identical. See the PR description for that diff's output.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from egpt.agent import Agent
from egpt.os.app import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.interfaces.whatsapp import Whatsapp

SIGNING_SECRET = "test-signing-secret"


def _agent() -> Agent:
    agent = Agent(name="Test Agent", id="test-agent", telemetry=False)
    agent.arun = AsyncMock(
        return_value=Mock(
            status="OK", content="ok", reasoning_content=None, images=None, files=None, videos=None, audio=None
        )
    )
    return agent


def _build_whatsapp_only_app():
    """A WhatsApp-only EgptOS with JWT auth on. No `slack_sdk` dependency -- see the module
    docstring for why that matters."""
    agent = _agent()
    whatsapp = Whatsapp(agent=agent, access_token="wa-token", phone_number_id="123456", verify_token="wa-verify")
    agent_os = EgptOS(
        agents=[agent],
        interfaces=[whatsapp],
        authorization=True,
        authorization_config=AuthorizationConfig(verification_keys=["dummy"]),
        telemetry=False,
    )
    return agent_os, agent_os.get_app()


def _build_whatsapp_and_slack_app():
    """Both self-authenticating channel adapters mounted. Importing `Slack` transitively
    requires `slack_sdk` (its router import-fails without it) -- callers must
    `pytest.importorskip("slack_sdk")` before calling this."""
    from egpt.os.interfaces.slack import Slack

    agent = _agent()
    whatsapp = Whatsapp(agent=agent, access_token="wa-token", phone_number_id="123456", verify_token="wa-verify")
    slack = Slack(agent=agent, token="xoxb-test", signing_secret=SIGNING_SECRET, streaming=False)

    mock_slack_tools = Mock()
    mock_slack_tools.token = "xoxb-test"
    mock_slack_tools.max_file_size = 1_073_741_824
    mock_async_web_client = AsyncMock()

    with (
        patch("egpt.integrations.channels.slack.router.SlackTools", return_value=mock_slack_tools),
        patch("egpt.integrations.channels.slack.event_handler.AsyncWebClient", return_value=mock_async_web_client),
    ):
        agent_os = EgptOS(
            agents=[agent],
            interfaces=[whatsapp, slack],
            authorization=True,
            authorization_config=AuthorizationConfig(verification_keys=["dummy"]),
            telemetry=False,
        )
        app = agent_os.get_app()

    return agent_os, app


def _auth_middleware(app):
    """The single parent-app AuthMiddleware instance -- mirrors
    `test_mcp_server.py::_parent_auth_middleware`."""
    return next(m for m in app.user_middleware if m.cls.__name__ == "AuthMiddleware")


def _route_table(agent_os: EgptOS):
    """(path, sorted methods) for every route, sorted -- a stable snapshot for diffing."""
    routes = agent_os.get_routes()
    table = []
    for route in routes:
        methods = getattr(route, "methods", None)
        table.append((route.path, tuple(sorted(methods)) if methods else ()))
    return sorted(table)


def test_self_authenticating_prefixes_are_excluded_from_auth():
    """WhatsApp verifies its own webhook signature, so its prefix must be appended to
    AuthMiddleware's excluded_route_paths -- unchanged by the module move."""
    _, app = _build_whatsapp_only_app()
    mw = _auth_middleware(app)
    excluded = mw.kwargs.get("excluded_route_paths") or []
    assert "/whatsapp/*" in excluded, f"expected /whatsapp/* in excluded_route_paths, got {excluded}"


def test_self_authenticating_interfaces_carry_no_scope_mappings():
    """WhatsApp doesn't override get_scope_mappings() and self-authenticates, so it's excluded
    from interface_mappings entirely (egpt/os/app.py's _add_auth_middleware skips any interface
    with authenticates_own_requests=True)."""
    _, app = _build_whatsapp_only_app()
    mw = _auth_middleware(app)
    assert not mw.kwargs.get("scope_mappings"), f"expected no scope_mappings, got {mw.kwargs.get('scope_mappings')}"


def test_route_table_snapshot():
    """Expected WhatsApp routes -- the concrete before/after diff evidence.

    Run this same assertion (or capture `_route_table`'s output directly) against a `main`
    worktree and this branch; the two lists must be identical. Only the module the router code
    lives under changes in this ticket, never a path, method, or operation_id.
    """
    agent_os, _ = _build_whatsapp_only_app()
    table = _route_table(agent_os)
    paths = {path for path, _methods in table}

    expected_whatsapp = {"/whatsapp/status", "/whatsapp/webhook"}
    missing = expected_whatsapp - paths
    assert not missing, f"expected routes missing from route table: {missing}\nfull table: {table}"

    # /whatsapp/webhook registers a GET (verification) and a POST (message intake) as two
    # separate FastAPI routes on the same path, not one route with both methods.
    whatsapp_webhook_methods = {m for path, methods in table if path == "/whatsapp/webhook" for m in methods}
    assert whatsapp_webhook_methods == {"GET", "POST"}, whatsapp_webhook_methods


def test_slack_prefix_also_excluded_when_configured():
    """Same guard, with Slack also mounted -- gated locally (not at module scope) so only this
    one test is skipped when `slack_sdk` is absent. See the module docstring."""
    pytest.importorskip("slack_sdk", reason="this test mounts the Slack interface")

    agent_os, app = _build_whatsapp_and_slack_app()
    mw = _auth_middleware(app)
    excluded = mw.kwargs.get("excluded_route_paths") or []
    assert "/whatsapp/*" in excluded and "/slack/*" in excluded, excluded
    assert not mw.kwargs.get("scope_mappings"), mw.kwargs.get("scope_mappings")

    table = _route_table(agent_os)
    paths = {path for path, _methods in table}
    expected = {"/whatsapp/status", "/whatsapp/webhook", "/slack/events", "/slack/interactions"}
    missing = expected - paths
    assert not missing, f"expected routes missing from route table: {missing}\nfull table: {table}"
