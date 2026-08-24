"""The structured user-profile store (SM-S3 #120)."""

from dataclasses import dataclass, field
from typing import Optional

from egpt.learn.schemas import UserProfile
from egpt.memory.semantic import SemanticProfileStore, profile_record_id
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryRecord, MemoryScopeRequiredError, Scope
from tests.unit.memory._fakes import FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")
TENANT_WIDE = Scope(tenant_id="acme")
ACME_BOT_A = Scope(tenant_id="acme", user_id="ada", agent_id="bot-a")
ACME_BOT_B = Scope(tenant_id="acme", user_id="ada", agent_id="bot-b")


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
        raise AssertionError("expected MemoryScopeRequiredError")
    except MemoryScopeRequiredError as exc:
        assert exc.field_name == "user_id"


def test_a_separator_character_inside_an_id_does_not_collide():
    """A tenant_id/user_id pair containing this store's own field separator must not collide
    with a differently-split pair that joins to the same string -- the exact bug QC round 1
    found: `profile_record_id` originally joined with `:`, unescaped, so ("ab", "cd:ef") and
    ("ab:cd", "ef") both produced ``user_profile:ab:cd:ef`` (PR #135)."""
    a = Scope(tenant_id="ab", user_id="cd:ef")
    b = Scope(tenant_id="ab:cd", user_id="ef")
    assert profile_record_id(a) != profile_record_id(b)


def test_a_slash_inside_an_id_does_not_collide_either():
    """`/` is this store's separator (matching `Scope.key`) and is what `escape_key_slot`
    exists to neutralise inside a slot -- covering the symmetric case to the `:` test above."""
    a = Scope(tenant_id="ab", user_id="cd/ef")
    b = Scope(tenant_id="ab/cd", user_id="ef")
    assert profile_record_id(a) != profile_record_id(b)


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


def test_a_write_under_one_agent_scope_is_visible_from_another_agent_scope_for_the_same_user():
    """A profile is per-(tenant, user), not per-(tenant, user, agent) -- two agents
    consolidating the same user must land on and read the same row (QC round 2, PR #135:
    `profile_record_id` keyed on tenant+user while `_record` stored the full caller `Scope`,
    so an agent-scoped write was invisible to, and silently overwritable by, a different
    agent's consolidation pass for the same user)."""
    s, vector = store()
    s.write(ACME_BOT_A, UserProfile(user_id="ada", name="Ada Lovelace"))

    assert s.get(ACME_BOT_B) is not None
    assert s.get(ACME_BOT_B).name == "Ada Lovelace"
    assert s.get(ACME) is not None
    assert s.get(ACME).name == "Ada Lovelace"
    # One row, not one per agent scope.
    assert len(vector.rows["acme"]) == 1


def test_a_second_agents_write_updates_the_same_row_rather_than_a_shadow_copy():
    s, vector = store()
    s.write(ACME_BOT_A, UserProfile(user_id="ada", name="Ada"))
    s.write(ACME_BOT_B, UserProfile(user_id="ada", name="Ada Lovelace"))

    assert len(vector.rows["acme"]) == 1
    assert s.get(ACME).name == "Ada Lovelace"


async def test_agent_scope_normalization_holds_for_the_async_half_too():
    s, vector = store()
    await s.awrite(ACME_BOT_A, UserProfile(user_id="ada", name="Ada Lovelace"))

    got = await s.aget(ACME_BOT_B)
    assert got is not None
    assert got.name == "Ada Lovelace"
    assert len(vector.rows["acme"]) == 1


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
        raise AssertionError("expected MemoryScopeRequiredError")
    except MemoryScopeRequiredError:
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


def test_get_ignores_a_same_id_row_carrying_the_wrong_record_kind():
    """Exercises the `record_kind` filter through `get()` itself -- unlike the test above, which
    only inspects `vector.rows` directly and cannot fail if `get()`'s own marker check were
    removed (QC round 2, PR #135). Simulates a row at the profile's exact id whose marker
    doesn't match, which the deterministic id alone cannot rule out."""
    s, vector = store()
    wrong_marker = MemoryRecord(
        id=profile_record_id(ACME),
        tier=MemoryTier.SEMANTIC,
        content="not actually a profile",
        scope=ACME,
        metadata={"record_kind": "something_else", "profile": {"user_id": "ada", "name": "Wrong"}},
    )
    vector.upsert(ACME, [wrong_marker])

    assert s.get(ACME) is None


async def test_aget_ignores_a_same_id_row_carrying_the_wrong_record_kind():
    s, vector = store()
    wrong_marker = MemoryRecord(
        id=profile_record_id(ACME),
        tier=MemoryTier.SEMANTIC,
        content="not actually a profile",
        scope=ACME,
        metadata={"record_kind": "something_else", "profile": {"user_id": "ada", "name": "Wrong"}},
    )
    await vector.aupsert(ACME, [wrong_marker])

    assert await s.aget(ACME) is None


# -- extending the schema (documented usage) ------------------------------------


@dataclass
class TeamUserProfile(UserProfile):
    company: Optional[str] = field(default=None, metadata={"description": "Company"})


def test_an_extended_schema_roundtrips_its_own_fields():
    s = SemanticProfileStore(vector=FakeVector(), schema=TeamUserProfile)
    s.write(ACME, TeamUserProfile(user_id="ada", name="Ada", company="Acme"))
    got = s.get(ACME)
    assert got.company == "Acme"
