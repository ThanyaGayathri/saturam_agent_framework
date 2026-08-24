"""The consolidation-driven profile update (SM-S3 #120): ``CoLocatedMemoryProvider.consolidate``/
``aconsolidate`` calling a wired ``profile_store`` and persisting the result through the tenant-
scoped ``SemanticProfileStore`` -- never through ``profile_store.db``.

Doubled with a stub rather than the real ``UserProfileStore``: what this file tests is the
provider's wiring decisions (when the hook runs, what it persists, how an unwired/no-user pass is
reported), not LLM extraction -- that is pinned in ``test_user_profile_extract.py``.
"""

import asyncio

import pytest

from egpt.learn.schemas import UserProfile
from egpt.memory.adapters.co_located import _PROFILE_NO_USER, CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError
from egpt.memory.types import NO_PROFILE, MemoryEvent, Scope
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

    got = memory._semantic_profile.get(ACME)
    assert got is not None
    assert got.name == "Ada Lovelace"


@pytest.mark.parametrize("is_async", [False, True])
def test_a_tenant_wide_pass_has_no_user_to_key_a_profile_by(is_async):
    """``scope.user_id is None`` on a tenant-wide pass -- distinct from ``NO_PROFILE``: the store
    is wired, there's simply no single user this pass names."""
    stub = _StubProfileStore()
    memory, _v, _g = make_provider(profile_store=stub)
    memory.write(ACME, [event("hello")])

    report = asyncio.run(memory.aconsolidate(TENANT_WIDE)) if is_async else memory.consolidate(TENANT_WIDE)

    assert report.details["profile"] != NO_PROFILE
    assert report.details["profile"] == _PROFILE_NO_USER
    assert not stub.extract_calls and not stub.aextract_calls


@pytest.mark.parametrize("is_async", [False, True])
def test_a_pass_with_no_episodes_does_not_call_the_store(is_async):
    stub = _StubProfileStore()
    memory, _v, _g = make_provider(profile_store=stub)
    # Nothing written -- an empty tier.

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["profile"] == {"complete": True, "updated": False}
    assert not stub.extract_calls and not stub.aextract_calls


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
