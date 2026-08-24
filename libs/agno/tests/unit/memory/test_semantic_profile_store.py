"""The structured user-profile store (SM-S3 #120)."""

from dataclasses import dataclass, field
from typing import Optional

from egpt.learn.schemas import UserProfile
from egpt.memory.semantic import SemanticProfileStore, profile_record_id
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryScopeRequiredError, Scope
from tests.unit.memory._fakes import FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")
TENANT_WIDE = Scope(tenant_id="acme")


def store():
    vector = FakeVector()
    return SemanticProfileStore(vector=vector), vector


# -- profile_record_id ---------------------------------------------------------


def test_a_user_scope_gets_a_deterministic_id():
    assert profile_record_id(ACME) == profile_record_id(ACME)


def test_two_users_get_different_ids():
    assert profile_record_id(ACME) != profile_record_id(ACME_BOB)


def test_two_tenants_get_different_ids_for_the_same_user_id():
    assert profile_record_id(ACME) != profile_record_id(GLOBEX)


def test_a_tenant_only_scope_is_refused():
    try:
        profile_record_id(TENANT_WIDE)
        raise AssertionError("expected MemoryOperationError")
    except MemoryOperationError as exc:
        assert exc.field_name == "user_id"


# -- SemanticProfileStore.write / get -------------------------------------------


def test_write_then_get_roundtrips():
    s, vector = store()
    profile = UserProfile(user_id="ada", name="Ada Lovelace", preferred_name="Ada")

    receipt = s.write(ACME, profile)

    assert receipt.accepted == 1
    assert receipt.tiers_written == [MemoryTier.SEMANTIC]
    assert "upsert" in vector.calls

    got = s.get(ACME)
    assert got is not None
    assert got.user_id == "ada"
    assert got.name == "Ada Lovelace"
    assert got.preferred_name == "Ada"


async def test_awrite_then_aget_roundtrips():
    s, vector = store()
    profile = UserProfile(user_id="ada", name="Ada Lovelace")

    receipt = await s.awrite(ACME, profile)
    assert receipt.accepted == 1

    got = await s.aget(ACME)
    assert got is not None
    assert got.name == "Ada Lovelace"


def test_get_before_any_write_is_none():
    s, _vector = store()
    assert s.get(ACME) is None


def test_a_second_write_replaces_rather_than_duplicates():
    """Upsert-by-id: two writes for the same user leave exactly one row."""
    s, vector = store()
    s.write(ACME, UserProfile(user_id="ada", name="Ada"))
    s.write(ACME, UserProfile(user_id="ada", name="Ada Lovelace"))

    assert len(vector.rows["acme"]) == 1
    got = s.get(ACME)
    assert got.name == "Ada Lovelace"


def test_cross_user_isolation():
    """Bob's read never sees Ada's profile, even though both are in the same tenant bucket."""
    s, _vector = store()
    s.write(ACME, UserProfile(user_id="ada", name="Ada"))
    s.write(ACME_BOB, UserProfile(user_id="bob", name="Bob"))

    ada = s.get(ACME)
    bob = s.get(ACME_BOB)
    assert ada.name == "Ada"
    assert bob.name == "Bob"


def test_cross_tenant_isolation():
    s, vector = store()
    s.write(ACME, UserProfile(user_id="ada", name="Acme Ada"))
    s.write(GLOBEX, UserProfile(user_id="ada", name="Globex Ada"))

    assert s.get(ACME).name == "Acme Ada"
    assert s.get(GLOBEX).name == "Globex Ada"
    # Two distinct buckets, distinct from the id collision this store must avoid.
    assert vector.rows["acme"].keys() != vector.rows["globex"].keys() or len(vector.rows) == 2


def test_an_unscoped_call_is_refused():
    s, _vector = store()
    try:
        s.write(None, UserProfile(user_id="ada"))  # type: ignore[arg-type]
        raise AssertionError("expected MemoryScopeRequiredError")
    except MemoryScopeRequiredError:
        pass


def test_a_tenant_only_scope_write_is_refused_before_touching_the_substrate():
    s, vector = store()
    try:
        s.write(TENANT_WIDE, UserProfile(user_id="ada"))
        raise AssertionError("expected MemoryOperationError")
    except MemoryOperationError:
        pass
    assert vector.calls == []


def test_an_unwired_substrate_is_a_no_op_not_a_raise():
    s = SemanticProfileStore(vector=None)
    assert s.write(ACME, UserProfile(user_id="ada")) is None
    assert s.get(ACME) is None


async def test_an_unwired_substrate_is_a_no_op_async():
    s = SemanticProfileStore(vector=None)
    assert await s.awrite(ACME, UserProfile(user_id="ada")) is None
    assert await s.aget(ACME) is None


def test_the_record_is_marked_so_a_fact_scan_cannot_be_mistaken_for_a_profile():
    """SEMANTIC-tier facts (SM-S1 #118) and a profile share a tier and a substrate; only the
    `record_kind` marker (and the deterministic id) tells `get` which row is the profile."""
    s, vector = store()
    s.write(ACME, UserProfile(user_id="ada", name="Ada"))
    stored = vector.rows["acme"][profile_record_id(ACME)]
    assert stored.metadata["record_kind"] == "user_profile"
    assert stored.tier is MemoryTier.SEMANTIC


# -- extending the schema (documented usage) ------------------------------------


@dataclass
class TeamUserProfile(UserProfile):
    company: Optional[str] = field(default=None, metadata={"description": "Company"})


def test_an_extended_schema_roundtrips_its_own_fields():
    s = SemanticProfileStore(vector=FakeVector(), schema=TeamUserProfile)
    s.write(ACME, TeamUserProfile(user_id="ada", name="Ada", company="Acme"))
    got = s.get(ACME)
    assert got.company == "Acme"
