"""Integration tests for running Workflows in EgptOS."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport
from pydantic import BaseModel

from egpt.db.base import ComponentType
from egpt.integrations.db.sqlite import SqliteDb
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.middleware.jwt import INVALID_TENANT_CLAIM_ERROR
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL
from egpt.workflow.factory import WorkflowFactory
from egpt.workflow.step import Step
from egpt.workflow.types import StepInput, StepOutput
from egpt.workflow.workflow import Workflow


def _factory_workflow_step(step_input: StepInput) -> StepOutput:
    return StepOutput(content=f"Factory workflow handled: {step_input.message}")


class FactoryWorkflowInput(BaseModel):
    flavor: str = "default"


def test_create_workflow_run(test_os_client, test_workflow: Workflow):
    """Test creating a workflow run using form input."""
    response = test_os_client.post(
        f"/workflows/{test_workflow.id}/runs",
        data={"message": "Hello, world!", "stream": "false"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200

    response_json = response.json()
    assert response_json["run_id"] is not None
    assert response_json["workflow_id"] == test_workflow.id
    assert response_json["content"] is not None


def test_create_workflow_run_streaming(test_os_client, test_workflow: Workflow):
    """Test creating a workflow run with streaming enabled."""
    with test_os_client.stream(
        "POST",
        f"/workflows/{test_workflow.id}/runs",
        data={
            "message": "Hello, world!",
            "stream": "true",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # Collect streaming chunks
        chunks = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                data = line[6:]  # Remove 'data: ' prefix
                if data != "[DONE]":
                    chunks.append(json.loads(data))

        # Verify we received data
        assert len(chunks) > 0

        # Check first chunk has expected fields
        first_chunk = chunks[0]
        assert first_chunk.get("run_id") is not None
        assert first_chunk.get("workflow_id") == test_workflow.id

        # Verify content across chunks
        content_chunks = [chunk.get("content") for chunk in chunks if chunk.get("content")]
        assert len(content_chunks) > 0


def test_running_unknown_workflow_returns_404(test_os_client):
    """Test running an unknown workflow returns a 404 error."""
    response = test_os_client.post(
        "/workflows/unknown-workflow/runs",
        data={"message": "Hello, world!", "stream": "false"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow not found"


def test_create_workflow_run_without_message_returns_422(test_os_client, test_workflow: Workflow):
    """Test that missing required message field returns validation error."""
    response = test_os_client.post(
        f"/workflows/{test_workflow.id}/runs",
        data={},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 422


def test_create_workflow_run_with_kwargs(test_os_client, test_workflow: Workflow):
    """Test that the create_agent_run endpoint accepts kwargs."""

    class MockRunOutput:
        def to_dict(self):
            return {}

    # Patch deep_copy to return the same instance so our mock works
    # (EgptOS uses create_fresh=True which calls deep_copy)
    with (
        patch.object(test_workflow, "deep_copy", return_value=test_workflow),
        patch.object(test_workflow, "arun", new_callable=AsyncMock) as mock_arun,
    ):
        mock_arun.return_value = MockRunOutput()

        response = test_os_client.post(
            f"/workflows/{test_workflow.id}/runs",
            data={
                "message": "Hello, world!",
                "stream": "false",
                # Passing some extra fields to the run endpoint
                "extra_field": "foo",
                "extra_field_two": "bar",
            },
        )
        assert response.status_code == 200

        # Asserting our extra fields were passed as kwargs
        call_args = mock_arun.call_args
        assert call_args.kwargs["extra_field"] == "foo"
        assert call_args.kwargs["extra_field_two"] == "bar"


@pytest.fixture
def factory_workflow_client(temp_storage_db_file):
    """Create a TestClient with a workflow factory registered in EgptOS."""
    db = SqliteDb(db_file=temp_storage_db_file)

    def build_workflow(ctx):
        return Workflow(
            name="factory-workflow",
            id="generated-workflow",
            steps=[Step(name="step1", executor=_factory_workflow_step)],
            db=db,
            metadata={"flavor": getattr(ctx.input, "flavor", None)},
        )

    workflow_factory = WorkflowFactory(
        db=db,
        id="factory-workflow",
        factory=build_workflow,
        input_schema=FactoryWorkflowInput,
    )

    app = EgptOS(workflows=[workflow_factory]).get_app()
    return TestClient(app)


def test_factory_workflow_get_run_and_list_runs(factory_workflow_client):
    """Factory workflows should support run polling and run listing after creation."""
    create_response = factory_workflow_client.post(
        "/workflows/factory-workflow/runs",
        data={
            "message": "Hello from factory workflow",
            "stream": "false",
            "factory_input": json.dumps({"flavor": "vanilla"}),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert create_response.status_code == 200

    run_data = create_response.json()
    run_id = run_data["run_id"]
    session_id = run_data["session_id"]

    get_response = factory_workflow_client.get(
        f"/workflows/factory-workflow/runs/{run_id}",
        params={"session_id": session_id, "factory_input": json.dumps({"flavor": "vanilla"})},
    )
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["run_id"] == run_id
    assert get_data["workflow_id"] == "factory-workflow"

    list_response = factory_workflow_client.get(
        "/workflows/factory-workflow/runs",
        params={"session_id": session_id, "factory_input": json.dumps({"flavor": "vanilla"})},
    )
    assert list_response.status_code == 200
    runs = list_response.json()
    assert any(run["run_id"] == run_id for run in runs)


@pytest.fixture
def websocket_factory_client(temp_storage_db_file):
    """Create a WebSocket TestClient with an authorized workflow factory.

    The fixture records what the factory sees in ``factory_calls`` so tests can
    assert on the trusted JWT context *after* the run without swallowing
    assertions inside the factory callback (which would surface as opaque
    "Factory error: ..." frames).
    """

    secret = "x" * 40
    os_id = "ws-factory-os"

    db = SqliteDb(db_file=temp_storage_db_file)
    factory_calls: list[dict] = []

    def websocket_workflow_step(_: StepInput) -> StepOutput:
        return StepOutput(content="ok")

    def build_workflow(ctx):
        factory_calls.append(
            {
                "claims": dict(ctx.trusted.claims) if ctx.trusted.claims else {},
                "scopes": set(ctx.trusted.scopes) if ctx.trusted.scopes else set(),
            }
        )
        return Workflow(
            id="ws-factory",
            name="ws-factory",
            steps=[Step(name="s", executor=websocket_workflow_step)],
            db=db,
        )

    app = EgptOS(
        id=os_id,
        workflows=[
            WorkflowFactory(
                id="ws-factory",
                db=db,
                factory=build_workflow,
            )
        ],
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[secret],
            algorithm="HS256",
        ),
        telemetry=False,
    ).get_app()

    with TestClient(app) as client:
        yield {
            "client": client,
            "secret": secret,
            "os_id": os_id,
            "factory_calls": factory_calls,
        }


def _issue_token(secret: str, os_id: str, sub: str, scopes: list[str]) -> str:
    return jwt.encode(
        {
            "sub": sub,
            "aud": os_id,
            "scopes": scopes,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iat": datetime.now(timezone.utc),
        },
        secret,
        algorithm="HS256",
    )


def _reauthenticate_ws(ws, token: str, expected_user_id: str) -> None:
    """Send an ``authenticate`` action without expecting the connection-open ``connected``
    frame first -- for a connection that has already authenticated at least once.
    """
    ws.send_text(json.dumps({"action": "authenticate", "token": token}))

    for _ in range(5):
        frame = json.loads(ws.receive_text())
        if frame.get("event") == "authenticated" and frame.get("user_id") == expected_user_id:
            return
    raise AssertionError(f"Did not receive authenticated event for user_id={expected_user_id}")


def _authenticate_ws(ws, token: str, expected_user_id: str) -> None:
    connection_event = json.loads(ws.receive_text())
    assert connection_event["event"] == "connected"
    _reauthenticate_ws(ws, token, expected_user_id)


def _drain_until(ws, predicate, max_frames: int = 20) -> str:
    """Read text frames until ``predicate(frame_text)`` is truthy or the limit is hit."""
    for _ in range(max_frames):
        frame = ws.receive_text()
        if predicate(frame):
            return frame
    raise AssertionError(f"No frame matched predicate within {max_frames} frames")


def test_websocket_workflow_factory_receives_trusted_jwt_context(
    websocket_factory_client,
):
    """Regression for #8684: WS start-workflow must forward JWT context to factory.

    Assertions live in the test body (not the factory callback) so a regression
    shows up as a clear diff on captured claims/scopes rather than an opaque
    ``Factory error: ...`` frame.
    """

    client = websocket_factory_client["client"]
    secret = websocket_factory_client["secret"]
    os_id = websocket_factory_client["os_id"]
    factory_calls = websocket_factory_client["factory_calls"]

    token = _issue_token(secret, os_id, sub="alice", scopes=["workflows:run"])

    with client.websocket_connect("/workflows/ws") as ws:
        _authenticate_ws(ws, token, expected_user_id="alice")

        ws.send_text(
            json.dumps(
                {
                    "action": "start-workflow",
                    "workflow_id": "ws-factory",
                    "message": "go",
                }
            )
        )

        started = _drain_until(ws, lambda f: "WorkflowStarted" in f)
        assert '"workflow_id": "ws-factory"' in started

    assert len(factory_calls) == 1, "Factory should be built exactly once"
    call = factory_calls[0]
    assert call["claims"].get("sub") == "alice", (
        f"Factory did not see trusted JWT sub. Saw claims={call['claims']}. "
        "This means websocket_user_context was not forwarded to "
        "handle_workflow_via_websocket — see agno/os/router.py."
    )
    assert "workflows:run" in call["scopes"], f"Factory did not see trusted JWT scopes. Saw scopes={call['scopes']}."


def test_websocket_workflow_factory_rejects_missing_scope(
    websocket_factory_client,
):
    """A token without ``workflows:run`` must be blocked at the dispatcher RBAC gate.

    The factory must not even be invoked in this case — the reject happens
    before ``handle_workflow_via_websocket`` is called.
    """

    client = websocket_factory_client["client"]
    secret = websocket_factory_client["secret"]
    os_id = websocket_factory_client["os_id"]
    factory_calls = websocket_factory_client["factory_calls"]

    token = _issue_token(secret, os_id, sub="alice", scopes=[])

    with client.websocket_connect("/workflows/ws") as ws:
        _authenticate_ws(ws, token, expected_user_id="alice")

        ws.send_text(
            json.dumps(
                {
                    "action": "start-workflow",
                    "workflow_id": "ws-factory",
                    "message": "go",
                }
            )
        )

        error_frame = json.loads(_drain_until(ws, lambda f: '"event": "error"' in f))
        assert error_frame["event"] == "error"
        assert "permission" in error_frame["error"].lower()

    assert factory_calls == [], "Factory must not be invoked when RBAC rejects the call"


# =============================================================================
# WS/HTTP Tenant Resolver Reconciliation (aicoe-tracker#174)
# =============================================================================


def _issue_tenant_token(secret: str, os_id: str, sub: str, scopes: list[str], tenant_id: str | None = None) -> str:
    payload = {
        "sub": sub,
        "aud": os_id,
        "scopes": scopes,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "iat": datetime.now(timezone.utc),
    }
    if tenant_id is not None:
        payload["tid"] = tenant_id
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def db_workflow_tenancy_client(temp_storage_db_file):
    """WS + HTTP client backed by a DB-only (non-factory) workflow scoped to tenant 'acme'.

    Exercises the WS hydration path with a component that only exists in the DB -- the
    in-memory `workflows` list stays empty, so neither the start-workflow nor the reconnect
    lookup can take the in-memory exemption, and hydration always reaches `db.get_config`.
    """
    secret = "x" * 40
    os_id = "ws-db-workflow-os"
    db = SqliteDb(db_file=temp_storage_db_file)

    db.create_component_with_config(
        component_id="db-ws-workflow",
        component_type=ComponentType.WORKFLOW,
        name="DB WS Workflow",
        config={"name": "DB WS Workflow", "id": "db-ws-workflow", "description": "DB-only workflow"},
        stage="published",
        tenant_id="acme",
    )

    app = EgptOS(
        id=os_id,
        db=db,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[secret],
            algorithm="HS256",
            tenant_id_claim="tid",
        ),
        telemetry=False,
    ).get_app()

    with TestClient(app) as client:
        yield {"client": client, "secret": secret, "os_id": os_id}


def test_ws_start_workflow_with_matching_tenant_claim_reaches_the_workflow(db_workflow_tenancy_client):
    """A caller whose JWT carries the configured tenant claim reaches the DB-only workflow
    over WS the same way it would over HTTP -- the positive side of aicoe-tracker#174.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    token = _issue_tenant_token(secret, os_id, sub="alice", scopes=["workflows:run"], tenant_id="acme")

    async def fake_arun(*_args, **kwargs):
        # arun normally emits its own events over the websocket; stand in for that so the
        # test has a frame to wait on without running a real step.
        run_websocket = kwargs.get("websocket")
        if run_websocket is not None:
            await run_websocket.send_text(json.dumps({"event": "WorkflowStarted", "workflow_id": "db-ws-workflow"}))

    with patch.object(Workflow, "arun", new_callable=AsyncMock, side_effect=fake_arun) as mock_arun:
        with client.websocket_connect("/workflows/ws") as ws:
            _authenticate_ws(ws, token, expected_user_id="alice")
            ws.send_text(json.dumps({"action": "start-workflow", "workflow_id": "db-ws-workflow", "message": "go"}))
            frame = _drain_until(ws, lambda f: "WorkflowStarted" in f or "error" in f)

    assert '"event": "error"' not in frame, f"Expected the workflow to hydrate, got: {frame}"
    assert mock_arun.await_count == 1


def test_ws_start_workflow_with_no_tenant_claim_is_refused_not_scoped_to_user_id(db_workflow_tenancy_client):
    """Regression for aicoe-tracker#174.

    Before the fix, a verified caller with no tenant claim silently hydrated over WS against
    `tenant_id=<their own user_id>` (`resolve_tenant_id`'s fallback) instead of failing closed
    like the HTTP resolver does. Against this fixture -- the workflow is scoped to "acme", not
    to any user id -- that surfaced as a misleading "Workflow ... not found" rather than the
    explicit tenant-scope refusal HTTP gives the same principal. Confirm the WS path now
    refuses explicitly instead of guessing a tenant.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    # No "tid" claim -- same principal shape as any other, minus the tenant.
    token = _issue_tenant_token(secret, os_id, sub="bob", scopes=["workflows:run"])

    with client.websocket_connect("/workflows/ws") as ws:
        _authenticate_ws(ws, token, expected_user_id="bob")
        ws.send_text(json.dumps({"action": "start-workflow", "workflow_id": "db-ws-workflow", "message": "go"}))
        error_frame = json.loads(_drain_until(ws, lambda f: '"event": "error"' in f))

    assert error_frame["error"] == TENANCY_REQUIRED_DETAIL


def test_http_and_ws_refuse_the_same_untenanted_principal(db_workflow_tenancy_client):
    """Acceptance criterion from aicoe-tracker#174: HTTP and WS/factory must produce the same
    scope for the same principal. A caller with no tenant claim is refused on both, with the
    same detail message.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    token = _issue_tenant_token(secret, os_id, sub="bob", scopes=["workflows:run", "workflows:read"])

    http_response = client.get("/workflows/db-ws-workflow", headers={"Authorization": f"Bearer {token}"})
    assert http_response.status_code == 403
    assert http_response.json()["detail"] == TENANCY_REQUIRED_DETAIL

    with client.websocket_connect("/workflows/ws") as ws:
        _authenticate_ws(ws, token, expected_user_id="bob")
        ws.send_text(json.dumps({"action": "start-workflow", "workflow_id": "db-ws-workflow", "message": "go"}))
        error_frame = json.loads(_drain_until(ws, lambda f: '"event": "error"' in f))

    assert error_frame["error"] == http_response.json()["detail"]


def test_ws_reauthenticate_clears_a_stale_tenant(db_workflow_tenancy_client):
    """Regression: re-authenticating on the same connection must not leak a prior tenant.

    websocket_user_context["tenant_id"] is cached at authenticate-time, not recomputed per
    dispatch. A connection can send "authenticate" more than once (token refresh, or a client
    re-authenticating as a different principal without reconnecting); the second authenticate
    must fully replace the tenant, not merely leave the first one in place when the new token
    happens to carry no claim.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    tenanted_token = _issue_tenant_token(secret, os_id, sub="alice", scopes=["workflows:run"], tenant_id="acme")
    untenanted_token = _issue_tenant_token(secret, os_id, sub="bob", scopes=["workflows:run"])

    async def fake_arun(*_args, **kwargs):
        run_websocket = kwargs.get("websocket")
        if run_websocket is not None:
            await run_websocket.send_text(json.dumps({"event": "WorkflowStarted", "workflow_id": "db-ws-workflow"}))

    # If arun is ever reached, hydration silently succeeded under the stale "acme" tenant --
    # the leak this test guards against. Patched (rather than left to run for real) so a
    # regression fails the assertion below instead of hanging the test on a frame that would
    # never arrive from a workflow with no steps.
    with patch.object(Workflow, "arun", new_callable=AsyncMock, side_effect=fake_arun):
        with client.websocket_connect("/workflows/ws") as ws:
            _authenticate_ws(ws, tenanted_token, expected_user_id="alice")
            _reauthenticate_ws(ws, untenanted_token, expected_user_id="bob")

            ws.send_text(json.dumps({"action": "start-workflow", "workflow_id": "db-ws-workflow", "message": "go"}))
            frame = json.loads(_drain_until(ws, lambda f: "WorkflowStarted" in f or '"event": "error"' in f))

    assert frame["event"] == "error", (
        f"Expected the stale 'acme' tenant from the first authenticate to be cleared and the "
        f"untenanted second identity refused, got: {frame}"
    )
    assert frame["error"] == TENANCY_REQUIRED_DETAIL


def test_ws_reconnect_with_no_tenant_claim_is_refused(db_workflow_tenancy_client):
    """The reconnect hydration gate (PR #121 review, finding 2).

    A run_id absent from the event buffer sends reconnect to the DB arm, where the workflow is
    hydrated to look the run up -- that hydration must fail closed for an untenanted caller the
    same way start-workflow does, not scope to the caller's user id or surface a misleading
    "not found". Reverting the gate to `hydration_tenant_id = ctx.tenant_id` fails this test.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    token = _issue_tenant_token(secret, os_id, sub="bob", scopes=["workflows:run"])

    with client.websocket_connect("/workflows/ws") as ws:
        _authenticate_ws(ws, token, expected_user_id="bob")
        ws.send_text(
            json.dumps(
                {
                    "action": "reconnect",
                    "run_id": "run-not-in-any-buffer",
                    "workflow_id": "db-ws-workflow",
                    "session_id": "session-1",
                }
            )
        )
        error_frame = json.loads(_drain_until(ws, lambda f: '"event": "error"' in f))

    assert error_frame["error"] == TENANCY_REQUIRED_DETAIL


def test_ws_continue_workflow_with_no_tenant_claim_is_refused(db_workflow_tenancy_client):
    """The continue-workflow hydration gate (PR #121 review, finding 2) -- the only test in the
    repository that drives this action. Reverting the gate to `ws_auth.tenant_id` fails it.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    token = _issue_tenant_token(secret, os_id, sub="bob", scopes=["workflows:run"])

    with client.websocket_connect("/workflows/ws") as ws:
        _authenticate_ws(ws, token, expected_user_id="bob")
        ws.send_text(
            json.dumps({"action": "continue-workflow", "workflow_id": "db-ws-workflow", "run_id": "paused-run-1"})
        )
        error_frame = json.loads(_drain_until(ws, lambda f: '"event": "error"' in f))

    assert error_frame["error"] == TENANCY_REQUIRED_DETAIL


@pytest.mark.parametrize("bad_claim", [12345, {"a": 1}, "", "   "], ids=["int", "object", "empty", "blank"])
def test_ws_rejects_a_malformed_tenant_claim_exactly_as_http_does(db_workflow_tenancy_client, bad_claim):
    """A claim that is present but not a non-empty string is rejected on both transports with
    the same words, and the socket is *not* authenticated (PR #121 review, finding 4). Before
    this, WS silently degraded the caller to untenanted and accepted the session.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    payload = {
        "sub": "mallory",
        "aud": os_id,
        "scopes": ["workflows:run", "workflows:read"],
        "tid": bad_claim,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, secret, algorithm="HS256")

    http_response = client.get("/workflows/db-ws-workflow", headers={"Authorization": f"Bearer {token}"})
    assert http_response.status_code == 401
    assert http_response.json()["detail"] == INVALID_TENANT_CLAIM_ERROR

    with client.websocket_connect("/workflows/ws") as ws:
        assert json.loads(ws.receive_text())["event"] == "connected"
        ws.send_text(json.dumps({"action": "authenticate", "token": token}))
        auth_frame = json.loads(ws.receive_text())
        assert auth_frame["event"] == "auth_error", auth_frame
        assert auth_frame["error"] == http_response.json()["detail"]

        # The socket must still be unauthenticated: any action is met with auth_required.
        ws.send_text(json.dumps({"action": "start-workflow", "workflow_id": "db-ws-workflow", "message": "go"}))
        assert json.loads(ws.receive_text())["event"] == "auth_required"


def test_ws_failed_reauthenticate_drops_the_prior_identity(db_workflow_tenancy_client):
    """A failed re-authenticate must not leave the previous principal live (PR #121 review
    round 2). Authenticate as tenanted alice, then re-authenticate with a garbage token: the
    socket must be unauthenticated afterwards -- a start-workflow is answered with
    auth_required, not a run hydrated under alice's tenant.
    """
    client = db_workflow_tenancy_client["client"]
    secret = db_workflow_tenancy_client["secret"]
    os_id = db_workflow_tenancy_client["os_id"]

    tenanted_token = _issue_tenant_token(secret, os_id, sub="alice", scopes=["workflows:run"], tenant_id="acme")

    async def fake_arun(*_args, **kwargs):
        run_websocket = kwargs.get("websocket")
        if run_websocket is not None:
            await run_websocket.send_text(json.dumps({"event": "WorkflowStarted", "workflow_id": "db-ws-workflow"}))

    # Patched so a regression (the run hydrating under the stale identity) fails the assertion
    # below instead of hanging on a frame that never arrives.
    with patch.object(Workflow, "arun", new_callable=AsyncMock, side_effect=fake_arun) as mock_arun:
        with client.websocket_connect("/workflows/ws") as ws:
            _authenticate_ws(ws, tenanted_token, expected_user_id="alice")

            ws.send_text(json.dumps({"action": "authenticate", "token": "not-a-jwt"}))
            auth_frame = json.loads(_drain_until(ws, lambda f: '"event": "auth_error"' in f))
            assert auth_frame["event"] == "auth_error"

            ws.send_text(json.dumps({"action": "start-workflow", "workflow_id": "db-ws-workflow", "message": "go"}))
            frame = json.loads(
                _drain_until(
                    ws, lambda f: any(e in f for e in ("WorkflowStarted", '"event": "error"', "auth_required"))
                )
            )

    assert frame["event"] == "auth_required", f"Expected the failed re-auth to drop alice's identity, got: {frame}"
    assert mock_arun.await_count == 0


# =============================================================================
# Workflow Version Tests
# =============================================================================


@pytest.fixture
def versioned_workflow_client(temp_storage_db_file):
    """Create a TestClient with a DB-only EgptOS containing two published workflow versions."""
    db = SqliteDb(db_file=temp_storage_db_file)

    db.create_component_with_config(
        component_id="versioned-wf",
        component_type=ComponentType.WORKFLOW,
        name="Workflow Alpha",
        config={"name": "Workflow Alpha", "id": "versioned-wf", "description": "First version"},
        stage="published",
        tenant_id=None,
    )
    db.upsert_config(
        component_id="versioned-wf",
        config={"name": "Workflow Beta", "id": "versioned-wf", "description": "Second version"},
        stage="published",
        tenant_id=None,
    )

    agent_os = EgptOS(db=db)
    app = agent_os.get_app()
    return TestClient(app)


def test_get_workflow_version_returns_specific_version(versioned_workflow_client):
    """Test GET /workflows/{id}?version=1 returns version 1 config."""
    response = versioned_workflow_client.get("/workflows/versioned-wf", params={"version": 1})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Workflow Alpha"
    assert data["description"] == "First version"


def test_get_workflow_version_returns_different_version(versioned_workflow_client):
    """Test GET /workflows/{id}?version=2 returns version 2 config."""
    response = versioned_workflow_client.get("/workflows/versioned-wf", params={"version": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Workflow Beta"
    assert data["description"] == "Second version"


def test_get_workflow_without_version_returns_current(versioned_workflow_client):
    """Test GET /workflows/{id} without version returns latest published version."""
    response = versioned_workflow_client.get("/workflows/versioned-wf")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Workflow Beta"
    assert data["description"] == "Second version"


def test_get_workflow_nonexistent_version_returns_404(versioned_workflow_client):
    """Test GET /workflows/{id}?version=999 returns 404."""
    response = versioned_workflow_client.get("/workflows/versioned-wf", params={"version": 999})
    assert response.status_code == 404


# =============================================================================
# Workflow Version Tests (Async)
# =============================================================================


@pytest.fixture
def versioned_workflow_app(temp_storage_db_file):
    """Create a FastAPI app with a DB-only EgptOS containing two published workflow versions."""
    db = SqliteDb(db_file=temp_storage_db_file)

    db.create_component_with_config(
        component_id="versioned-wf",
        component_type=ComponentType.WORKFLOW,
        name="Workflow Alpha",
        config={"name": "Workflow Alpha", "id": "versioned-wf", "description": "First version"},
        stage="published",
        tenant_id=None,
    )
    db.upsert_config(
        component_id="versioned-wf",
        config={"name": "Workflow Beta", "id": "versioned-wf", "description": "Second version"},
        stage="published",
        tenant_id=None,
    )

    agent_os = EgptOS(db=db)
    return agent_os.get_app()


@pytest.mark.asyncio
async def test_aget_workflow_version_returns_specific_version(versioned_workflow_app):
    """Test async GET /workflows/{id}?version=1 returns version 1 config."""
    async with httpx.AsyncClient(transport=ASGITransport(app=versioned_workflow_app), base_url="http://test") as client:
        response = await client.get("/workflows/versioned-wf", params={"version": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Workflow Alpha"
        assert data["description"] == "First version"


@pytest.mark.asyncio
async def test_aget_workflow_version_returns_different_version(versioned_workflow_app):
    """Test async GET /workflows/{id}?version=2 returns version 2 config."""
    async with httpx.AsyncClient(transport=ASGITransport(app=versioned_workflow_app), base_url="http://test") as client:
        response = await client.get("/workflows/versioned-wf", params={"version": 2})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Workflow Beta"
        assert data["description"] == "Second version"


@pytest.mark.asyncio
async def test_aget_workflow_without_version_returns_current(versioned_workflow_app):
    """Test async GET /workflows/{id} without version returns latest published version."""
    async with httpx.AsyncClient(transport=ASGITransport(app=versioned_workflow_app), base_url="http://test") as client:
        response = await client.get("/workflows/versioned-wf")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Workflow Beta"
        assert data["description"] == "Second version"


@pytest.mark.asyncio
async def test_aget_workflow_nonexistent_version_returns_404(versioned_workflow_app):
    """Test async GET /workflows/{id}?version=999 returns 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=versioned_workflow_app), base_url="http://test") as client:
        response = await client.get("/workflows/versioned-wf", params={"version": 999})
        assert response.status_code == 404
