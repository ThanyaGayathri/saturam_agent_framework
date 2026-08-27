"""Seeding and importing stay inside one tenant (EGPT-2.8).

There is a limit here worth stating plainly, because the acceptance criterion "seed/import are
tenant-scoped" can be read as promising more than the schema can deliver: ``component_id`` is a
**global** primary key (EGPT-2.1 deliberately kept ``tenant_id`` out of it), so two tenants can
never hold the same id. Tenant-scoped therefore means:

* rows are stamped with the *target* tenant, never the source's;
* reads, ownership checks and conflict probes are tenant-aware;
* a component belonging to another tenant is never written to, under any conflict policy.

It does **not** mean the same bundle seeds into two tenants unchanged. That needs ``id_prefix``,
and the tests below pin both halves of that: the plain attempt refuses, the prefixed one works.
"""

import json

import pytest

from egpt.components.io import import_component, seed_from_directory
from egpt.registry import Registry
from egpt.registry.io import ComponentIdConflictError, export_component

TENANT_A = "acme"
TENANT_B = "globex"

TEAM_BUNDLE = {
    "format": "egpt.component-bundle",
    "format_version": 1,
    "schema_version": 1,
    "root": "support-team",
    "components": [
        {
            "component_id": "faq-agent",
            "component_type": "agent",
            "name": "FAQ Agent",
            "stage": "published",
            "config": {
                "id": "faq-agent",
                "name": "FAQ Agent",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
            },
            "links": [],
        },
        {
            "component_id": "support-team",
            "component_type": "team",
            "name": "Support Team",
            "stage": "published",
            "config": {
                "id": "support-team",
                "name": "Support Team",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
                "members": [{"type": "agent", "agent_id": "faq-agent"}],
            },
            "links": [
                {
                    "link_kind": "member",
                    "link_key": "member_0",
                    "child_component_id": "faq-agent",
                    "position": 0,
                    "meta": {"type": "agent"},
                }
            ],
        },
    ],
}


@pytest.fixture
def registry(component_db):
    reg = Registry()
    reg.add_db(component_db)
    return reg


@pytest.fixture
def templates(tmp_path):
    (tmp_path / "team.support.json").write_text(json.dumps(TEAM_BUNDLE), encoding="utf-8")
    return tmp_path


class TestRowsCarryTheTargetTenant:
    def test_component_config_and_link_rows_are_all_stamped(self, component_db, templates, registry):
        from sqlalchemy import text

        seed_from_directory(component_db, templates, tenant_id=TENANT_A, registry=registry)

        with component_db.Session() as sess:
            for table in ("egpt_components", "egpt_component_configs", "egpt_component_links"):
                tenants = {row[0] for row in sess.execute(text(f"SELECT DISTINCT tenant_id FROM {table}"))}
                assert tenants == {TENANT_A}, table

    def test_the_source_tenant_in_the_bundle_is_ignored(self, component_db, templates, registry):
        """A bundle records where it came from; that must not decide where it lands."""
        bundle = json.loads(json.dumps(TEAM_BUNDLE))
        bundle["source"] = {"tenant_id": "somebody-else", "component_id": "support-team"}
        (templates / "team.support.json").write_text(json.dumps(bundle), encoding="utf-8")

        seed_from_directory(component_db, templates, tenant_id=TENANT_A, registry=registry)

        assert component_db.get_component("support-team", tenant_id=TENANT_A) is not None
        assert component_db.get_component("support-team", tenant_id="somebody-else") is None


class TestOneTenantCannotSeeAnother:
    @pytest.fixture(autouse=True)
    def seeded(self, component_db, templates, registry):
        seed_from_directory(component_db, templates, tenant_id=TENANT_A, registry=registry)

    def test_listing_is_scoped(self, component_db):
        mine, mine_total = component_db.list_components(tenant_id=TENANT_A)
        theirs, theirs_total = component_db.list_components(tenant_id=TENANT_B)

        assert {row["component_id"] for row in mine} == {"faq-agent", "support-team"}
        assert mine_total == 2
        assert theirs == [] and theirs_total == 0

    def test_export_from_the_wrong_tenant_finds_nothing(self, component_db):
        from egpt.registry.io import ComponentIOError

        with pytest.raises(ComponentIOError, match="not found"):
            export_component(component_db, "support-team", tenant_id=TENANT_B)

    def test_seeding_the_same_corpus_into_a_second_tenant_refuses(self, component_db, templates, registry):
        """The global primary key, surfaced honestly rather than worked around."""
        with pytest.raises(ComponentIdConflictError):
            seed_from_directory(component_db, templates, tenant_id=TENANT_B, registry=registry)

    def test_nothing_is_written_by_the_refusal(self, component_db, templates, registry):
        with pytest.raises(ComponentIdConflictError):
            seed_from_directory(component_db, templates, tenant_id=TENANT_B, registry=registry)

        assert component_db.list_components(tenant_id=TENANT_B) == ([], 0)

    def test_an_id_prefix_lets_both_tenants_hold_the_same_corpus(self, component_db, templates, registry):
        seed_from_directory(component_db, templates, tenant_id=TENANT_B, registry=registry, id_prefix=f"{TENANT_B}-")

        theirs, _total = component_db.list_components(tenant_id=TENANT_B)
        assert {row["component_id"] for row in theirs} == {"globex-faq-agent", "globex-support-team"}
        # And the original tenant is untouched.
        mine, _total = component_db.list_components(tenant_id=TENANT_A)
        assert {row["component_id"] for row in mine} == {"faq-agent", "support-team"}

    def test_both_copies_hydrate_independently(self, component_db, templates, registry):
        from egpt.team import get_team_by_id

        seed_from_directory(component_db, templates, tenant_id=TENANT_B, registry=registry, id_prefix=f"{TENANT_B}-")

        mine = get_team_by_id(component_db, "support-team", registry=registry, tenant_id=TENANT_A)
        theirs = get_team_by_id(component_db, "globex-support-team", registry=registry, tenant_id=TENANT_B)

        assert [m.id for m in mine.members] == ["faq-agent"]
        assert [m.id for m in theirs.members] == ["globex-faq-agent"]

    def test_a_tenant_cannot_reach_the_others_component_by_id(self, component_db, registry):
        from egpt.agent import get_agent_by_id

        assert get_agent_by_id(component_db, "faq-agent", registry=registry, tenant_id=TENANT_B) is None


class TestImportNeverWritesIntoAnotherTenant:
    @pytest.fixture(autouse=True)
    def seeded(self, component_db, templates, registry):
        seed_from_directory(component_db, templates, tenant_id=TENANT_A, registry=registry)

    def test_new_version_degrades_to_a_refusal(self, component_db, registry):
        """`new_version` is the seed default, so this is the path an operator actually hits."""
        before = component_db.list_configs("support-team", tenant_id=TENANT_A)

        with pytest.raises(ComponentIdConflictError):
            import_component(
                component_db,
                TEAM_BUNDLE,
                tenant_id=TENANT_B,
                registry=registry,
                on_conflict="new_version",
            )

        assert component_db.list_configs("support-team", tenant_id=TENANT_A) == before

    def test_skip_touches_nothing_either(self, component_db, registry):
        results = import_component(component_db, TEAM_BUNDLE, tenant_id=TENANT_B, registry=registry, on_conflict="skip")

        assert {r.action for r in results} == {"skipped"}
        assert component_db.list_components(tenant_id=TENANT_B) == ([], 0)


class TestCollisionsAreTheSameOnAStrictDb:
    """The acceptance criterion of aicoe-tracker#176, at the io layer.

    ``require_tenant_id`` must not change what a cross-tenant id collision looks like. Under the
    scoped fallback of #70 it did: the pre-pass could not see the foreign holder, so the import
    ran into the write, whose "already exists" ValueError is reserved for soft-deleted holders --
    a live collision surfaced as ``ComponentSoftDeletedError`` telling the operator to hard-delete
    a component that was never deleted. The unscoped one-bit probe restores the same
    ``ComponentIdConflictError`` the lax db raises, from the same pre-pass.
    """

    @pytest.fixture
    def strict_db(self):
        import os
        import tempfile

        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = SqliteDb(db_file=path, id="strict-db", require_tenant_id=True)
        yield db
        try:
            os.unlink(path)
        except OSError:
            pass

    @pytest.fixture
    def strict_registry(self, strict_db):
        reg = Registry()
        reg.add_db(strict_db)
        return reg

    @pytest.fixture(autouse=True)
    def seeded(self, strict_db, templates, strict_registry):
        seed_from_directory(strict_db, templates, tenant_id=TENANT_A, registry=strict_registry)

    def test_seeding_into_a_second_tenant_raises_the_domain_error(self, strict_db, templates, strict_registry):
        with pytest.raises(ComponentIdConflictError):
            seed_from_directory(strict_db, templates, tenant_id=TENANT_B, registry=strict_registry)

    def test_the_error_matches_the_lax_dbs(self, strict_db, component_db, templates, registry, strict_registry):
        """One probe strategy means one failure mode, not two flavours of the same refusal."""
        seed_from_directory(component_db, templates, tenant_id=TENANT_A, registry=registry)

        with pytest.raises(ComponentIdConflictError) as on_strict:
            seed_from_directory(strict_db, templates, tenant_id=TENANT_B, registry=strict_registry)
        with pytest.raises(ComponentIdConflictError) as on_lax:
            seed_from_directory(component_db, templates, tenant_id=TENANT_B, registry=registry)

        assert str(on_strict.value) == str(on_lax.value)

    def test_nothing_is_written_by_the_refusal(self, strict_db, templates, strict_registry):
        with pytest.raises(ComponentIdConflictError):
            seed_from_directory(strict_db, templates, tenant_id=TENANT_B, registry=strict_registry)

        assert strict_db.list_components(tenant_id=TENANT_B) == ([], 0)

    def test_reseeding_the_owning_tenant_is_still_a_no_op(self, strict_db, templates, strict_registry):
        results = seed_from_directory(strict_db, templates, tenant_id=TENANT_A, registry=strict_registry)

        assert {r.action for r in results} == {"unchanged"}
