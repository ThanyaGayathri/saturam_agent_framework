"""``same_model`` and the declared-connection snapshot (EGPT-3.5 follow-up).

``same_model`` answers one question for ``Agent.deep_copy`` / ``Team.deep_copy``: did the caller
choose a genuinely different model, or hand back the one already in use? Getting it wrong is not a
bookkeeping error -- a provider honouring pins returns ``request.requested`` unchanged, so a wrong
"same" keeps the *previous* tenant's object as the pin and the copy then serves traffic from that
tenant's endpoint under that tenant's key.

Two rounds of review shipped green with a real defect open because the tests and the helper shared
a blind spot: every test used a double declaring none of the connection attributes, so nothing
exercised the connection question at all. These tests are written to attack the helper rather than
to match it -- in particular ``TestNullIsNotAWildcard`` fails under the natural-looking repair
("skip an attribute when either side is ``None``"), which passes the rest of the suite while
re-opening a cross-tenant leak.
"""

from __future__ import annotations

import dataclasses
from typing import Any, List, Tuple

import pytest

from egpt.models.base import (
    _CONNECTION_ATTRS,
    _CONNECTION_EXCLUSIONS,
    _CONNECTION_NON_DISCRIMINATORS,
    Model,
    same_model,
)

openai_module = pytest.importorskip(
    "egpt.integrations.models.openai", reason="the connection cases need a real provider subclass"
)
OpenAIResponses = openai_module.OpenAIResponses


class BareModel(Model):
    """A ``Model`` that declares no connection attributes. Right for the identity question only."""

    def invoke(self, *args: Any, **kwargs: Any):  # pragma: no cover - never called
        raise AssertionError("not invoked")

    async def ainvoke(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    def invoke_stream(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    def _parse_provider_response(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")

    def _parse_provider_response_delta(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("not invoked")


class TestTheIdentityQuestion:
    def test_an_absent_side_never_matches(self):
        """An agent with no model cannot already be using the one being offered. Two ``None``s do
        not match either -- an absent model is not a model."""
        m = BareModel(id="x")
        assert same_model(None, m) is False
        assert same_model(m, None) is False
        assert same_model(None, None) is False

    def test_the_same_object_matches(self):
        m = BareModel(id="x")
        assert same_model(m, m) is True

    def test_a_distinct_but_equivalent_object_matches(self):
        """The case identity alone got wrong: every in-tree provider builds a fresh ``Model`` per
        resolve, so the model an agent is already using reaches a caller as a distinct object."""
        assert same_model(BareModel(id="x"), BareModel(id="x")) is True

    def test_a_different_id_does_not_match(self):
        assert same_model(BareModel(id="x"), BareModel(id="y")) is False

    def test_a_different_class_does_not_match(self):
        class OtherModel(BareModel):
            pass

        assert same_model(OtherModel(id="x"), BareModel(id="x")) is False


class TestTheConnectionQuestion:
    """Class-and-id alone is not enough, and this direction is the dangerous one: two models can
    share both while pointing at different tenants under different credentials."""

    def test_a_different_endpoint_does_not_match(self):
        a = OpenAIResponses(id="gpt-5.5", base_url="https://tenant-a.internal/v1", api_key="key_a")
        b = OpenAIResponses(id="gpt-5.5", base_url="https://tenant-b.internal/v1", api_key="key_b")
        assert same_model(b, a) is False

    def test_the_same_explicit_connection_matches(self):
        kwargs = {"id": "gpt-5.5", "base_url": "https://tenant-a.internal/v1", "api_key": "key_a"}
        assert same_model(OpenAIResponses(**kwargs), OpenAIResponses(**kwargs)) is True

    @pytest.mark.parametrize("attr", ["base_url", "api_key", "organization"])
    def test_each_connection_attribute_discriminates_on_its_own(self, attr):
        """One attribute differing is enough. Parametrised over the attributes an
        ``OpenAIResponses`` actually declares, so the assertion is real rather than vacuous."""
        base = {"id": "gpt-5.5", "base_url": "https://x/v1", "api_key": "k", "organization": "org-1"}
        other = dict(base)
        other[attr] = "different-value"
        assert same_model(OpenAIResponses(**other), OpenAIResponses(**base)) is False

    @pytest.mark.parametrize("attr", ["base_url", "api_key", "organization"])
    def test_an_attribute_set_on_one_side_only_does_not_match(self, attr):
        """The column that was missing, and the cheapest guard against a repeat: a value on one
        side and nothing on the other is a *different* connection, not an unknown one."""
        declared = OpenAIResponses(**{"id": "gpt-5.5", attr: "a-value"})
        bare = OpenAIResponses(id="gpt-5.5")
        assert same_model(declared, bare) is False
        assert same_model(bare, declared) is False


class TestTheLazyFillDoesNotBreakComparison:
    """Providers resolve credentials inside ``_get_client_params`` and write them onto ``self``, so
    a model that has served one request carries values its own reconstruction does not."""

    def test_an_identical_reconstruction_still_matches_after_a_client_build(self, monkeypatch):
        """The round-2 critical. Before the declared-connection snapshot this returned False from
        run 2 onward, re-opening both directions of the pin bug."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        live = OpenAIResponses(id="gpt-5.5")
        live.get_client()
        assert live.api_key == "sk-from-the-environment", "the lazy fill must have happened"

        assert same_model(OpenAIResponses(id="gpt-5.5"), live) is True

    def test_an_explicitly_configured_model_still_matches_after_a_client_build(self, monkeypatch):
        """The guard is ``if not self.X:``, so an explicit value is never overwritten and must
        keep comparing equal for the model's whole life."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        kwargs = {"id": "gpt-5.5", "base_url": "https://tenant-a.internal/v1", "api_key": "key_a"}
        live = OpenAIResponses(**kwargs)
        live.get_client()
        assert live.api_key == "key_a", "an explicit key must not be overwritten by the environment"

        assert same_model(OpenAIResponses(**kwargs), live) is True

    def test_a_caller_edit_after_construction_is_still_honoured(self):
        """``value -> value`` is unambiguously a caller edit -- the lazy-fill guard would not have
        fired -- so it must be read, or an edited model would compare equal to its original."""
        live = OpenAIResponses(id="gpt-5.5", base_url="https://tenant-a.internal/v1")
        live.base_url = "https://tenant-b.internal/v1"

        assert same_model(OpenAIResponses(id="gpt-5.5", base_url="https://tenant-a.internal/v1"), live) is False


class TestNullIsNotAWildcard:
    """The tests that discriminate a correct fix from the one that looks obvious.

    The natural repair for the lazy-fill problem is "skip an attribute when either side is
    ``None``". It passes every other test in this file and re-opens the cross-tenant leak, because
    a bare model has *every* connection attribute ``None`` by construction -- so the rule
    degenerates to class-and-id and a tenant-pinned agent keeps the previous tenant's credentials.

    ``None`` on a bare ``OpenAIResponses(id=...)`` is a positive assertion of the default public
    endpoint, not an absence of information.
    """

    def test_a_bare_model_is_not_the_same_as_a_tenant_configured_one(self):
        tenant_a = OpenAIResponses(id="gpt-5.5", base_url="https://tenant-a.internal/v1", api_key="key_a")
        bare = OpenAIResponses(id="gpt-5.5")

        assert all(getattr(bare, name, None) is None for name in _CONNECTION_ATTRS), (
            "precondition: the bare model declares nothing, which is what makes the wrong fix wrong"
        )
        assert same_model(bare, tenant_a) is False

    def test_a_bare_model_after_a_client_build_is_still_not_the_tenant_model(self, monkeypatch):
        """The two failure modes combined: the lazy fill populates the bare model's ``api_key``,
        and it still must not be confused with a tenant-configured model."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        tenant_a = OpenAIResponses(id="gpt-5.5", base_url="https://tenant-a.internal/v1", api_key="key_a")
        bare = OpenAIResponses(id="gpt-5.5")
        bare.get_client()

        assert same_model(bare, tenant_a) is False


class TestTheAttributeTupleDoesNotDrift:
    """A hardcoded allowlist that no test enforces is stale within two provider additions, and the
    failure mode is silent cross-tenant reuse rather than a broken build."""

    def _constructible_subclasses(self) -> List[type]:
        """Every importable ``Model`` subclass in the tree.

        Walks the integrations package defensively: several provider modules raise ``ImportError``
        from a compatibility shim when their SDK is absent, and one shim raises even on
        ``__bases__``, so both the import and the membership test are guarded.
        """
        import importlib
        import pkgutil

        import egpt.integrations.models as pkg

        found: List[type] = []
        for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            try:
                module = importlib.import_module(info.name)
            except Exception:
                continue
            for name in dir(module):
                try:
                    obj = getattr(module, name)
                    if isinstance(obj, type) and issubclass(obj, Model) and obj is not Model:
                        found.append(obj)
                except Exception:
                    continue
        return sorted(set(found), key=lambda c: f"{c.__module__}.{c.__name__}")

    #: Substrings that mark a dataclass field as carrying an endpoint or a credential.
    _VOCABULARY = (
        "api_key",
        "base_url",
        "endpoint",
        "host",
        "region",
        "location",
        "project",
        "organization",
        "account",
        "gateway",
        "resource",
        "auth_token",
        "session_token",
        "ad_token",
        "secret",
        "url",
        "credential",
        "session",
        "api_base",
        "api_version",
        "virtual_key",
        "vertexai",
    )

    def test_every_credential_or_endpoint_field_is_listed_or_excluded(self):
        """The drift guard. A new provider adding a discriminator must either list it in
        ``_CONNECTION_ATTRS`` or record it in ``_CONNECTION_EXCLUSIONS`` with a reason -- silence
        means ``same_model`` treats two tenants as one."""
        known = set(_CONNECTION_ATTRS) | set(_CONNECTION_EXCLUSIONS) | set(_CONNECTION_NON_DISCRIMINATORS)
        unlisted: List[Tuple[str, str]] = []
        for cls in self._constructible_subclasses():
            if not dataclasses.is_dataclass(cls):
                continue
            for field in dataclasses.fields(cls):
                if field.name.startswith("_") or field.name in known:
                    continue
                if any(token in field.name for token in self._VOCABULARY):
                    unlisted.append((f"{cls.__module__}.{cls.__name__}", field.name))

        assert not unlisted, (
            "connection-bearing fields neither compared nor explicitly accounted for — add each "
            "to _CONNECTION_ATTRS, or to _CONNECTION_EXCLUSIONS / _CONNECTION_NON_DISCRIMINATORS "
            "with the reason:\n" + "\n".join(f"  {c}.{f}" for c, f in sorted(set(unlisted)))
        )

    @pytest.mark.parametrize(
        "name",
        [
            # The pairs whose *spelling* differs between providers, which is how a discriminator
            # goes missing: carrying only one of each leaves the other class's identity invisible.
            "aws_access_key_id",  # aws/bedrock.py
            "aws_access_key",  # aws/claude.py -- a different field, not a duplicate
            "aws_secret_access_key",  # aws/bedrock.py
            "aws_secret_key",  # aws/claude.py
            "aws_session_token",
            "region",  # vertexai/claude.py -- distinct from aws_region
            "aws_region",
            "base_host_url",  # langdb
            "api_base",  # litellm
            "endpoint",  # mistral
            "url",  # ibm watsonx
            "auth_token",  # anthropic
            "azure_ad_token",  # azure openai
            "resource",  # azure claude
            "account_id",  # cloudflare
            "gateway_id",  # cloudflare
            "portkey_api_key",
            "virtual_key",  # portkey
            "parallel_api_key",  # google gemini
            "vertexai",  # google gemini: flips the whole auth path
        ],
    )
    def test_a_known_discriminator_stays_in_the_tuple(self, name):
        """Pins each name whose provider SDK may be absent from a given environment.

        The drift test above can only see classes that import, so on a partial install it cannot
        notice a name being dropped -- verified: deleting `aws_access_key` failed nothing before
        this test existed. These twenty were each confirmed against the declaring source file.
        """
        assert name in _CONNECTION_ATTRS

    def test_every_constructible_model_takes_a_declared_connection_snapshot(self):
        """A subclass whose ``__post_init__`` does not chain to ``super()`` silently keeps the
        pre-snapshot behaviour, which is exactly the round-2 critical for that provider. Three
        classes were in that state when the snapshot landed."""
        missing: List[str] = []
        for cls in self._constructible_subclasses():
            if "__post_init__" not in cls.__dict__:
                continue  # inherits Model's, which snapshots
            import inspect

            try:
                body = inspect.getsource(cls.__post_init__)
            except (OSError, TypeError):  # pragma: no cover - source unavailable
                continue
            if "super().__post_init__()" not in body:
                missing.append(f"{cls.__module__}.{cls.__name__}")

        assert not missing, (
            "these override __post_init__ without chaining to super(), so they take no "
            "declared-connection snapshot and same_model falls back to live values for them:\n"
            + "\n".join(f"  {name}" for name in sorted(set(missing)))
        )


class TestWhatUpdateModelNoneMeans:
    """`deep_copy(update={"model": None})` keeps the pin. Recorded, not incidental (aicoe-tracker#172).

    The input is genuinely ambiguous and the codebase had never decided: "clear the pin" is a
    plausible reading of an explicit `None`, and "do not change the model" is what the code does.
    Both are defensible, so the risk was that the answer lived in one guard clause and one test
    and could be flipped by a refactor without anyone choosing.

    The decision is **keep the pin**, on the grounds that a pin is a tenancy-sensitive control:
    clearing it hands the copy to whatever the provider's default policy picks, which is a model
    no caller chose. There is deliberately no way to *ask* to be un-pinned -- a caller who wants a
    different model passes one.
    """

    def test_an_explicit_none_does_not_clear_the_pin(self):
        from egpt.agent import Agent

        agent = Agent(model=BareModel(id="P"), telemetry=False)
        copy = agent.deep_copy(update={"model": None})

        assert copy._user_pinned_model is not None, (
            "update={'model': None} means 'do not change the model', not 'clear the pin'"
        )
        assert copy._user_pinned_model.id == "P"

    def test_a_real_model_in_update_still_repins(self):
        """The control: keeping the pin on `None` must not make the copy un-repinnable."""
        from egpt.agent import Agent

        agent = Agent(model=BareModel(id="P"), telemetry=False)
        copy = agent.deep_copy(update={"model": BareModel(id="Q")})

        assert copy._user_pinned_model.id == "Q"

    def test_an_unpinned_agent_stays_unpinned(self):
        """And the other control: `None` in, `None` out, for an agent nobody pinned."""
        from egpt.agent import Agent

        agent = Agent(telemetry=False)
        agent._user_pinned_model = None

        assert agent.deep_copy(update={"model": None})._user_pinned_model is None


class TestThePinIsNotSharedAcrossCopies:
    """A copy derives its own pin object rather than aliasing the original's (aicoe-tracker#172).

    Not cosmetic. `mode_a`'s resolver returns `request.requested` *as the serving model*
    (`providers/mode_a.py:277`), and `_get_client_params` writes environment credentials onto the
    model it is handed (aicoe-tracker#171). With one shared object, one request's client build writes
    `api_key`/`base_url` into the pin every other copy resolves to -- on the hottest path there
    is, since `os/utils.py` deep-copies an agent per request and `team/_utils.py` copies every
    member.
    """

    def test_each_copy_gets_its_own_pin_object(self):
        from egpt.agent import Agent

        agent = Agent(model=BareModel(id="P"), telemetry=False)
        first, second = agent.deep_copy(), agent.deep_copy()

        assert first._user_pinned_model is not agent._user_pinned_model
        assert second._user_pinned_model is not first._user_pinned_model

    def test_mutating_one_copys_pin_does_not_reach_the_others(self):
        """The failure this exists to prevent, written as the scenario rather than as identity."""
        from egpt.agent import Agent

        agent = Agent(model=BareModel(id="P"), telemetry=False)
        first, second = agent.deep_copy(), agent.deep_copy()

        first._user_pinned_model.api_key = "tenant-1-secret"

        assert getattr(second._user_pinned_model, "api_key", None) is None, (
            "one copy's credential must not appear on another copy's pin"
        )
        assert getattr(agent._user_pinned_model, "api_key", None) is None, (
            "nor on the original the copies were made from"
        )

    def test_the_copy_still_compares_equal_to_the_original_pin(self):
        """A copied pin must remain the *same model* by the predicate the seam uses, or the copy
        would read as a fresh caller pin and re-pin itself on the next resolve."""
        from egpt.agent import Agent

        agent = Agent(model=BareModel(id="P"), telemetry=False)
        copy = agent.deep_copy()

        assert same_model(copy._user_pinned_model, agent._user_pinned_model)

    def test_a_team_copy_gets_its_own_pin_too(self):
        """The Team path shares the helper, so it must not drift from the Agent path."""
        from egpt.team import Team

        team = Team(members=[], name="t", model=BareModel(id="TP"), telemetry=False)
        copy = team.deep_copy()

        assert copy._user_pinned_model is not team._user_pinned_model
        assert copy._user_pinned_model.id == "TP"


class TestTheUncopyablePinFallback:
    """`carry_over_pin`'s last resort, which had no coverage (raised in review of #107).

    Returning the original object re-admits the shared-pin hazard the helper exists to close, so
    the branch is a deliberate degrade rather than a silent one: it warns. Raising instead would
    break `deep_copy` outright for an exotic `Model` subclass, which is the worse failure -- a
    shared pin still resolves correctly, it is only unsafe if something mutates it.
    """

    def test_an_uncopyable_pin_falls_back_to_the_original(self, caplog):
        import logging

        from egpt.models.base import carry_over_pin

        class Uncopyable(BareModel):
            def model_copy(self, *args, **kwargs):
                raise RuntimeError("cannot copy this model")

            def __copy__(self):
                raise RuntimeError("cannot copy this model")

        pin = Uncopyable(id="P")
        with caplog.at_level(logging.WARNING):
            carried = carry_over_pin(pin)

        assert carried is pin, "the fallback keeps the run working rather than raising"
        assert any("share the original's pin" in r.getMessage() for r in caplog.records), (
            "the degrade must be visible: it re-admits the hazard this helper closes"
        )

    def test_a_none_pin_is_returned_unchanged(self):
        """An unpinned component must not acquire a pin object from the copy path."""
        from egpt.models.base import carry_over_pin

        assert carry_over_pin(None) is None


class TestTheTeamPathMatchesTheAgentPath:
    """`team/_utils.py:184` is an independent call site of the same helpers, so it can drift.

    Agent's cases above cover mutation isolation and the `update={"model": None}` decision; the
    Team path had only an identity check. Raised in review of #107.
    """

    def _team(self, **kwargs):
        from egpt.team import Team

        return Team(members=[], name="t", telemetry=False, **kwargs)

    def test_mutating_one_team_copys_pin_does_not_reach_the_others(self):
        team = self._team(model=BareModel(id="TP"))
        first, second = team.deep_copy(), team.deep_copy()

        first._user_pinned_model.api_key = "tenant-1-secret"

        assert getattr(second._user_pinned_model, "api_key", None) is None
        assert getattr(team._user_pinned_model, "api_key", None) is None

    def test_an_explicit_none_does_not_clear_a_teams_pin(self):
        team = self._team(model=BareModel(id="TP"))

        copy = team.deep_copy(update={"model": None})

        assert copy._user_pinned_model is not None
        assert copy._user_pinned_model.id == "TP"

    def test_a_real_model_in_update_still_repins_a_team(self):
        team = self._team(model=BareModel(id="TP"))

        copy = team.deep_copy(update={"model": BareModel(id="TQ")})

        assert copy._user_pinned_model.id == "TQ"
