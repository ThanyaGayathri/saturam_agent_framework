"""Tenant scoping of workflow hydration, including the step DB fallback (EGPT-2.5).

A workflow's steps reference agents/teams by id and fall back to a DB read when the registry
does not hold them -- that fallback is the recursion that must carry the caller's tenant, or
a scoped workflow could still execute another tenant's agent.
"""

import copy
from unittest.mock import MagicMock

from egpt.db.base import BaseDb
from egpt.workflow.workflow import get_workflow_by_id

WORKFLOW_ROW = {
    "component_id": "wf-1",
    "config": {
        "name": "Stored Workflow",
        "steps": [{"type": "Step", "name": "step-1", "agent_id": "step-agent"}],
    },
    "tenant_id": "acme",
    "version": 1,
    "stage": "published",
}

STEP_AGENT_ROW = {
    "component_id": "step-agent",
    "config": {"name": "Step Agent"},
    "tenant_id": "acme",
    "version": 1,
    "stage": "published",
}


def _mock_db(rows=None):
    rows = rows if rows is not None else {"wf-1": WORKFLOW_ROW, "step-agent": STEP_AGENT_ROW}
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"

    def get_config(component_id=None, version=None, label=None, *, tenant_id, **_):
        row = rows.get(component_id)
        if row is None:
            return None
        if tenant_id is not None and tenant_id != row.get("tenant_id"):
            return None
        return copy.deepcopy(row)

    db.get_config = MagicMock(side_effect=get_config)
    db.get_links = MagicMock(return_value=[])
    return db


class TestStepFallbackCarriesTheTenant:
    def test_the_step_agent_read_is_scoped_to_the_caller(self):
        db = _mock_db()
        workflow = get_workflow_by_id(db=db, id="wf-1", tenant_id="acme")
        assert workflow is not None
        step = workflow.steps[0]
        assert step.agent is not None and step.agent.name == "Step Agent"
        tenants = [call.kwargs["tenant_id"] for call in db.get_config.call_args_list]
        assert tenants == ["acme", "acme"]

    def test_a_cross_tenant_step_agent_fails_the_hydration_loudly(self):
        """The foreign agent resolves as missing, so the step has no executor and the
        workflow raises HydrationError -- a broken workflow must not run half-built."""
        import pytest

        from egpt.registry.hydrate import HydrationError

        rows = {"wf-1": WORKFLOW_ROW, "step-agent": {**STEP_AGENT_ROW, "tenant_id": "globex"}}
        db = _mock_db(rows=rows)
        with pytest.raises(HydrationError, match="wf-1"):
            get_workflow_by_id(db=db, id="wf-1", tenant_id="acme")

    def test_the_link_read_is_scoped_too(self):
        db = _mock_db()
        get_workflow_by_id(db=db, id="wf-1", tenant_id="acme")
        assert db.get_links.call_args.kwargs["tenant_id"] == "acme"

    def test_a_cross_tenant_workflow_does_not_resolve_at_all(self):
        db = _mock_db()
        assert get_workflow_by_id(db=db, id="wf-1", tenant_id="globex") is None
