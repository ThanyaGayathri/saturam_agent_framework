"""Regression test for aicoe-tracker#162: component_links FK constraint name overflow.

The ``component_links`` -> ``component_configs`` foreign key names itself
``{table_prefix}component_links_parent_component_id_parent_version_fkey``. At a long
``table_prefix`` (legal up to ``TABLE_PREFIX_MAX_LENGTH``) that exceeded Postgres's 63-character
identifier limit and ``CREATE TABLE`` failed outright -- index names were already bounded for
exactly this reason (``derive_index_name``), but FK constraint names were not.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from egpt.db.table_names import TABLE_PREFIX_MAX_LENGTH
from egpt.integrations.db.postgres.postgres import PostgresDb


def test_component_tables_create_with_a_long_prefix_and_the_fk_is_enforced(postgres_engine):
    """Pins the fix at the exact case the issue reports: table creation must succeed at the
    longest legal prefix, and the resulting FK constraint must actually be enforced -- a link
    row pointing at a non-existent parent config must be rejected by Postgres, not just by the
    Python layer."""
    longest_prefix = "a" * TABLE_PREFIX_MAX_LENGTH

    db = PostgresDb(db_engine=postgres_engine, db_schema="test_schema", table_prefix=longest_prefix)

    # Only the component tables, in FK dependency order -- not the full ``_create_all_tables``
    # sweep, which also provisions the SM-R12 memory tiers and would conflate this regression
    # with unrelated tables.
    db._create_table(db.components_table_name, "components")
    db._create_table(db.component_configs_table_name, "component_configs")
    db._create_table(db.component_links_table_name, "component_links")

    with db.Session() as sess:
        table_exists = sess.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table"),
            {"schema": "test_schema", "table": db.component_links_table_name},
        ).scalar()
        assert table_exists is not None, "component_links table was not created"

        fk_row = sess.execute(
            text(
                "SELECT con.conname FROM pg_constraint con "
                "JOIN pg_class cls ON cls.oid = con.conrelid "
                "JOIN pg_namespace ns ON ns.oid = cls.relnamespace "
                "WHERE con.contype = 'f' AND ns.nspname = :schema AND cls.relname = :table"
            ),
            {"schema": "test_schema", "table": db.component_links_table_name},
        ).fetchone()
        assert fk_row is not None, "no FK constraint was created on component_links"
        assert len(fk_row[0]) <= 63, fk_row[0]

    # The FK must actually be enforced under its derived name, not merely present: insert a link
    # row whose parent does not exist in component_configs and confirm Postgres rejects it.
    insert_orphan_link = text(
        f'INSERT INTO test_schema."{db.component_links_table_name}" '
        "(parent_component_id, parent_version, link_kind, link_key, child_component_id, position) "
        "VALUES ('missing-parent', 1, 'tool', 'k1', 'child-1', 0)"
    )
    with db.Session() as sess, pytest.raises(IntegrityError):
        sess.execute(insert_orphan_link)
        sess.commit()
