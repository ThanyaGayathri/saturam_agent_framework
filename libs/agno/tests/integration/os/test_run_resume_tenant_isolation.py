"""HTTP /resume tenant isolation across workflows, agents, and teams (aicoe-tracker#196 QC
round 2, finding 1).

The original fix (PR #155) closed only the WS `reconnect` action for workflows. QC review found
the identical shape -- `event_buffer.get_run_status(run_id)` read straight from the buffer with
no tenant check -- untouched in the HTTP `/resume` SSE endpoint for all three component types:
hydrating the workflow/agent/team with `tenant_for_direct_read` proves the caller's tenant can
see that *component*, not that this specific buffered *run* belongs to their tenant, since an
in-memory/code-defined component's hydration is exempt for every tenant. These tests seed the
event buffer directly (as the WS regression tests in test_workflow_runs.py do) rather than
driving a real model run, so they exercise the gate itself without needing OPENAI_API_KEY or a
recorded cassette.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from egpt.agent.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.managers import event_buffer
from egpt.run.base import RunStatus
from egpt.team.team import Team
from egpt.workflow.workflow import Workflow


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
def resume_tenancy_client(temp_storage_db_file):
    """A tenancy-enabled EgptOS with an in-memory (code-defined) workflow, agent, and team.

    In-memory components are the exploitable case (aicoe-tracker#196): their hydration is
    exempt from the fail-closed tenant gate for every tenant (code-defined components are
    deployment-owned, not tenant rows -- see `tenant_for_direct_read`), so ANY tenant's caller
    reaches the buffer-arm gate this fix adds. A DB-only component wouldn't exercise the same
    path: a cross-tenant caller is already refused at hydration (config not found for their
    tenant) before ever reaching the buffer.
    """
    secret = "x" * 40
    os_id = "resume-tenancy-os"
    db = SqliteDb(db_file=temp_storage_db_file)

    member_agent = Agent(id="mem-agent-member", name="member", db=db)
    workflow = Workflow(id="mem-workflow", name="mem-workflow", db=db)
    agent = Agent(id="mem-agent", name="mem-agent", db=db)
    team = Team(id="mem-team", name="mem-team", members=[member_agent], db=db)

    app = EgptOS(
        id=os_id,
        db=db,
        workflows=[workflow],
        agents=[agent],
        teams=[team],
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


def _seed_buffered_run(run_id: str, tenant_id: str | None, *, register: bool = True) -> None:
    # RunStatus.completed (not running): the "still active" buffer arm streams indefinitely,
    # waiting on live events that will never arrive for a run nothing is actually executing --
    # the "finished" arm replays what's buffered and returns immediately, which is all these
    # tests need to observe (the gate's refusal/pass-through, not live streaming).
    event_buffer.run_metadata[run_id] = {
        "status": RunStatus.completed,
        "created_at": 0,
        "last_updated": 0,
        "completed_at": 0,
    }
    if register:
        event_buffer.register_run_tenant(run_id, tenant_id)


@pytest.mark.parametrize(
    "path,scope",
    [
        ("/workflows/mem-workflow/runs/{run_id}/resume", "workflows:run"),
        ("/agents/mem-agent/runs/{run_id}/resume", "agents:run"),
        ("/teams/mem-team/runs/{run_id}/resume", "teams:run"),
    ],
    ids=["workflow", "agent", "team"],
)
def test_http_resume_is_refused_for_a_mismatched_tenant(resume_tenancy_client, path, scope):
    client = resume_tenancy_client["client"]
    secret = resume_tenancy_client["secret"]
    os_id = resume_tenancy_client["os_id"]
    run_id = f"buffered-{path.split('/')[1]}-mismatch"

    _seed_buffered_run(run_id, "acme")
    try:
        attacker_token = _issue_tenant_token(secret, os_id, sub="mallory", scopes=[scope], tenant_id="beta")
        response = client.post(
            path.format(run_id=run_id),
            data={},
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        body = response.text
        assert f"Run {run_id} not found" in body, body
    finally:
        event_buffer.cleanup_run(run_id)


@pytest.mark.parametrize(
    "path,scope",
    [
        ("/workflows/mem-workflow/runs/{run_id}/resume", "workflows:run"),
        ("/agents/mem-agent/runs/{run_id}/resume", "agents:run"),
        ("/teams/mem-team/runs/{run_id}/resume", "teams:run"),
    ],
    ids=["workflow", "agent", "team"],
)
def test_http_resume_succeeds_for_the_owning_tenant(resume_tenancy_client, path, scope):
    client = resume_tenancy_client["client"]
    secret = resume_tenancy_client["secret"]
    os_id = resume_tenancy_client["os_id"]
    run_id = f"buffered-{path.split('/')[1]}-match"

    _seed_buffered_run(run_id, "acme")
    try:
        owner_token = _issue_tenant_token(secret, os_id, sub="alice", scopes=[scope], tenant_id="acme")
        response = client.post(
            path.format(run_id=run_id),
            data={},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert response.status_code == 200
        body = response.text
        assert f"Run {run_id} not found" not in body, body
    finally:
        event_buffer.cleanup_run(run_id)


@pytest.mark.parametrize(
    "path,scope",
    [
        ("/workflows/mem-workflow/runs/{run_id}/resume", "workflows:run"),
        ("/agents/mem-agent/runs/{run_id}/resume", "agents:run"),
        ("/teams/mem-team/runs/{run_id}/resume", "teams:run"),
    ],
    ids=["workflow", "agent", "team"],
)
def test_http_resume_refuses_a_buffered_run_no_start_handler_ever_registered(resume_tenancy_client, path, scope):
    """A run in the buffer that no start/continue handler registered a tenant for (the shape
    this whole gate exists to close, aicoe-tracker#196) must fail closed once tenancy is
    enabled -- there's nothing to reconcile the caller's tenant against.
    """
    client = resume_tenancy_client["client"]
    secret = resume_tenancy_client["secret"]
    os_id = resume_tenancy_client["os_id"]
    run_id = f"buffered-{path.split('/')[1]}-unregistered"

    _seed_buffered_run(run_id, None, register=False)
    try:
        token = _issue_tenant_token(secret, os_id, sub="alice", scopes=[scope], tenant_id="acme")
        response = client.post(
            path.format(run_id=run_id),
            data={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.text
        assert f"Run {run_id} not found" in body, body
    finally:
        event_buffer.cleanup_run(run_id)


def test_http_resume_workflow_still_replays_when_tenancy_is_disabled(temp_storage_db_file):
    """Pinning test for the tenancy-off no-op path (QC round 2, nit 7): the gate must not touch
    behavior in a deployment that never opted into tenancy.
    """
    from egpt.workflow.step import Step
    from egpt.workflow.types import StepInput, StepOutput
    from egpt.workflow.workflow import Workflow

    def _step(step_input: StepInput) -> StepOutput:
        return StepOutput(content="ok")

    db = SqliteDb(db_file=temp_storage_db_file)
    workflow = Workflow(id="no-tenancy-wf", name="no-tenancy-wf", steps=[Step(name="s", executor=_step)], db=db)
    app = EgptOS(id="no-tenancy-os", db=db, workflows=[workflow], telemetry=False).get_app()

    run_id = "buffered-no-tenancy"
    _seed_buffered_run(run_id, None, register=False)
    try:
        with TestClient(app) as client:
            response = client.post(f"/workflows/no-tenancy-wf/runs/{run_id}/resume", data={})
        assert response.status_code == 200
        body = response.text
        assert f"Run {run_id} not found" not in body, body
    finally:
        event_buffer.cleanup_run(run_id)
