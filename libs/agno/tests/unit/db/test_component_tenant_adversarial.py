"""Adversarial-tenant isolation on the component data layer (EGPT-2.7).

The acceptance gate for EGPT-2.7: tenant B, holding valid credentials and a correct component
id, cannot read, list, update, delete or hydrate anything belonging to tenant A. Isolation is
proven here rather than assumed from the presence of a predicate.

How this differs from ``test_component_tenant_scoping.py``
---------------------------------------------------------
That suite (EGPT-2.1) asks "does the tenant predicate work?", one behaviour per test. This one
takes the attacker's position and sweeps *every* method in one fixture, then asserts on tenant
A's rows with raw SQL afterwards. The distinction matters for the mutations: a
``delete_component`` that returns ``False`` has told you nothing about whether it deleted
something on the way to returning ``False``. Only reading A's tables afterwards proves it did
not, so every mutation here is checked by its return value *and* by the state it left behind.

The audit trail is part of the threat model
-------------------------------------------
EGPT-2.6 made every mutation write an audit row. That created a second surface: refused
cross-tenant mutations must not write audit rows (or B could write into A's history), and B must
not be able to read A's history. Both are asserted.

Setup is one shared, fully-populated component
----------------------------------------------
Tenant A owns ``agent-a``: published, two config versions, a link to a child component, and the
audit history all of that generated. A component with only a draft and no links would let a
method pass by finding nothing rather than by being correctly scoped.
"""

import os
import tempfile

import pytest
from sqlalchemy import text

from egpt.db.base import ComponentType
from egpt.db.component_scope import TenantScopeRequiredError

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

COMPONENT_TABLES = (
    "egpt_components",
    "egpt_component_configs",
    "egpt_component_links",
    "egpt_component_audit",
)


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


@pytest.fixture(autouse=True)
def _no_ambient_strict_mode(monkeypatch):
    """Clear ``EGPT_REQUIRE_TENANT_ID`` for every test in this module.

    These tests assert on *scoping*, which needs the unscoped reads a strict db refuses -- the
    NULL-tenant cases below in particular. ``BaseDb`` reads the variable at construction, so an
    exported value would turn those assertions into errors about an unrelated control.
    """
    monkeypatch.delenv("EGPT_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_TENANT_ID", raising=False)


@pytest.fixture
def sqlite_db():
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def seeded(sqlite_db):
    """Tenant A owns a published component with a child link, a draft, and audit history."""
    sqlite_db.create_component_with_config(
        component_id="child-a",
        component_type=ComponentType.AGENT,
        name="Child A",
        config={"model": "gpt-5.5"},
        stage="published",
        tenant_id=TENANT_A,
    )
    sqlite_db.create_component_with_config(
        component_id="agent-a",
        component_type=ComponentType.TEAM,
        name="Agent A",
        config={"model": "gpt-5.5", "v": 1},
        stage="published",
        links=[
            {
                "link_kind": "member",
                "link_key": "child",
                "child_component_id": "child-a",
                "child_version": 1,
                "position": 0,
            }
        ],
        tenant_id=TENANT_A,
    )
    # A second, draft version so delete_config and set_current_version have a real target.
    sqlite_db.upsert_config(
        component_id="agent-a",
        config={"model": "gpt-5.5", "v": 2},
        stage="draft",
        tenant_id=TENANT_A,
    )
    return sqlite_db


def _snapshot(db):
    """Every row of every component table, as comparable tuples."""
    snap = {}
    with db.Session() as sess:
        for table in COMPONENT_TABLES:
            rows = sess.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2")).fetchall()
            snap[table] = [tuple(r) for r in rows]
    return snap


def _audit_rows(db, tenant_id=None):
    with db.Session() as sess:
        if tenant_id is None:
            return sess.execute(text("SELECT component_id, action, tenant_id FROM egpt_component_audit")).fetchall()
        return sess.execute(
            text("SELECT component_id, action, tenant_id FROM egpt_component_audit WHERE tenant_id = :t"),
            {"t": tenant_id},
        ).fetchall()


# ============================================================================
# READS: TENANT B SEES NOTHING OF TENANT A
# ============================================================================


class TestTenantBCannotRead:
    def test_get_component(self, seeded):
        assert seeded.get_component("agent-a", tenant_id=TENANT_B) is None

    def test_get_component_is_indistinguishable_from_missing(self, seeded):
        """A foreign component and a nonexistent one must give the same answer.

        Any difference -- a distinct error, a different shape -- is an existence oracle that
        confirms tenant A owns that id.
        """
        assert seeded.get_component("agent-a", tenant_id=TENANT_B) == seeded.get_component(
            "no-such-component", tenant_id=TENANT_B
        )

    def test_list_components_excludes_rows_and_the_count(self, seeded):
        """The total must exclude A's rows too, not just the page.

        A correct page with an inflated total still leaks: it tells B how many components A has,
        and paginating an empty list is how you notice.
        """
        rows, total = seeded.list_components(tenant_id=TENANT_B)
        assert rows == []
        assert total == 0

    def test_get_config(self, seeded):
        assert seeded.get_config("agent-a", tenant_id=TENANT_B) is None

    def test_get_config_by_explicit_version(self, seeded):
        assert seeded.get_config("agent-a", version=1, tenant_id=TENANT_B) is None

    def test_list_configs(self, seeded):
        assert seeded.list_configs("agent-a", tenant_id=TENANT_B) == []

    def test_get_links(self, seeded):
        assert seeded.get_links("agent-a", version=1, tenant_id=TENANT_B) == []

    def test_get_dependents(self, seeded):
        """B must not learn that A's child is referenced by something."""
        assert seeded.get_dependents("child-a", tenant_id=TENANT_B) == []

    def test_load_component_graph(self, seeded):
        """Hydration is the path that resolves children, so it gets its own assertion."""
        assert seeded.load_component_graph("agent-a", tenant_id=TENANT_B) is None

    def test_list_component_audit(self, seeded):
        """B cannot read A's change history -- who edited what, and when."""
        records, total = seeded.list_component_audit("agent-a", tenant_id=TENANT_B)
        assert records == []
        assert total == 0

    def test_tenant_a_still_sees_everything(self, seeded):
        """The mirror of every assertion above: scoping must not have broken the owner."""
        assert seeded.get_component("agent-a", tenant_id=TENANT_A) is not None
        rows, total = seeded.list_components(tenant_id=TENANT_A)
        assert total == 2
        assert seeded.get_config("agent-a", tenant_id=TENANT_A) is not None
        assert len(seeded.list_configs("agent-a", tenant_id=TENANT_A)) == 2
        assert seeded.get_links("agent-a", version=1, tenant_id=TENANT_A) != []
        assert seeded.get_dependents("child-a", tenant_id=TENANT_A) != []
        assert seeded.load_component_graph("agent-a", tenant_id=TENANT_A) is not None
        _, audit_total = seeded.list_component_audit("agent-a", tenant_id=TENANT_A)
        assert audit_total > 0


# ============================================================================
# MUTATIONS: TENANT B CANNOT CHANGE TENANT A'S ROWS
# ============================================================================


class TestTenantBCannotMutate:
    def test_delete_component_soft(self, seeded):
        before = _snapshot(seeded)
        assert seeded.delete_component("agent-a", tenant_id=TENANT_B) is False
        assert _snapshot(seeded) == before

    def test_delete_component_hard(self, seeded):
        before = _snapshot(seeded)
        assert seeded.delete_component("agent-a", hard_delete=True, tenant_id=TENANT_B) is False
        assert _snapshot(seeded) == before

    def test_delete_config(self, seeded):
        before = _snapshot(seeded)
        assert seeded.delete_config("agent-a", version=2, tenant_id=TENANT_B) is False
        assert _snapshot(seeded) == before

    def test_set_current_version(self, seeded):
        before = _snapshot(seeded)
        assert seeded.set_current_version("agent-a", version=1, tenant_id=TENANT_B) is False
        assert _snapshot(seeded) == before

    def test_upsert_config(self, seeded):
        """B cannot add a version to A's component.

        Refused as "component not found", because from B's position it is not found.
        """
        before = _snapshot(seeded)
        with pytest.raises(ValueError):
            seeded.upsert_config("agent-a", config={"model": "evil"}, tenant_id=TENANT_B)
        assert _snapshot(seeded) == before

    def test_create_component_with_config_on_a_taken_id(self, seeded):
        """The global primary key must not let B overwrite A's component by re-creating its id."""
        before = _snapshot(seeded)
        with pytest.raises(ValueError):
            seeded.create_component_with_config(
                component_id="agent-a",
                component_type=ComponentType.AGENT,
                name="Hijacked",
                config={"model": "evil"},
                tenant_id=TENANT_B,
            )
        assert _snapshot(seeded) == before

    def test_upsert_component_creates_a_new_row_rather_than_editing_a_foreign_one(self, seeded):
        """The subtlest case, and the reason this is asserted on the row and not the return value.

        ``upsert_component`` is create-or-update. Scoped to B, A's row is invisible, so the
        upsert takes the *create* branch -- and the primary key is global, so what happens next
        is an insert against an id that already exists. What must never happen is A's row being
        updated to B's values.
        """
        a_before = seeded.get_component("agent-a", tenant_id=TENANT_A)
        try:
            seeded.upsert_component(
                component_id="agent-a",
                component_type=ComponentType.AGENT,
                name="Hijacked",
                tenant_id=TENANT_B,
            )
        except Exception:
            pass  # an IntegrityError on the shared primary key is an acceptable outcome
        a_after = seeded.get_component("agent-a", tenant_id=TENANT_A)
        assert a_after == a_before, "tenant A's component was modified by tenant B"
        assert a_after["name"] == "Agent A"
        assert a_after["tenant_id"] == TENANT_A


# ============================================================================
# THE AUDIT TRAIL IS NOT A SIDE CHANNEL
# ============================================================================


class TestAuditTrailIsolation:
    def test_refused_mutations_write_no_audit_rows(self, seeded):
        """A refused cross-tenant mutation must leave the history untouched.

        Otherwise B can append to A's audit trail -- writing into a record A is expected to be
        able to trust, and revealing which ids exist by which writes succeed.
        """
        before = _audit_rows(seeded)

        seeded.delete_component("agent-a", tenant_id=TENANT_B)
        seeded.delete_config("agent-a", version=2, tenant_id=TENANT_B)
        seeded.set_current_version("agent-a", version=1, tenant_id=TENANT_B)
        with pytest.raises(ValueError):
            seeded.upsert_config("agent-a", config={"model": "evil"}, tenant_id=TENANT_B)

        assert _audit_rows(seeded) == before

    def test_no_audit_row_is_ever_stamped_with_the_attacker_tenant(self, seeded):
        seeded.delete_component("agent-a", tenant_id=TENANT_B)
        assert _audit_rows(seeded, tenant_id=TENANT_B) == []

    def test_tenant_a_history_survives_the_full_sweep(self, seeded):
        a_before = _audit_rows(seeded, tenant_id=TENANT_A)
        assert a_before, "fixture should have produced audit history"

        _sweep_every_method_as(seeded, TENANT_B)

        assert _audit_rows(seeded, tenant_id=TENANT_A) == a_before


# ============================================================================
# THE FULL SWEEP
# ============================================================================


def _sweep_every_method_as(db, tenant_id):
    """Call all fifteen component methods against tenant A's ids as ``tenant_id``.

    Exceptions are swallowed: this is the attacker trying everything and not caring what comes
    back. What the caller asserts is the state left behind.
    """
    attempts = [
        lambda: db.get_component("agent-a", tenant_id=tenant_id),
        lambda: db.list_components(tenant_id=tenant_id),
        lambda: db.get_config("agent-a", tenant_id=tenant_id),
        lambda: db.list_configs("agent-a", tenant_id=tenant_id),
        lambda: db.get_links("agent-a", version=1, tenant_id=tenant_id),
        lambda: db.get_dependents("child-a", tenant_id=tenant_id),
        lambda: db.load_component_graph("agent-a", tenant_id=tenant_id),
        lambda: db.list_component_audit("agent-a", tenant_id=tenant_id),
        lambda: db.upsert_component(component_id="agent-a", name="Hijacked", tenant_id=tenant_id),
        lambda: db.upsert_config("agent-a", config={"model": "evil"}, tenant_id=tenant_id),
        lambda: db.create_component_with_config(
            component_id="agent-a",
            component_type=ComponentType.AGENT,
            name="Hijacked",
            config={"model": "evil"},
            tenant_id=tenant_id,
        ),
        lambda: db.set_current_version("agent-a", version=1, tenant_id=tenant_id),
        lambda: db.delete_config("agent-a", version=2, tenant_id=tenant_id),
        lambda: db.delete_component("agent-a", hard_delete=True, tenant_id=tenant_id),
        # The fifteenth method, and the one an attacker would actually lean on: it is
        # deliberately unscoped (aicoe-tracker#176) and takes no tenant_id at all, so it is
        # the one call in this sweep that is *supposed* to see tenant A's row -- the
        # assertion below is that seeing it changes nothing about tenant A's state, not that
        # the call itself is refused.
        lambda: db.component_id_is_taken("agent-a"),
    ]
    for attempt in attempts:
        try:
            attempt()
        except Exception:
            pass


def test_tenant_a_data_survives_the_full_adversarial_sweep(seeded):
    """The single assertion the acceptance criterion reduces to.

    Every method, every table, byte for byte. This is what catches a mutation that scopes its
    precheck but not the statement underneath -- the return value looks like a clean refusal
    while a row has already changed.
    """
    before = _snapshot(seeded)

    _sweep_every_method_as(seeded, TENANT_B)

    after = _snapshot(seeded)
    for table in COMPONENT_TABLES:
        assert after[table] == before[table], f"tenant B altered {table}"


def test_tenant_a_can_still_operate_after_the_sweep(seeded):
    """Isolation must not have been achieved by breaking the database."""
    _sweep_every_method_as(seeded, TENANT_B)

    assert seeded.get_component("agent-a", tenant_id=TENANT_A) is not None
    version = seeded.upsert_config("agent-a", config={"model": "gpt-5.5", "v": 3}, tenant_id=TENANT_A)
    assert version["version"] == 3
    assert seeded.delete_component("agent-a", tenant_id=TENANT_A) is True


# ============================================================================
# NULL-TENANT ROWS ARE THEIR OWN SCOPE
# ============================================================================


class TestNonHttpWritersInheritTheDbGuarantee:
    """What ``Agent.save()`` and friends do about tenancy, pinned rather than asserted in prose.

    These entry points keep ``tenant_id=None`` as their default. That is a deliberate,
    documented limit, not an oversight: ``Agent.save()`` is the framework's most-used public
    API, and requiring a tenant would force every single-tenant deployment to write
    ``save(tenant_id=None)`` -- ceremony with no isolation gain, since they have one tenant.

    The consequence is real and is recorded here as executable fact rather than a claim in a
    docstring: on a **non-strict** db an unscoped save overwrites a foreign tenant's row, and
    on a **strict** db it is refused. ``require_tenant_id`` is the control; the default is not.
    If anyone later decides that trade is wrong, these two tests are what changes.
    """

    def _acme_row(self, db):
        return db.get_component("shared-id", tenant_id="acme")

    def _seed_acme(self, db):
        db.create_component_with_config(
            component_id="shared-id",
            component_type=ComponentType.AGENT,
            name="acme's agent",
            config={"owner": "acme"},
            stage="published",
            tenant_id="acme",
        )

    def test_unscoped_save_overwrites_a_foreign_row_on_a_non_strict_db(self, sqlite_db):
        """The exposure, stated as a test so it cannot be quietly forgotten.

        Note the row keeps ``tenant_id='acme'``: the victim's own correctly-scoped read serves
        the foreign config, so there is nothing for them to detect.
        """
        from egpt.agent.agent import Agent

        self._seed_acme(sqlite_db)
        assert sqlite_db.require_tenant_id is False

        Agent(id="shared-id", name="OTHER WRITER", db=sqlite_db).save()

        row = self._acme_row(sqlite_db)
        assert row["name"] == "OTHER WRITER"
        assert row["tenant_id"] == "acme"

    def test_strict_mode_refuses_the_unscoped_save(self, sqlite_db):
        """...and this is why ``require_tenant_id`` is the control rather than the default."""
        from egpt.agent.agent import Agent
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        self._seed_acme(sqlite_db)
        strict = SqliteDb(db_file=sqlite_db.db_file, require_tenant_id=True)

        with pytest.raises(TenantScopeRequiredError):
            Agent(id="shared-id", name="OTHER WRITER", db=strict).save()

        assert self._acme_row(sqlite_db)["name"] == "acme's agent"

    def test_a_scoped_save_cannot_reach_a_foreign_row(self, sqlite_db):
        """Naming a tenant is sufficient even without strict mode."""
        from egpt.agent.agent import Agent

        self._seed_acme(sqlite_db)
        try:
            Agent(id="shared-id", name="OTHER WRITER", db=sqlite_db).save(tenant_id=TENANT_B)
        except Exception:
            pass  # an IntegrityError on the shared primary key is an acceptable outcome
        assert self._acme_row(sqlite_db)["name"] == "acme's agent"

    def test_strict_mode_survives_the_rehydration_clone(self, sqlite_db):
        """The compounding failure: a clone that dropped the flag removed the mitigation.

        ``EgptOS`` enables strict mode by mutating the registered instance, while
        ``resolve_db_from_config`` *clones* on the component rehydration path -- so a clone that
        dropped ``require_tenant_id`` would leave the guard missing exactly where components get
        written, for an operator who had correctly opted in.
        """
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        strict = SqliteDb(db_file=sqlite_db.db_file, require_tenant_id=True)
        assert strict.clone_with_table_overrides(session_table="other").require_tenant_id is True
        assert SqliteDb.from_dict(strict.to_dict()).require_tenant_id is True


class TestNullTenantRows:
    def test_a_null_tenant_row_is_invisible_to_every_named_tenant(self, sqlite_db):
        """Pre-multi-tenancy rows are not a shared pool that any tenant can claim."""
        sqlite_db.create_component_with_config(
            component_id="legacy",
            component_type=ComponentType.AGENT,
            name="Legacy",
            config={"model": "gpt-5.5"},
            tenant_id=None,
        )
        assert sqlite_db.get_component("legacy", tenant_id=TENANT_A) is None
        assert sqlite_db.get_component("legacy", tenant_id=TENANT_B) is None
        assert sqlite_db.get_component("legacy", tenant_id=None) is not None

    def test_a_named_tenant_row_is_visible_unscoped(self, seeded):
        """``tenant_id=None`` is the widest scope, not a third tenant.

        Documented behaviour rather than a leak -- and precisely why ``require_tenant_id`` exists
        to refuse it in a deployment that scopes by tenant.
        """
        assert seeded.get_component("agent-a", tenant_id=None) is not None
