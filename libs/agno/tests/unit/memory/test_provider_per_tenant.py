"""Per-tenant memory config resolution (SM-C5, #93).

SM-C5's four acceptance criteria, and what each actually required:

- *Per-tenant config resolves tiers + backends* -- the new work. ``MemoryConfigResolver`` picks
  **which** ``MemoryProviderConfig`` answers for an identity.
- *Graph tier toggle works (vector-only tenant supported)* -- already true before this change:
  ``CoLocatedMemoryProvider`` treats an absent graph as vector-only throughout. Pinned here anyway,
  because "a tenant can be vector-only" is now a per-tenant claim and nothing else asserts it
  through the resolver.
- *Mode A/B selectable per tenant* -- ``config.mode`` already existed; what was missing was two
  tenants resolving to different ones.
- *Invalid config rejected with a clear error* -- the resolver's own validation.

The resolution shape deliberately mirrors ``egpt.providers.config.ProviderConfig``: same wildcard
key, same most-specific-first ordering, same ``default``.
``test_the_candidate_ordering_does_not_drift_from_the_model_seam`` imports the model seam and
compares against what it actually returns, so a reordering there fails here. The two seams are
**not** identical, and deliberately so: the memory side strips and rejects blank identity slots
where the model side does not, because ``Scope`` already ruled that way for this package. Those
divergences are asserted, not assumed -- see ``TestIdentityStringsAreNormalized``.
"""

from typing import Dict

import pytest

from egpt.memory import config as config_module
from egpt.memory.config import (
    MemoryConfigResolver,
    MemoryProviderConfig,
    MemoryProviderConfigError,
    _reset_registry_for_tests,
    build_memory_provider_for,
    register_provider,
)
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import Substrate
from egpt.providers.config import ProviderConfig
from egpt.providers.model_provider import ProviderMode


class _RecordingProvider(MemoryProvider):
    """A provider that remembers the config it was built from, so a test can assert the binding."""

    provider_key = "recording"

    def __init__(self, config: MemoryProviderConfig) -> None:
        self.config = config

    def read(self, *args, **kwargs):  # pragma: no cover - not exercised
        return []

    async def aread(self, *args, **kwargs):  # pragma: no cover
        return []

    def write(self, *args, **kwargs):  # pragma: no cover
        return None

    async def awrite(self, *args, **kwargs):  # pragma: no cover
        return None

    def consolidate(self, *args, **kwargs):  # pragma: no cover
        return None

    async def aconsolidate(self, *args, **kwargs):  # pragma: no cover
        return None

    def read_tier(self, *args, **kwargs):  # pragma: no cover
        return []

    async def aread_tier(self, *args, **kwargs):  # pragma: no cover
        return []

    def write_tier(self, *args, **kwargs):  # pragma: no cover
        return None

    async def awrite_tier(self, *args, **kwargs):  # pragma: no cover
        return None

    def tiers(self) -> Dict[MemoryTier, bool]:
        return {tier: False for tier in MemoryTier}

    def substrates(self) -> Dict[Substrate, bool]:
        # Vector-only when the config names no graph backend -- the shape the graph toggle takes.
        # Enum keys to match the ABC (`provider.py`) and `co_located.py`; `Substrate` is a
        # `str, Enum`, so this compares equal to the string-keyed spelling either way.
        return {Substrate.VECTOR: True, Substrate.GRAPH: bool(self.config.graph)}


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the global registry, then put back exactly what was there.

    Same reasoning as ``test_provider_seam.py``'s fixture: in-tree adapters self-register at import
    time and a module body runs once per process, so clearing without restoring would leave every
    later test in the session facing an empty registry.
    """
    saved = dict(config_module._REGISTRY)
    _reset_registry_for_tests()
    register_provider("recording")(_RecordingProvider)
    yield
    _reset_registry_for_tests()
    config_module._REGISTRY.update(saved)


def _config(mode: ProviderMode = ProviderMode.LOCAL, **kwargs) -> MemoryProviderConfig:
    return MemoryProviderConfig(provider="recording", mode=mode, **kwargs)


# ---------------------------------------------------------------------------
# AC1: a per-tenant config resolves
# ---------------------------------------------------------------------------


class TestResolutionPicksTheMostSpecificPolicy:
    def test_an_exact_tenant_and_agent_key_wins_over_the_tenant_wildcard(self):
        exact, tenant_wide = _config(), _config(ProviderMode.FRONTIER)
        resolver = MemoryConfigResolver(policies={("acme", "agent-1"): exact, ("acme", None): tenant_wide})

        assert resolver.lookup("acme", "agent-1") is exact
        assert resolver.lookup("acme", "agent-2") is tenant_wide

    def test_a_bare_tenant_string_is_the_same_key_as_a_padded_tuple(self):
        """``"acme"`` and ``("acme", None)`` must not be two different policies."""
        cfg = _config()
        resolver = MemoryConfigResolver(policies={"acme": cfg})

        assert resolver.lookup("acme") is cfg
        assert resolver.lookup("acme", "any-agent") is cfg

    def test_an_agent_wildcard_answers_across_tenants(self):
        agent_wide = _config()
        resolver = MemoryConfigResolver(policies={(None, "shared-agent"): agent_wide})

        assert resolver.lookup("acme", "shared-agent") is agent_wide
        assert resolver.lookup("beta", "shared-agent") is agent_wide

    def test_an_unmatched_identity_falls_back_to_default(self):
        fallback = _config()
        resolver = MemoryConfigResolver(policies={"acme": _config(ProviderMode.FRONTIER)}, default=fallback)

        assert resolver.lookup("unknown-tenant") is fallback

    def test_no_match_and_no_default_resolves_to_none(self):
        resolver = MemoryConfigResolver(policies={"acme": _config()})

        assert resolver.lookup("beta") is None

    def test_candidate_order_matches_the_model_seams_ordering(self):
        """The correspondence with ``ProviderConfig.candidate_keys`` is the point, so pin it."""
        resolver = MemoryConfigResolver(default=_config())

        assert resolver.candidate_keys("t", "a") == (("t", "a"), ("t", None), (None, "a"), (None, None))

    def test_the_candidate_ordering_does_not_drift_from_the_model_seam(self):
        """Compare against what the model seam *returns*, not against a literal copied from it.

        The file docstring claims the two seams share an ordering. Asserting that against a
        hardcoded tuple makes the claim unfalsifiable -- reordering ``ProviderConfig`` tomorrow
        would not fail anything here. So derive the expected value from the model seam itself.

        The model key has three slots to memory's two (``role``), so project its keys onto the
        first two and drop the duplicates that projection creates, preserving order. What survives
        is the specificity ordering both seams agree on, and *that* is what must not drift.
        """
        memory_order = MemoryConfigResolver(default=_config()).candidate_keys("t", "a")

        model_keys = ProviderConfig(default=None, policies={}).candidate_keys("t", "a", "r")
        projected = []
        for tenant, agent, _role in model_keys:
            if (tenant, agent) not in projected:
                projected.append((tenant, agent))

        assert tuple(projected) == memory_order

    def test_a_tenant_wildcard_outranks_an_agent_wildcard(self):
        """The isolation-relevant precedence, asserted behaviourally rather than by tuple shape.

        ``("acme", None)`` above ``(None, "shared")`` is what keeps a tenant-wide policy from being
        overridden by a policy some shared agent carries across every tenant. Swapping those two
        lines in ``candidate_keys`` previously failed exactly one test -- the tuple comparison
        above -- so neutering that one assertion made the swap invisible. This one resolves.
        """
        tenant_wide, agent_wide = _config(), _config(ProviderMode.FRONTIER)
        resolver = MemoryConfigResolver(policies={("acme", None): tenant_wide, (None, "shared"): agent_wide})

        assert resolver.lookup("acme", "shared") is tenant_wide
        assert resolver.lookup("beta", "shared") is agent_wide


# ---------------------------------------------------------------------------
# AC2: graph tier toggle -- a vector-only tenant beside a graph-enabled one
# ---------------------------------------------------------------------------


class TestAVectorOnlyTenantIsSupported:
    def test_two_tenants_can_differ_on_whether_the_graph_tier_is_configured(self):
        """The toggle is "does this tenant's config name a graph backend", not a boolean flag.

        Omitting ``graph`` is what makes a tenant vector-only -- the provider already treats an
        absent graph as such. What SM-C5 adds is that the answer can differ *per tenant*.
        """
        vector_only = _config()
        with_graph = _config(graph={"backend": "fake-graph"})
        resolver = MemoryConfigResolver(policies={"vec-tenant": vector_only, "graph-tenant": with_graph})

        assert resolver.lookup("vec-tenant").graph == {}
        assert resolver.lookup("graph-tenant").graph == {"backend": "fake-graph"}

    def test_the_vector_only_config_builds_a_provider(self):
        resolver = MemoryConfigResolver(policies={"vec-tenant": _config()})

        provider = build_memory_provider_for(resolver, "vec-tenant")

        assert isinstance(provider, _RecordingProvider)
        assert provider.config.graph == {}

    def test_the_built_provider_reports_vector_only_and_its_twin_reports_graph_on(self):
        """The criterion itself: assert the *provider's* substrate report, not the dict we passed.

        The two tests above compare ``config.graph`` against the dict the test just constructed --
        that proves the resolver round-trips a config, not that a vector-only tenant behaves as
        vector-only. Inverting ``_RecordingProvider.substrates()`` left all of them passing,
        because nothing called it. The ticket's testing note asks to "toggle graph off for a
        tenant; assert vector-only retrieval path", and this is that assertion. Shape copied from
        ``test_provider_co_located.py``.
        """
        resolver = MemoryConfigResolver(
            policies={"vec-tenant": _config(), "graph-tenant": _config(graph={"backend": "fake-graph"})}
        )

        vector_only = build_memory_provider_for(resolver, "vec-tenant")
        with_graph = build_memory_provider_for(resolver, "graph-tenant")

        assert vector_only.substrates() == {Substrate.VECTOR: True, Substrate.GRAPH: False}
        assert with_graph.substrates() == {Substrate.VECTOR: True, Substrate.GRAPH: True}


# ---------------------------------------------------------------------------
# AC3: Mode A/B selectable per tenant
# ---------------------------------------------------------------------------


class TestModeIsSelectablePerTenant:
    def test_two_tenants_resolve_to_different_modes(self):
        resolver = MemoryConfigResolver(
            policies={
                "airgapped": _config(ProviderMode.LOCAL),
                "hosted": _config(ProviderMode.FRONTIER),
            }
        )

        # `config.mode` rather than a `mode()` method: `MemoryProvider` declares no such method,
        # neither adapter defines one, and real consumers read the config. AC3's evidence should
        # not run through a surface only this file's double has.
        assert build_memory_provider_for(resolver, "airgapped").config.mode is ProviderMode.LOCAL
        assert build_memory_provider_for(resolver, "hosted").config.mode is ProviderMode.FRONTIER

    def test_the_agent_slot_reaches_the_build_path(self):
        """Two calls differing only in ``agent_id``, resolving to different modes.

        Nothing else pinned that ``build_memory_provider_for`` forwards the agent slot at all:
        rewriting its body to ``resolver.lookup(tenant_id)`` -- discarding half of
        ``MemoryPolicyKey``, this ticket's headline data structure -- previously left every test
        passing. The agent slot was exercised only through ``lookup`` directly and through the
        error-message test, which builds its message from ``candidate_keys`` independently.
        """
        resolver = MemoryConfigResolver(
            policies={
                ("acme", "planner"): _config(ProviderMode.LOCAL),
                ("acme", None): _config(ProviderMode.FRONTIER),
            }
        )

        assert build_memory_provider_for(resolver, "acme", "planner").config.mode is ProviderMode.LOCAL
        assert build_memory_provider_for(resolver, "acme", "critic").config.mode is ProviderMode.FRONTIER

    def test_modes_reports_every_mode_the_resolver_can_produce(self):
        """A deployment that believes it is air-gapped asserts this, per ADR 0003 A.3c."""
        local_only = MemoryConfigResolver(policies={"a": _config(ProviderMode.LOCAL)})
        mixed = MemoryConfigResolver(
            policies={"a": _config(ProviderMode.LOCAL)}, default=_config(ProviderMode.FRONTIER)
        )

        assert local_only.modes() == (ProviderMode.LOCAL,)
        assert mixed.modes() == (ProviderMode.FRONTIER, ProviderMode.LOCAL)

    def test_a_mode_string_survives_resolution_as_the_enum(self):
        """Configs arriving from JSON compare with ``is`` downstream, so coercion must hold."""
        resolver = MemoryConfigResolver(policies={"acme": MemoryProviderConfig(provider="recording", mode="frontier")})

        assert resolver.lookup("acme").mode is ProviderMode.FRONTIER


# ---------------------------------------------------------------------------
# AC4: invalid configuration is rejected with a clear error
# ---------------------------------------------------------------------------


class TestInvalidConfigurationIsRejected:
    def test_an_empty_resolver_is_refused_at_definition_not_at_lookup(self):
        """Otherwise the mistake surfaces later as a confusing "no config" at the build site."""
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver()

        assert "at least one policy or a default" in str(excinfo.value)

    def test_a_key_with_too_many_parts_names_the_expected_slots(self):
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={("t", "a", "role"): _config()})

        assert "expected at most 2" in str(excinfo.value)
        assert "tenant_id, agent_id" in str(excinfo.value)

    @pytest.mark.parametrize("bad_key", [7, 3.5])
    def test_a_key_of_the_wrong_type_is_refused(self, bad_key):
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={bad_key: _config()})

        assert "string or a tuple" in str(excinfo.value)

    def test_a_non_string_key_part_is_refused(self):
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={("acme", 7): _config()})

        assert "non-string part" in str(excinfo.value)

    def test_a_policy_that_is_not_a_config_is_refused(self):
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={"acme": "not-a-config"})

        assert "must be a MemoryProviderConfig" in str(excinfo.value)

    def test_a_default_that_is_not_a_config_is_refused(self):
        with pytest.raises(MemoryProviderConfigError):
            MemoryConfigResolver(default="not-a-config")

    def test_two_spellings_of_one_key_are_refused_rather_than_silently_ranked(self):
        """``"acme"`` and ``("acme", None)`` normalize to the same key; the loser would be inert."""
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={"acme": _config(), ("acme", None): _config(ProviderMode.FRONTIER)})

        assert "Duplicate policy key" in str(excinfo.value)

    def test_building_for_an_unmatched_identity_names_what_was_tried(self):
        """The error has to be diagnosable: which keys were checked, in what order."""
        resolver = MemoryConfigResolver(policies={"acme": _config()})

        with pytest.raises(MemoryProviderConfigError) as excinfo:
            build_memory_provider_for(resolver, "beta", "agent-9")

        message = str(excinfo.value)
        assert "tenant_id='beta'" in message
        assert "('beta', 'agent-9')" in message


# ---------------------------------------------------------------------------
# Construction still goes through the one seam
# ---------------------------------------------------------------------------


class TestConstructionStaysBehindTheSingleSeam:
    def test_an_unregistered_provider_is_still_refused_through_the_per_tenant_path(self):
        """``build_memory_provider_for`` must not become a way around the registry check."""
        resolver = MemoryConfigResolver(policies={"acme": MemoryProviderConfig(provider="never-registered")})

        with pytest.raises(MemoryProviderConfigError):
            build_memory_provider_for(resolver, "acme")

    def test_with_policy_returns_a_copy_and_leaves_the_original_alone(self):
        original = MemoryConfigResolver(policies={"acme": _config()})

        updated = original.with_policy("beta", _config(ProviderMode.FRONTIER))

        assert original.lookup("beta") is None, "the original resolver was mutated"
        assert updated.lookup("beta").mode is ProviderMode.FRONTIER
        assert updated.lookup("acme") is not None, "the copy dropped an existing policy"


# ---------------------------------------------------------------------------
# Identity handling: both sides of the seam agree, and neither accepts a blank
# ---------------------------------------------------------------------------


class TestIdentityStringsAreNormalized:
    """A policy map and a request must land on the same key for the same tenant.

    This is where the memory seam deliberately diverges from the model seam, which strips nothing.
    ``Scope`` already ruled this way for this package (``memory/types.py``), and ``require_scope``
    names the failure verbatim: a stray ``"acme "`` *"becomes a distinct tenant to any equality
    predicate."* Tenant ids arrive from headers and JWTs, so the query side is the half that takes
    untrusted input -- validating only the policy side would leave the misroute reachable.
    """

    def test_a_padded_request_resolves_to_the_same_policy_as_the_clean_one(self):
        local = _config(ProviderMode.LOCAL)
        resolver = MemoryConfigResolver(policies={"acme": local}, default=_config(ProviderMode.FRONTIER))

        assert resolver.lookup("acme") is local
        assert resolver.lookup("acme ") is local, "an air-gapped tenant fell through to the default"
        assert resolver.lookup(" acme") is local
        assert resolver.lookup("acme", " planner ") is local

    def test_a_padded_policy_key_resolves_to_the_clean_request(self):
        """The other direction: the dirty spelling is in the config, not the request."""
        local = _config()
        resolver = MemoryConfigResolver(policies={" acme ": local})

        assert resolver.lookup("acme") is local

    def test_two_spellings_that_differ_only_in_padding_collide_rather_than_last_win(self):
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={"acme": _config(), " acme": _config(ProviderMode.FRONTIER)})

        assert "Duplicate policy key" in str(excinfo.value)

    def test_the_duplicate_message_names_both_colliding_spellings(self):
        """An operator with a 200-entry policy map needs to know *which two lines* collided.

        Both spellings are deliberately chosen to be absent from the normalized key the message
        already prints. Using ``"acme"`` here would make the test vacuous -- ``'acme'`` is a
        substring of ``('acme', None)``, so the assertion would hold even for a message that named
        no spellings at all.
        """
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={" acme ": _config(), ("acme ", None): _config(ProviderMode.FRONTIER)})

        message = str(excinfo.value)
        assert "' acme '" in message, "the first-seen spelling is not named"
        assert "('acme ', None)" in message, "the colliding spelling is not named"

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_policy_key_is_refused(self, blank):
        """Blank is not a wildcard -- ``None`` is -- and it cannot identify a tenant either."""
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={blank: _config()})

        assert "blank" in str(excinfo.value)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_identity_is_refused_at_lookup(self, blank):
        resolver = MemoryConfigResolver(policies={"acme": _config()}, default=_config())

        with pytest.raises(MemoryProviderConfigError):
            resolver.lookup(blank)

    @pytest.mark.parametrize("bad", [123, 3.5, object()])
    def test_a_non_string_identity_is_refused_at_lookup_rather_than_routed_to_default(self, bad):
        """An ``int`` tenant id -- straight out of a database -- used to resolve to ``default``.

        Silently. That is the same misroute class as the padding case, and it survives any fix
        confined to the policy side.
        """
        resolver = MemoryConfigResolver(policies={"acme": _config()}, default=_config(ProviderMode.FRONTIER))

        with pytest.raises(MemoryProviderConfigError) as excinfo:
            resolver.lookup(bad)

        assert excinfo.value.field_name == "tenant_id"

    def test_an_unhashable_identity_raises_the_typed_error_not_a_bare_builtin(self):
        resolver = MemoryConfigResolver(policies={"acme": _config()}, default=_config())

        with pytest.raises(MemoryProviderConfigError):
            resolver.lookup(["acme"])

    def test_case_is_significant(self):
        """Not a behaviour request -- case-sensitive is right for opaque tenant ids.

        It simply was not asserted, so the normalization work above could have flipped it
        unnoticed. Pinning it makes any future case-folding a deliberate change.
        """
        local = _config(ProviderMode.LOCAL)
        resolver = MemoryConfigResolver(policies={"ACME": local}, default=_config(ProviderMode.FRONTIER))

        assert resolver.lookup("ACME") is local
        assert resolver.lookup("acme").mode is ProviderMode.FRONTIER

    def test_candidate_keys_echoes_the_normalized_identity_not_the_callers_spelling(self):
        """A diagnostic that echoed the un-stripped spelling would hide the mismatch it is run to find."""
        resolver = MemoryConfigResolver(default=_config())

        assert resolver.candidate_keys(" acme ", " planner ")[0] == ("acme", "planner")


# ---------------------------------------------------------------------------
# The validation cannot be walked around after construction
# ---------------------------------------------------------------------------


class TestValidationHoldsAfterConstruction:
    def test_an_identity_less_policy_may_not_shadow_the_default(self):
        """``(None, None)`` is a candidate for every identity, so ``default`` could never win.

        Two defaults, one live, no error is the misconfiguration shape this module rejects
        everywhere else. The model seam rejects the analogous ``(None, None, None)``.
        """
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies={(None, None): _config()}, default=_config(ProviderMode.FRONTIER))

        assert "shadows 'default'" in str(excinfo.value)

    @pytest.mark.parametrize("spelling", [None, ()])
    def test_every_spelling_of_the_identity_less_key_is_caught(self, spelling):
        """``None`` and ``()`` normalize to ``(None, None)``; catching only the tuple would be half a fix."""
        with pytest.raises(MemoryProviderConfigError):
            MemoryConfigResolver(policies={spelling: _config()}, default=_config(ProviderMode.FRONTIER))

    def test_with_policy_cannot_smuggle_in_a_shadowing_key(self):
        """``replace()`` re-runs ``__post_init__``, so the copy path is closed by the same guard."""
        resolver = MemoryConfigResolver(policies={"acme": _config()}, default=_config(ProviderMode.FRONTIER))

        with pytest.raises(MemoryProviderConfigError):
            resolver.with_policy(None, _config())

    def test_an_identity_less_policy_is_fine_without_a_default(self):
        """The guard is about the *pair*. Alone, ``(None, None)`` is a legitimate catch-all."""
        catch_all = _config()

        assert MemoryConfigResolver(policies={(None, None): catch_all}).lookup("anything") is catch_all

    def test_policies_is_read_only_after_construction(self):
        """A plain dict left every ``__post_init__`` check bypassable by writing to it.

        Both failure shapes were reachable: a non-config value reaching ``_REGISTRY.get`` as an
        ``AttributeError`` several frames from the caller, and an un-normalized key that is
        permanently inert. ``ProviderConfig`` uses ``MappingProxyType`` for this reason.
        """
        resolver = MemoryConfigResolver(policies={"acme": _config()})

        with pytest.raises(TypeError):
            resolver.policies[("beta", None)] = "junk"  # type: ignore[index]

    @pytest.mark.parametrize("bad", [["acme"], "acme", 5, None])
    def test_a_non_mapping_policies_raises_the_typed_error(self, bad):
        """These used to escape as bare ``ValueError``/``TypeError`` -- uncatchable by AC4's handler.

        "dictionary update sequence element #0 has length 4; 2 is required" is also not the clear
        error the criterion asks for.
        """
        with pytest.raises(MemoryProviderConfigError) as excinfo:
            MemoryConfigResolver(policies=bad)

        assert excinfo.value.field_name == "policies"


# ---------------------------------------------------------------------------
# Documented consequences -- pinned so they stay decisions rather than accidents
# ---------------------------------------------------------------------------


class TestResolvedConfigsAreShared:
    def test_lookup_returns_the_stored_instance_not_a_copy(self):
        """Aliasing is real and documented; the class docstring says so.

        Not a defect introduced here -- it follows from ``MemoryProviderConfig`` being a plain
        unfrozen dataclass, which predates this ticket and cannot simply take ``frozen=True``
        because its ``__post_init__`` reassigns four fields. Freezing it is its own ticket. What
        this test buys is that the sharing is a recorded decision: if someone later makes ``lookup``
        copy, they will see this test and decide deliberately.
        """
        shared = _config()
        resolver = MemoryConfigResolver(policies={"acme": shared, "beta": shared})

        assert resolver.lookup("acme") is shared
        assert resolver.lookup("acme") is resolver.lookup("beta")

    def test_every_identity_falling_through_to_default_gets_one_object(self):
        """Two tenants on ``default`` share a config -- and therefore one physical store.

        Selection is not isolation; ``Scope`` is what partitions data at read/write time.
        """
        fallback = _config()
        resolver = MemoryConfigResolver(policies={"acme": _config()}, default=fallback)

        assert resolver.lookup("x") is resolver.lookup("y") is fallback


class TestTheBuildEntryPointRequiresATenant:
    def test_tenant_id_is_required(self):
        """``memory/types.py``: "a seam where the unscoped call is the easiest one to write is a
        seam that leaks." ``build_memory_provider_for(resolver)`` was previously both legal and the
        shortest call, and it resolved cleanly to whatever ``default`` was.
        """
        resolver = MemoryConfigResolver(default=_config())

        with pytest.raises(TypeError):
            build_memory_provider_for(resolver)  # type: ignore[call-arg]

    def test_lookup_stays_permissive_for_diagnostics(self):
        """The strictness belongs on the build path, not on the question-answering one.

        An operator asking "what does a wildcard-only resolver answer for nobody in particular?"
        is a legitimate question; building a provider for nobody in particular is not.
        """
        fallback = _config()
        resolver = MemoryConfigResolver(default=fallback)

        assert resolver.lookup() is fallback

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_tenant_is_refused_at_the_build_path(self, blank):
        resolver = MemoryConfigResolver(default=_config())

        with pytest.raises(MemoryProviderConfigError):
            build_memory_provider_for(resolver, blank)

    def test_an_explicit_none_tenant_is_refused_like_a_blank_one(self):
        """Regression: ``None`` resolved cleanly while the identical ``""`` was refused.

        ``_normalize_part``'s ``None`` branch is a *wildcard* -- right for a policy key, where
        ``("acme", None)`` means "every agent of acme", and wrong for this query entry point,
        where an absent tenant means "no tenant identified". Passing it through left the laxer
        spelling as the one that silently worked, and handed an unidentified caller whatever
        ``default`` was.

        Not hypothetical: ``agent/_run.py`` records the same unthreaded ``tenant_id=None``
        silently defeating per-tenant resolution in ``ProviderConfig``, the sibling seam this
        class is modelled on (QC review of #150).
        """
        resolver = MemoryConfigResolver(default=_config())

        with pytest.raises(MemoryProviderConfigError) as excinfo:
            build_memory_provider_for(resolver, None)  # type: ignore[arg-type]

        assert "requires a tenant_id" in str(excinfo.value)

    def test_a_none_tenant_cannot_reach_a_cross_tenant_agent_policy(self):
        """The sharper half: an agent-wildcard policy made ``None`` resolve to a real config.

        With ``(None, "shared-agent")`` registered, an unidentified caller naming that agent
        matched a policy rather than falling through to ``default`` -- so the refusal has to
        land before resolution, not after it.
        """
        agent_wide = _config(ProviderMode.FRONTIER)
        resolver = MemoryConfigResolver(policies={(None, "shared-agent"): agent_wide}, default=_config())

        with pytest.raises(MemoryProviderConfigError):
            build_memory_provider_for(resolver, None, "shared-agent")  # type: ignore[arg-type]
