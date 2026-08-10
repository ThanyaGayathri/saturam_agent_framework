"""Service-account tenancy and the v2_7_0 migration, against a real Postgres (EGPT-2.4).

Two things only a real database can show:

- the `tenant_id` predicate on `get_service_account*` is a SQL predicate, so a foreign account
  is genuinely unreachable rather than filtered by a mock;
- the migration adds and removes the column and its index without touching the credential,
  scope or audit data on rows that already exist.

Gated on ``EGPT_INTEGRATION_POSTGRES``::

    ./cookbook/scripts/run_pgvector.sh
    EGPT_INTEGRATION_POSTGRES=1 pytest libs/agno/tests/integration/os/tenancy_postgres -v
"""

import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from egpt.db.migrations.manager import MigrationManager
from egpt.db.schemas.service_accounts import ServiceAccount

TENANT_A = "acme"
TENANT_B = "globex"


@pytest.fixture
def sa_db(postgres_engine):
    from egpt.integrations.db.postgres import PostgresDb  # noqa: PLC0415

    suffix = uuid4().hex[:8]
    return PostgresDb(
        db_engine=postgres_engine,
        db_schema="test_schema",
        session_table=f"sa_sessions_{suffix}",
        memory_table=f"sa_memories_{suffix}",
        metrics_table=f"sa_metrics_{suffix}",
        eval_table=f"sa_evals_{suffix}",
        knowledge_table=f"sa_knowledge_{suffix}",
    )


def mint(db, name: str, tenant):
    account = ServiceAccount(
        id=str(uuid4()),
        name=name,
        token_hash=uuid4().hex * 2,
        token_prefix="agno_pat_" + uuid4().hex[:8],
        scopes=["components:read"],
        created_at=int(time.time()),
        tenant_id=tenant,
    )
    db.create_service_account(account.to_dict())
    return account


class TestTenantColumnIsPersisted:
    def test_the_tenant_survives_a_round_trip(self, sa_db):
        account = mint(sa_db, "ci-acme", TENANT_A)
        assert sa_db.get_service_account(account.id)["tenant_id"] == TENANT_A

    def test_it_survives_the_dataclass_round_trip(self, sa_db):
        account = mint(sa_db, "ci-acme", TENANT_A)
        row = sa_db.get_service_account_by_token_hash(account.token_hash)
        assert ServiceAccount.from_dict(row).tenant_id == TENANT_A

    def test_an_unscoped_account_stores_null(self, sa_db):
        account = mint(sa_db, "platform-bot", None)
        assert sa_db.get_service_account(account.id)["tenant_id"] is None


class TestReadsAreTenantScoped:
    def test_the_owner_can_read_it(self, sa_db):
        account = mint(sa_db, "ci-acme", TENANT_A)
        assert sa_db.get_service_account(account.id, tenant_id=TENANT_A) is not None

    def test_another_tenant_cannot(self, sa_db):
        account = mint(sa_db, "ci-acme", TENANT_A)
        assert sa_db.get_service_account(account.id, tenant_id=TENANT_B) is None

    def test_an_unscoped_read_sees_everything(self, sa_db):
        account = mint(sa_db, "ci-acme", TENANT_A)
        assert sa_db.get_service_account(account.id, tenant_id=None) is not None

    def test_listings_do_not_cross(self, sa_db):
        mint(sa_db, "ci-acme", TENANT_A)
        mint(sa_db, "ci-globex", TENANT_B)

        rows_a, count_a = sa_db.get_service_accounts(tenant_id=TENANT_A)
        rows_b, count_b = sa_db.get_service_accounts(tenant_id=TENANT_B)

        assert [r["name"] for r in rows_a] == ["ci-acme"]
        assert [r["name"] for r in rows_b] == ["ci-globex"]
        # The count is filtered too, not just the page.
        assert count_a == count_b == 1

    def test_an_unscoped_listing_sees_both(self, sa_db):
        mint(sa_db, "ci-acme", TENANT_A)
        mint(sa_db, "ci-globex", TENANT_B)
        rows, count = sa_db.get_service_accounts()
        assert count == 2
        assert {r["name"] for r in rows} == {"ci-acme", "ci-globex"}


class TestMigrationV2_7_0:
    """The column and index are added and removed cleanly, and existing rows are untouched.

    Note the deploy-order hazard this migration carries: `_get_table` rejects a live table
    missing a column the schema declares, and `ServiceAccountVerifier` turns that into
    UNAVAILABLE -- so shipping the code before running the migration fails every PAT.
    """

    def _table(self, sa_db) -> str:
        return sa_db.service_accounts_table_name

    def _columns(self, sa_db):
        with sa_db.Session() as sess:
            rows = sess.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": "test_schema", "table": self._table(sa_db)},
            ).fetchall()
        return {r[0] for r in rows}

    def _indexes(self, sa_db):
        with sa_db.Session() as sess:
            rows = sess.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema AND tablename = :table"),
                {"schema": "test_schema", "table": self._table(sa_db)},
            ).fetchall()
        return {r[0] for r in rows}

    def test_the_service_accounts_table_is_reachable_by_the_runner(self):
        """Without this entry a v2_7_0 targeting the table would silently never run."""
        assert MigrationManager._TABLE_TYPE_TO_ATTR["service_accounts"] == "service_accounts_table_name"

    def test_a_freshly_created_table_already_has_the_column(self, sa_db):
        mint(sa_db, "ci-acme", TENANT_A)
        assert "tenant_id" in self._columns(sa_db)

    def test_the_index_is_created(self, sa_db):
        mint(sa_db, "ci-acme", TENANT_A)
        assert f"idx_{self._table(sa_db)}_tenant_id" in self._indexes(sa_db)

    async def test_down_then_up_restores_the_schema_and_keeps_the_rows(self, sa_db):
        account = mint(sa_db, "ci-acme", TENANT_A)
        table = self._table(sa_db)
        index = f"idx_{table}_tenant_id"

        from egpt.db.migrations.versions import v2_7_0  # noqa: PLC0415

        assert v2_7_0.down(sa_db, "service_accounts", table) is True
        assert "tenant_id" not in self._columns(sa_db)
        assert index not in self._indexes(sa_db)

        # The credential and its audit trail survived the revert.
        with sa_db.Session() as sess:
            row = sess.execute(
                text(f'SELECT name, token_hash, scopes FROM test_schema."{table}" WHERE id = :id'),
                {"id": account.id},
            ).fetchone()
        assert row[0] == "ci-acme"
        assert row[1] == account.token_hash

        assert v2_7_0.up(sa_db, "service_accounts", table) is True
        assert "tenant_id" in self._columns(sa_db)
        assert index in self._indexes(sa_db)

        # The row is still there; its tenant is NULL, which is the documented meaning of a
        # pre-migration row -- unscoped.
        assert sa_db.get_service_account(account.id)["tenant_id"] is None

    def test_up_is_idempotent(self, sa_db):
        mint(sa_db, "ci-acme", TENANT_A)
        from egpt.db.migrations.versions import v2_7_0  # noqa: PLC0415

        assert v2_7_0.up(sa_db, "service_accounts", self._table(sa_db)) is False

    def test_it_ignores_other_table_types(self, sa_db):
        from egpt.db.migrations.versions import v2_7_0  # noqa: PLC0415

        assert v2_7_0.up(sa_db, "sessions", sa_db.session_table_name) is False
        assert v2_7_0.down(sa_db, "sessions", sa_db.session_table_name) is False
