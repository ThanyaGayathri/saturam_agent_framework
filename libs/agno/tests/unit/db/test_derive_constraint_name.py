"""Regression coverage for ``derive_constraint_name`` (aicoe-tracker#162).

The ``component_links`` foreign key to ``component_configs`` names itself
``{table}_parent_component_id_parent_version_fkey``. Index names were already bounded to
Postgres's 63-character identifier limit via ``derive_index_name``; constraint names were not,
so a long ``table_prefix`` (legal up to ``TABLE_PREFIX_MAX_LENGTH``) pushed this one past the
limit and Postgres rejected the ``CREATE TABLE`` outright instead of truncating silently.
"""

from egpt.db.table_names import (
    MAX_IDENTIFIER_LENGTH,
    TABLE_PREFIX_MAX_LENGTH,
    derive_constraint_name,
)

# The columns component_links' FK to component_configs is defined over -- see
# COMPONENT_LINKS_TABLE_SCHEMA["__foreign_keys__"] in egpt.integrations.db.postgres.schemas.
_COMPONENT_LINKS_FK_COLUMNS = ["parent_component_id", "parent_version"]


def test_the_default_prefix_keeps_the_readable_name():
    """Bounding must not rename anything at a normal prefix, or an upgrade would rebuild every
    constraint it touches."""
    assert (
        derive_constraint_name("egpt_component_links", _COMPONENT_LINKS_FK_COLUMNS, "fkey")
        == "egpt_component_links_parent_component_id_parent_version_fkey"
    )


def test_component_links_fk_name_stays_within_the_identifier_limit_at_any_legal_prefix():
    """The exact case aicoe-tracker#162 reports: at the longest legal ``table_prefix``, the
    component_links -> component_configs FK constraint name must fit under Postgres's 63-byte
    limit rather than causing CREATE TABLE to fail."""
    longest_prefix = "a" * TABLE_PREFIX_MAX_LENGTH
    table_name = f"{longest_prefix}component_links"

    name = derive_constraint_name(table_name, _COMPONENT_LINKS_FK_COLUMNS, "fkey")

    assert len(name) <= MAX_IDENTIFIER_LENGTH, name


def test_two_over_long_names_stay_distinct():
    """Truncation alone would collide these into one constraint; the hash is what keeps them
    apart."""
    table = "a" * TABLE_PREFIX_MAX_LENGTH + "component_links"

    first = derive_constraint_name(table, ["parent_component_id", "parent_version"], "fkey")
    second = derive_constraint_name(table, ["child_component_id", "child_version"], "fkey")

    assert first != second
    assert first == derive_constraint_name(table, ["parent_component_id", "parent_version"], "fkey"), "must be stable"
