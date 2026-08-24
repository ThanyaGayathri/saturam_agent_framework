"""The consolidation-driven profile update (SM-S3 #120): ``CoLocatedMemoryProvider.consolidate``/
``aconsolidate`` calling a wired ``profile_store`` and persisting the result through the tenant-
scoped ``SemanticProfileStore`` -- never through ``profile_store.db``.

Doubled with a stub rather than the real ``UserProfileStore``: what this file tests is the
provider's wiring decisions (when the hook runs, what it persists, how an unwired/no-user pass is
reported), not LLM extraction -- that is pinned in ``test_user_profile_extract.py``.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest

from egpt.learn.schemas import UserProfile
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError
from egpt.memory.types import NO_PROFILE, PROFILE_NO_USER, MemoryEvent, Scope
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")
TENANT_WIDE = Scope(tenant_id="acme")


def event(content, **kwargs):
    return MemoryEvent(content=content, scope=kwargs.pop("scope", ACME), **kwargs)


class _StubProfileStore:
    """Returns a fixed profile and records every call -- the pipeline's own decisions (what it
    extracted) are out of scope here; see ``test_user_profile_extract.py``."""

    def __init__(self, name="Ada"):
        self.name = name
        self.extract_calls = []
        self.aextract_calls = []

    def extract_profile(self, messages, user_id, existing_profile=None, run_metrics=None):
        self.extract_calls.append((user_id, existing_profile))
        return UserProfile(user_id=user_id, name=self.name)

    async def aextract_profile(self, messages, user_id, existing_profile=None, run_metrics=None):
        self.aextract_calls.append((user_id, existing_profile))
        return UserProfile(user_id=user_id, name=self.name)


class _NoneExtractingProfileStore:
    """Simulates a real extraction pass that finds nothing to save (blank conversation, no tool
    call) -- unlike ``_StubProfileStore``, which always returns a profile, so no existing test
    exercised the ``if updated is None`` guard with the extractor actually invoked (QC round 2,
    PR #135)."""

    def __init__(self):
        self.extract_calls = []
        self.aextract_calls = []

    def extract_profile(self, messages, user_id, existing_profile=None, run_metrics=None):
        self.extract_calls.append((user_id, existing_profile))
        return None

    async def aextract_profile(self, messages, user_id, existing_profile=None, run_metrics=None):
        self.aextract_calls.append((user_id, existing_profile))
        return None


class _NoOpWriteSemanticProfile:
    """A ``SemanticProfileStore`` double whose write always fails to persist (``None``, matching
    an unwired substrate or an unserialisable profile) -- swapped in for the provider's real
    ``_semantic_profile`` to prove ``_update_profile``/``_aupdate_profile`` report what actually
    landed rather than discarding the write's return value (QC round 2, PR #135). Also records
    every write call, since ``SemanticProfileStore.write``/``awrite`` returning ``None`` for a
    ``None`` profile (``to_dict_safe(None)`` is falsy) would otherwise make a *removed*
    ``if updated is None`` guard produce the same report -- `write_calls`/`awrite_calls` catch
    that the guard's real job is skipping the call entirely, not just its return value."""

    def __init__(self):
        self.write_calls = []
        self.awrite_calls = []

    def write(self, scope, profile):
        self.write_calls.append(profile)
        return None

    async def awrite(self, scope, profile):
        self.awrite_calls.append(profile)
        return None

    def get(self, scope):
        return None

    async def aget(self, scope):
        return None


def make_provider(profile_store=None):
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    provider = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config, profile_store=profile_store)
    return provider, vector, graph


@pytest.mark.parametrize("is_async", [False, True])
def test_a_provider_with_no_profile_store_says_so_rather_than_omitting_the_key(is_async):
    memory, _v, _g = make_provider(profile_store=None)
    memory.write(ACME, [event("hello")])

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["profile"] == NO_PROFILE


@pytest.mark.parametrize("is_async", [False, True])
def test_a_wired_store_updates_and_persists_the_profile(is_async):
    stub = _StubProfileStore(name="Ada Lovelace")
    memory, vector, _g = make_provider(profile_store=stub)
    memory.write(ACME, [event("I'm Ada Lovelace")])

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["profile"] == {"complete": True, "updated": True}
    calls = stub.aextract_calls if is_async else stub.extract_calls
    assert calls[0][0] == "ada"

    got = memory.get_profile(ACME)
    assert got is not None
    assert got.name == "Ada Lovelace"


def test_get_profile_and_aget_profile_are_public_and_return_what_consolidate_wrote():
    """Before this (QC round 3, PR #135), the only way to reach a stored profile was the
    private ``provider._semantic_profile`` attribute directly -- this pins the public accessor
    the cookbook and any other caller now use instead."""
    stub = _StubProfileStore(name="Ada Lovelace")
    memory, _v, _g = make_provider(profile_store=stub)
    memory.write(ACME, [event("I'm Ada Lovelace")])
    memory.consolidate(ACME)

    assert memory.get_profile(ACME) is not None
    assert memory.get_profile(ACME).name == "Ada Lovelace"


async def test_aget_profile_returns_what_consolidate_wrote():
    stub = _StubProfileStore(name="Ada Lovelace")
    memory, _v, _g = make_provider(profile_store=stub)
    memory.write(ACME, [event("I'm Ada Lovelace")])
    await memory.aconsolidate(ACME)

    got = await memory.aget_profile(ACME)
    assert got is not None
    assert got.name == "Ada Lovelace"


def test_get_profile_before_any_consolidation_is_none():
    memory, _v, _g = make_provider(profile_store=_StubProfileStore())
    assert memory.get_profile(ACME) is None


@dataclass
class _TeamProfile(UserProfile):
    company: Optional[str] = None


class _TeamProfileStore:
    """A ``profile_store`` double carrying an extended schema -- exercises that the provider
    threads ``profile_store.schema`` through to its ``SemanticProfileStore`` rather than always
    defaulting to the base ``UserProfile`` (QC round 4, PR #135: before this fix, `company`
    would have been silently dropped on every read through the provider)."""

    schema = _TeamProfile

    def extract_profile(self, messages, user_id, existing_profile=None, run_metrics=None):
        return _TeamProfile(user_id=user_id, name="Ada", company="Acme")

    async def aextract_profile(self, messages, user_id, existing_profile=None, run_metrics=None):
        return _TeamProfile(user_id=user_id, name="Ada", company="Acme")


def test_get_profile_preserves_an_extended_schemas_extra_fields():
    memory, _v, _g = make_provider(profile_store=_TeamProfileStore())
    memory.write(ACME, [event("I'm Ada, I work at Acme")])
    memory.consolidate(ACME)

    got = memory.get_profile(ACME)
    assert got is not None
    assert isinstance(got, _TeamProfile)
    assert got.company == "Acme"


@pytest.mark.parametrize("is_async", [False, True])
def test_a_tenant_wide_pass_has_no_user_to_key_a_profile_by(is_async):
    """``scope.user_id is None`` on a tenant-wide pass -- distinct from ``NO_PROFILE``: the store
    is wired, there's simply no single user this pass names."""
    stub = _StubProfileStore()
    memory, _v, _g = make_provider(profile_store=stub)
    memory.write(ACME, [event("hello")])

    report = asyncio.run(memory.aconsolidate(TENANT_WIDE)) if is_async else memory.consolidate(TENANT_WIDE)

    assert report.details["profile"] != NO_PROFILE
    assert report.details["profile"] == PROFILE_NO_USER
    assert not stub.extract_calls and not stub.aextract_calls


@pytest.mark.parametrize("is_async", [False, True])
def test_a_pass_with_no_episodes_does_not_call_the_store(is_async):
    stub = _StubProfileStore()
    memory, _v, _g = make_provider(profile_store=stub)
    # Nothing written -- an empty tier.

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["profile"] == {"complete": True, "updated": False}
    assert not stub.extract_calls and not stub.aextract_calls


@pytest.mark.parametrize("is_async", [False, True])
def test_an_extraction_with_episodes_present_that_finds_nothing_reports_updated_false(is_async):
    """Distinct from `test_a_pass_with_no_episodes_does_not_call_the_store`: here the extractor
    is actually invoked (episodes present) and itself returns `None` -- the `if updated is None`
    guard's own coverage, not the earlier empty-messages short-circuit's. Asserts on the write
    call, not only the report shape: `SemanticProfileStore.write`/`awrite` already return `None`
    for a `None` profile (`to_dict_safe(None)` is falsy), so the report alone can't tell a removed
    guard from a present one -- see `_NoOpWriteSemanticProfile`'s docstring."""
    stub = _NoneExtractingProfileStore()
    memory, _v, _g = make_provider(profile_store=stub)
    memory._semantic_profile = _NoOpWriteSemanticProfile()
    memory.write(ACME, [event("what's the weather?")])

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["profile"] == {"complete": True, "updated": False}
    extract_calls = stub.aextract_calls if is_async else stub.extract_calls
    assert extract_calls and extract_calls[0][0] == "ada"
    write_calls = memory._semantic_profile.awrite_calls if is_async else memory._semantic_profile.write_calls
    assert write_calls == []


@pytest.mark.parametrize("is_async", [False, True])
def test_a_write_that_persists_nothing_reports_updated_false_not_true(is_async):
    """`_semantic_profile.write`/`awrite` return `Optional[WriteReceipt]`; discarding that and
    reporting `updated: True` unconditionally is exactly the bug this pins (QC round 2, PR #135)."""
    stub = _StubProfileStore(name="Ada")
    memory, _v, _g = make_provider(profile_store=stub)
    memory._semantic_profile = _NoOpWriteSemanticProfile()
    memory.write(ACME, [event("I'm Ada")])

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["profile"] == {"complete": True, "updated": False}


def test_a_second_pass_reuses_the_first_passs_profile_as_existing():
    stub = _StubProfileStore(name="Ada")
    memory, _v, _g = make_provider(profile_store=stub)
    memory.write(ACME, [event("first pass", id="e1")])
    memory.consolidate(ACME)

    memory.write(ACME, [event("second pass", id="e2")])
    memory.consolidate(ACME)

    assert stub.extract_calls[1][1] is not None
    assert stub.extract_calls[1][1].name == "Ada"


def test_a_store_missing_the_async_half_is_refused_when_the_provider_is_built():
    class SyncOnly:
        def extract_profile(self, messages, user_id, existing_profile=None, run_metrics=None): ...

    with pytest.raises(MemoryProviderConfigError) as excinfo:
        CoLocatedMemoryProvider(vector=FakeVector(), graph=FakeGraph(), profile_store=SyncOnly())

    assert excinfo.value.field_name == "options.profile_store"
