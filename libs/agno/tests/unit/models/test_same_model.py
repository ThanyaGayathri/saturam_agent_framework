"""``same_model`` (EGPT-171 follow-up to EGPT-3.5).

``same_model`` answers one question for ``Agent.deep_copy`` / ``Team.deep_copy``: did the caller
choose a genuinely different model, or hand back the one already in use? Getting it wrong is not a
bookkeeping error -- a provider honouring pins returns ``request.requested`` unchanged, so a wrong
"same" keeps the *previous* tenant's object as the pin and the copy then serves traffic from that
tenant's endpoint under that tenant's key.

It compares connection attributes **live** -- safe because no in-tree provider mutates them after
construction. Providers used to resolve credentials/endpoints from the environment and write them
onto ``self`` inside ``_get_client_params``, which required a construction-time snapshot to compare
against instead of live values; that mutation is gone (EGPT-171), so the snapshot is too.

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
    """Providers used to resolve credentials inside ``_get_client_params`` and write them onto
    ``self``; this class guards against a regression back to that (EGPT-171)."""

    def test_an_identical_reconstruction_still_matches_after_a_client_build(self, monkeypatch):
        """The round-2 critical. ``get_client()`` must not mutate ``self.api_key``, so a bare
        model still compares equal to its own reconstruction after serving a request -- no
        construction-time snapshot needed, live comparison is enough."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        live = OpenAIResponses(id="gpt-5.5")
        live.get_client()
        assert live.api_key is None, "get_client() must not mutate api_key on self"

        assert same_model(OpenAIResponses(id="gpt-5.5"), live) is True

    def test_an_explicitly_configured_model_still_matches_after_a_client_build(self, monkeypatch):
        """An explicit value is never overwritten by the environment and must keep comparing
        equal for the model's whole life."""
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
        """Regression guard: even if ``get_client()`` were to leak an env value onto ``self``
        again, a bare model must still not compare equal to a tenant-configured one -- asserted
        post-``get_client()`` so a future regression here is caught too."""
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
