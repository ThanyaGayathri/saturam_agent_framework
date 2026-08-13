"""Conformance properties for the SM-R12 memory tier schemas (SM-C3 #91).

Every invariant here is one shared implementation in ``egpt.db.schemas.memory`` and one
property parametrised over **every** tier that carries it. An invariant asserted against a
single tier is how the same rule ends up holding on one model and not its neighbour, which is
the drift these tables exist to prevent.
"""

import dataclasses
import importlib
from datetime import datetime, timezone

import pytest

from egpt.db.schemas.memory import (
    MEMORY_TIER_VALUES,
    PROCEDURAL_KINDS,
    BiTemporal,
    Decaying,
    Embeddable,
    EpisodicRecord,
    MemoryLink,
    ProceduralRecord,
    SemanticEntity,
    SemanticFact,
    SemanticRelation,
    TenantScoped,
    UserMemory,
)
from egpt.db.table_names import MEMORY_TIER_TABLE_TYPES, TABLE_SUFFIXES, memory_tier_table_attr

# Every persisted tier, with the table type its rows live in and the minimum kwargs that make
# one valid. The single source of truth for the parametrised properties below.
TIERS = {
    "episodic": (EpisodicRecord, "memory_episodic", {"content": "the user shipped v2"}),
    "semantic_fact": (SemanticFact, "memory_semantic_fact", {"content": "acme uses postgres"}),
    "entity": (SemanticEntity, "memory_entity", {"entity_type": "company"}),
    "relation": (
        SemanticRelation,
        "memory_relation",
        {"subject_entity_id": "e1", "predicate": "works_at", "object_entity_id": "e2"},
    ),
    "procedural": (ProceduralRecord, "memory_procedural", {"content": "always cite the source"}),
    "link": (
        MemoryLink,
        "memory_link",
        {"from_id": "a", "from_tier": "episodic", "to_id": "b", "to_tier": "semantic"},
    ),
}

ALL_TIERS = sorted(TIERS)
BI_TEMPORAL_TIERS = sorted(k for k, (model, _, _) in TIERS.items() if issubclass(model, BiTemporal))
EMBEDDABLE_TIERS = sorted(k for k, (model, _, _) in TIERS.items() if issubclass(model, Embeddable))
DECAYING_TIERS = sorted(k for k, (model, _, _) in TIERS.items() if issubclass(model, Decaying))


def build(tier, **overrides):
    model, _, required = TIERS[tier]
    return model(tenant_id="acme", **{**required, **overrides})


def field_names(tier):
    return {f.name for f in dataclasses.fields(TIERS[tier][0])}


# --------------------------------------------------------------------------- #
# The presence matrix -- SM-R12 section 4.4, asserted rather than documented
# --------------------------------------------------------------------------- #


class TestFieldPresenceByTier:
    """SM-R12 section 4.4. A tier gaining or losing a mixin is a schema change, so it fails here
    before it reaches a migration."""

    def test_bi_temporal_tiers_are_exactly_the_spec_s(self):
        assert BI_TEMPORAL_TIERS == ["episodic", "link", "relation", "semantic_fact"]

    def test_embeddable_tiers_are_exactly_the_spec_s(self):
        assert EMBEDDABLE_TIERS == ["entity", "episodic", "procedural", "semantic_fact"]

    def test_decaying_tiers_are_exactly_the_spec_s(self):
        assert DECAYING_TIERS == ["episodic", "semantic_fact"]

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_every_tier_is_tenant_scoped(self, tier):
        assert issubclass(TIERS[tier][0], TenantScoped)

    @pytest.mark.parametrize("tier", DECAYING_TIERS)
    def test_strength_is_absent_by_decision(self, tier):
        """SM-R5 section 5 defers ``strength`` ("do NOT add now") and ADR 0003 section 6
        ratifies that: ``last_accessed_at`` + ``importance`` capture the behaviour with one
        fewer stateful field. Issue #91's file table still asks for it; this pins the ADR.
        """
        assert "strength" not in field_names(tier)


# --------------------------------------------------------------------------- #
# Boundary guards -- one helper, every tier that carries the field
# --------------------------------------------------------------------------- #


class TestTenantGuard:
    @pytest.mark.parametrize("tier", ALL_TIERS)
    @pytest.mark.parametrize("blank", ["", None])
    def test_a_blank_tenant_is_refused(self, tier, blank):
        model, _, required = TIERS[tier]
        with pytest.raises(ValueError, match="tenant_id is required"):
            model(tenant_id=blank, **required)

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_a_blank_id_is_refused(self, tier):
        with pytest.raises(ValueError, match="id is required"):
            build(tier, id="")

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_id_defaults_to_a_fresh_value_per_row(self, tier):
        assert build(tier).id != build(tier).id


class TestTemporalGuard:
    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_an_inverted_valid_window_is_refused(self, tier):
        with pytest.raises(ValueError, match="t_invalid"):
            build(tier, t_valid=200, t_invalid=100)

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_expiry_before_the_row_existed_is_refused(self, tier):
        with pytest.raises(ValueError, match="expired_at"):
            build(tier, created_at=200, expired_at=100)

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_open_ended_windows_are_allowed(self, tier):
        """NULL bounds mean "still true" / "still held" -- the common case, not an error."""
        row = build(tier, t_valid=100)
        assert row.is_valid_at(500) and row.is_current()

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_point_in_time_is_half_open(self, tier):
        row = build(tier, t_valid=100, t_invalid=200)

        assert not row.is_valid_at(99)
        assert row.is_valid_at(100)  # inclusive lower bound
        assert row.is_valid_at(199)
        assert not row.is_valid_at(200)  # exclusive upper bound


class TestExpire:
    """SCD-2: closing a row must not rewrite when the fact became true, and must not let a
    replayed correction overwrite the original bound."""

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_expire_preserves_t_valid(self, tier):
        row = build(tier, created_at=100, t_valid=150)

        assert row.expire(expired_at=300, t_invalid=250)
        assert row.t_valid == 150
        assert (row.expired_at, row.t_invalid) == (300, 250)

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_re_expire_is_a_guarded_no_op(self, tier):
        row = build(tier, created_at=100)
        row.expire(expired_at=300, t_invalid=250)

        assert row.expire(expired_at=999, t_invalid=999) is False
        assert (row.expired_at, row.t_invalid) == (300, 250), "a replay must not clobber the first bound"

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_expire_refuses_to_write_an_inverted_window(self, tier):
        row = build(tier, created_at=100, t_valid=200)

        with pytest.raises(ValueError, match="t_invalid"):
            row.expire(expired_at=300, t_invalid=150)

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_an_expired_row_is_not_current(self, tier):
        row = build(tier)
        assert row.is_current()

        row.expire()

        assert not row.is_current()

    @pytest.mark.parametrize("tier", BI_TEMPORAL_TIERS)
    def test_a_superseded_row_is_not_current(self, tier):
        """Episodic carries a list (an SM-R6 re-cut can merge or split); the others a single
        id. One ``is_current`` covers both shapes."""
        superseded_by = ["new-1", "new-2"] if tier == "episodic" else "new-1"

        assert not build(tier, superseded_by=superseded_by).is_current()


class TestEmbeddingGuard:
    @pytest.mark.parametrize("tier", EMBEDDABLE_TIERS)
    def test_a_dim_that_contradicts_the_vector_is_refused(self, tier):
        with pytest.raises(ValueError, match="embedding_dim"):
            build(tier, embedding=[0.1, 0.2], embedding_dim=768)

    @pytest.mark.parametrize("tier", EMBEDDABLE_TIERS)
    def test_dim_is_derived_when_absent(self, tier):
        assert build(tier, embedding=[0.1, 0.2, 0.3]).embedding_dim == 3

    @pytest.mark.parametrize("tier", EMBEDDABLE_TIERS)
    def test_no_embedding_leaves_dim_alone(self, tier):
        assert build(tier).embedding_dim is None


class TestUnitIntervalGuards:
    @pytest.mark.parametrize("tier", DECAYING_TIERS)
    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_importance_outside_the_unit_interval_is_refused(self, tier, bad):
        with pytest.raises(ValueError, match="importance"):
            build(tier, importance=bad)

    @pytest.mark.parametrize("tier", DECAYING_TIERS)
    def test_importance_defaults_to_the_ratified_midpoint(self, tier):
        assert build(tier).importance == 0.5

    @pytest.mark.parametrize("tier", ["semantic_fact", "relation"])
    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_confidence_outside_the_unit_interval_is_refused(self, tier, bad):
        with pytest.raises(ValueError, match="confidence"):
            build(tier, confidence=bad)


class TestClosedVocabularies:
    @pytest.mark.parametrize("endpoint", ["from_tier", "to_tier"])
    def test_an_unknown_tier_is_refused_on_both_endpoints(self, endpoint):
        with pytest.raises(ValueError, match=endpoint):
            build("link", **{endpoint: "epsiodic"})

    @pytest.mark.parametrize("tier_value", sorted(MEMORY_TIER_VALUES))
    def test_every_canonical_tier_is_accepted(self, tier_value):
        assert build("link", from_tier=tier_value).from_tier == tier_value

    def test_the_tier_vocabulary_tracks_the_memory_package(self):
        """``egpt.db.schemas`` deliberately does not import ``egpt.memory.tiers`` at runtime --
        this is the db layer. The vocabularies still have to agree, so the coupling is a test
        rather than an import: a tier renamed or added in SM-C2's file fails here."""
        from egpt.memory.tiers import MemoryTier

        assert MEMORY_TIER_VALUES == {tier.value for tier in MemoryTier}

    def test_an_unknown_procedural_kind_is_refused(self):
        with pytest.raises(ValueError, match="kind"):
            build("procedural", kind="heuristic")

    @pytest.mark.parametrize("kind", sorted(PROCEDURAL_KINDS))
    def test_every_ratified_kind_is_accepted(self, kind):
        assert build("procedural", kind=kind).kind == kind


class TestEpochNormalisation:
    """One loop normalises every time field on every tier, so a new time column cannot be
    added without it."""

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_every_time_field_accepts_iso_and_datetime(self, tier):
        time_fields = [f for f in field_names(tier) if f.endswith("_at") or f in ("t_valid", "t_invalid")]
        assert time_fields, "a tier with no time field would make this vacuous"

        for name in time_fields:
            # created_at is pinned below the probe value because expired_at is guarded against
            # preceding it -- the guard is the point, so the probe works around it rather than
            # relaxing it.
            base = {"created_at": 1_000_000_000} if name != "created_at" else {}
            iso = build(tier, **base, **{name: "2026-01-02T03:04:05Z"})
            aware = build(tier, **base, **{name: datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)})

            assert getattr(iso, name) == 1767323045, name
            assert getattr(aware, name) == 1767323045, name

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_created_at_is_stamped_when_absent(self, tier):
        assert build(tier).created_at is not None


class TestSerialisation:
    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_round_trip_is_lossless(self, tier):
        row = build(tier, user_id="ada", metadata={"k": "v"})

        assert TIERS[tier][0].from_dict(row.to_dict()).to_dict() == row.to_dict()

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_to_dict_covers_every_field_and_preserves_none(self, tier):
        row = build(tier)

        assert set(row.to_dict()) == field_names(tier)
        assert row.to_dict()["updated_at"] is None, "a dropped None cannot clear a column on update"

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_from_dict_ignores_columns_the_tier_does_not_have(self, tier):
        """A read from a wider row (or a future column) must not explode the model."""
        row = TIERS[tier][0].from_dict({**build(tier).to_dict(), "no_such_column": 1})

        assert row.tenant_id == "acme"

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_mutable_defaults_are_not_shared_between_rows(self, tier):
        first, second = build(tier), build(tier)
        first.metadata["k"] = "v"

        assert second.metadata == {}

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_collections_are_copied_not_aliased(self, tier):
        """A row that aliases the caller's list lets a later mutation rewrite persisted state."""
        supplied = {"k": "v"}
        row = build(tier, metadata=supplied)
        supplied["k"] = "mutated"

        assert row.metadata == {"k": "v"}


# --------------------------------------------------------------------------- #
# Model <-> physical schema parity
# --------------------------------------------------------------------------- #

sqlite_schemas = pytest.importorskip(
    "egpt.integrations.db.sqlite.schemas", reason="sqlalchemy is required for the table schemas"
)
postgres_schemas = pytest.importorskip(
    "egpt.integrations.db.postgres.schemas", reason="sqlalchemy is required for the table schemas"
)


def columns_of(schema):
    return {name for name in schema if not name.startswith("__") and not name.startswith("_")}


class TestPhysicalSchemaParity:
    @pytest.mark.parametrize("tier", ALL_TIERS)
    @pytest.mark.parametrize("backend", ["sqlite", "postgres"])
    def test_columns_match_the_model_field_for_field(self, tier, backend):
        module = sqlite_schemas if backend == "sqlite" else postgres_schemas
        schema = module.get_table_schema_definition(TIERS[tier][1])

        assert columns_of(schema) == field_names(tier)

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_the_two_backends_agree_on_columns_and_nullability(self, tier):
        table_type = TIERS[tier][1]
        sqlite_schema = sqlite_schemas.get_table_schema_definition(table_type)
        postgres_schema = postgres_schemas.get_table_schema_definition(table_type)

        assert columns_of(sqlite_schema) == columns_of(postgres_schema)
        for column in columns_of(sqlite_schema):
            assert sqlite_schema[column].get("nullable") == postgres_schema[column].get("nullable"), column

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_the_two_backends_agree_on_the_primary_key_and_indexes(self, tier):
        table_type = TIERS[tier][1]
        sqlite_schema = sqlite_schemas.get_table_schema_definition(table_type)
        postgres_schema = postgres_schemas.get_table_schema_definition(table_type)

        assert sqlite_schema["__primary_key__"] == ["tenant_id", "id"] == postgres_schema["__primary_key__"]
        assert sqlite_schema.get("__composite_indexes__") == postgres_schema.get("__composite_indexes__")

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_tenant_id_leads_every_composite_index(self, tier):
        """There is no LIST partitioning here, so a tenant-blind index would scan across
        tenants. Leading with tenant_id is what keeps the non-partitioned layout equivalent."""
        schema = sqlite_schemas.get_table_schema_definition(TIERS[tier][1])

        for index in schema.get("__composite_indexes__", []):
            assert index["columns"][0] == "tenant_id", index

    @pytest.mark.parametrize("backend", ["sqlite", "postgres"])
    def test_a_backend_refuses_an_unknown_tier_rather_than_returning_empty(self, backend):
        module = sqlite_schemas if backend == "sqlite" else postgres_schemas

        with pytest.raises(ValueError, match="Unknown table type"):
            module.get_table_schema_definition("memory_procedrual")


class TestSeedMemoryTable:
    @pytest.mark.parametrize("backend", ["sqlite", "postgres"])
    def test_the_memories_table_carries_tenant_id(self, backend):
        module = sqlite_schemas if backend == "sqlite" else postgres_schemas
        schema = module.get_table_schema_definition("memories")

        assert schema["tenant_id"]["nullable"] is True, "a NOT NULL column could not be added reversibly"
        assert schema["tenant_id"]["index"] is True

    def test_user_memory_carries_tenant_id_and_drops_it_when_unset(self):
        assert "tenant_id" not in UserMemory(memory="m").to_dict()
        assert UserMemory(memory="m", tenant_id="acme").to_dict()["tenant_id"] == "acme"
        assert UserMemory.from_dict({"memory": "m", "tenant_id": "acme"}).tenant_id == "acme"

    @pytest.mark.parametrize("backend_module", ["mysql", "singlestore", "dynamo"])
    def test_backends_without_a_migration_do_not_get_the_column(self, backend_module):
        """The column exists exactly where a migration can add it. Adding it to a backend this
        version cannot migrate would give fresh installs a column that existing deployments can
        never get -- a two-state world with no upgrade path. aicoe-tracker#175 owns extending
        or refusing these backends when it wires the read/write path.
        """
        # Not importorskip: these schema modules live in a package whose ``__init__`` imports
        # the backend, so the ImportError comes from the driver rather than the named module.
        try:
            module = importlib.import_module(f"egpt.integrations.db.{backend_module}.schemas")
        except ImportError as e:
            pytest.skip(f"optional driver for '{backend_module}' not installed: {e}")
        schema = next(
            value
            for name, value in vars(module).items()
            if name.endswith("MEMORY_TABLE_SCHEMA") and isinstance(value, dict)
        )

        assert "tenant_id" not in schema


class TestTableNaming:
    def test_the_tier_tables_derive_from_the_table_prefix(self):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        db = SqliteDb(db_file=":memory:", table_prefix="acme_")

        for table_type in MEMORY_TIER_TABLE_TYPES:
            assert getattr(db, memory_tier_table_attr(table_type)) == f"acme_{table_type}"

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    def test_the_tier_tables_are_deliberately_not_individually_overridable(self, table_type):
        """Pins the split rather than leaving it to look like an omission: per-table overrides
        exist to be forwarded through ``clone_with_table_overrides`` by a consumer, and the
        memory read/write path is SM-V1 (#95) / SM-C2 (#90). This test fails when someone
        promotes them, which is the moment the six constructor parameters are also needed.
        """
        assert table_type not in TABLE_SUFFIXES

    def test_the_migration_runner_can_resolve_every_tier_table(self):
        """Without an entry here a tier table is simply unreachable by the runner, so its
        migration would never run -- the state the component tables were once in."""
        from egpt.db.migrations.manager import MigrationManager

        for table_type in MEMORY_TIER_TABLE_TYPES:
            assert MigrationManager._TABLE_TYPE_TO_ATTR[table_type] == memory_tier_table_attr(table_type)
