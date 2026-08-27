"""``Workflow.tenant_id`` and the session read/write consistency it enables (aicoe-tracker#197).

Threading a resolved tenant into ``aget_run_output``/``aget_session`` only closes the isolation
gap the issue describes if the *write* that created the session used the same tenant -- otherwise
a scoped read would filter against a row still carrying ``tenant_id=NULL`` and see nothing, a
functional regression rather than a fix. Rather than threading ``tenant_id`` through the ~100
internal call sites in the run loop, ``Workflow`` carries it as an instance attribute
(``self.tenant_id``, stamped at hydration by ``get_workflow_by_id``/``Workflow.from_dict``), and
both the read path (``_aread_session``/``_read_session``) and the write path
(``_aupsert_session``/``_upsert_session``) fall back to it when a call does not explicitly
override it. This file pins that the fallback is symmetric and that ``deep_copy()`` -- called
per-request by ``os/utils.py`` for request isolation -- does not silently drop it, the way a
plain (non-dataclass-field) attribute would (see ``egpt/agent/_utils.py::deep_copy``'s handling
of ``model_provider`` for what that failure mode looks like).
"""

import os
import tempfile

import pytest

from egpt.integrations.db.sqlite.sqlite import SqliteDb
from egpt.session.workflow import WorkflowSession
from egpt.workflow.workflow import Workflow

TENANT_A = "acme"
TENANT_B = "globex"


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = SqliteDb(db_file=path)
    yield database
    try:
        os.unlink(path)
    except OSError:
        pass


class TestTenantIdIsADeclaredField:
    def test_default_is_none(self):
        assert Workflow(id="w1").tenant_id is None

    def test_constructor_accepts_it(self):
        assert Workflow(id="w1", tenant_id=TENANT_A).tenant_id == TENANT_A

    def test_survives_deep_copy(self):
        """The property that motivated a declared dataclass field over a plain attribute."""
        original = Workflow(id="w1", tenant_id=TENANT_A)
        copy = original.deep_copy()
        assert copy.tenant_id == TENANT_A

    def test_deep_copy_with_update_can_override_it(self):
        original = Workflow(id="w1", tenant_id=TENANT_A)
        copy = original.deep_copy(update={"tenant_id": TENANT_B})
        assert copy.tenant_id == TENANT_B


class TestReadWriteConsistency:
    """The core correctness property: a session this workflow creates must be findable by a
    scoped read against the same instance, and invisible to a differently-scoped one."""

    def test_a_tenant_stamped_instance_can_read_back_what_it_wrote(self, db):
        wf = Workflow(id="w1", db=db, tenant_id=TENANT_A)
        wf.save_session(WorkflowSession(session_id="s1", workflow_id="w1", user_id="u1"))

        assert wf.get_session(session_id="s1") is not None

    def test_a_differently_tenanted_instance_cannot_read_it(self, db):
        wf_a = Workflow(id="w1", db=db, tenant_id=TENANT_A)
        wf_a.save_session(WorkflowSession(session_id="s1", workflow_id="w1", user_id="u1"))

        wf_b = Workflow(id="w1", db=db, tenant_id=TENANT_B)
        assert wf_b.get_session(session_id="s1") is None

    def test_an_unscoped_instance_is_unaffected(self, db):
        """Zero behaviour change for a deployment that never sets tenant_id at all."""
        wf = Workflow(id="w1", db=db)
        wf.save_session(WorkflowSession(session_id="s1", workflow_id="w1", user_id="u1"))

        assert wf.get_session(session_id="s1") is not None
        raw = db.get_session("s1", deserialize=False)
        assert raw["tenant_id"] is None

    def test_the_row_is_actually_stamped_in_the_database(self, db):
        wf = Workflow(id="w1", db=db, tenant_id=TENANT_A)
        wf.save_session(WorkflowSession(session_id="s1", workflow_id="w1", user_id="u1"))

        raw = db.get_session("s1", tenant_id=TENANT_A, deserialize=False)
        assert raw["tenant_id"] == TENANT_A

    def test_an_explicit_override_wins_over_self_tenant_id(self, db):
        wf = Workflow(id="w1", db=db, tenant_id=TENANT_A)
        wf.save_session(WorkflowSession(session_id="s1", workflow_id="w1", user_id="u1"))

        # Explicitly requesting a different tenant is honoured, not silently coerced to self.tenant_id.
        assert wf.get_session(session_id="s1", tenant_id=TENANT_B) is None
        assert wf.get_session(session_id="s1", tenant_id=TENANT_A) is not None

    def test_get_run_output_inherits_the_same_scoping(self, db):
        from egpt.run.workflow import WorkflowRunOutput

        wf = Workflow(id="w1", db=db, tenant_id=TENANT_A)
        session = WorkflowSession(session_id="s1", workflow_id="w1", user_id="u1")
        session.upsert_run(WorkflowRunOutput(run_id="r1", workflow_id="w1", session_id="s1"))
        wf.save_session(session)

        assert wf.get_run_output(run_id="r1", session_id="s1") is not None

        wf_b = Workflow(id="w1", db=db, tenant_id=TENANT_B)
        assert wf_b.get_run_output(run_id="r1", session_id="s1") is None


class TestHydrationStampsTenantId:
    def test_get_workflow_by_id_stamps_the_callers_tenant(self, db):
        wf = Workflow(id="w1", db=db)
        wf.save(db=db, tenant_id=TENANT_A)

        from egpt.workflow.workflow import get_workflow_by_id

        loaded = get_workflow_by_id(db=db, id="w1", tenant_id=TENANT_A)
        assert loaded is not None
        assert loaded.tenant_id == TENANT_A

    def test_get_workflow_by_id_stamps_none_for_an_unscoped_caller(self, db):
        wf = Workflow(id="w1", db=db)
        wf.save(db=db, tenant_id=None)

        from egpt.workflow.workflow import get_workflow_by_id

        loaded = get_workflow_by_id(db=db, id="w1", tenant_id=None)
        assert loaded is not None
        assert loaded.tenant_id is None
