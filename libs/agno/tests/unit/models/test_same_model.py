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
        fired -- so it must be read, or an edited model would compare equal to its original.

        The edit goes through `_set_connection_attr`, the one sanctioned way to patch a
        connection attr post-construction -- `Model.__setattr__` blocks a raw second write.
        """
        live = OpenAIResponses(id="gpt-5.5", base_url="https://tenant-a.internal/v1")
        live._set_connection_attr("base_url", "https://tenant-b.internal/v1")

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


# Each entry: (module suffix under egpt.integrations.models, class name, env vars to set so
# resolution succeeds without raising, connection attrs expected to stay `None` on `self`,
# the method that performs resolution, and any extra constructor kwargs a class requires that
# have no env fallback of their own).
#
# Hand-curated, not derived from `_CONNECTION_ATTRS` or a source walk: the whole point is to
# pin the *env var name* each provider actually reads, which nothing else in the tree records
# in one place. A dynamic version would have to parse `getenv(...)` calls out of source, which
# is more fragile than just writing down what EGPT-171's fix touched.
_PROVIDER_MUTATION_CASES = [
    ("requesty.requesty", "Requesty", {"REQUESTY_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("sambanova.sambanova", "Sambanova", {"SAMBANOVA_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "portkey.portkey",
        "Portkey",
        {"PORTKEY_VIRTUAL_KEY": "sentinel"},
        ("virtual_key",),
        "_get_client_params",
        {"portkey_api_key": "dummy-portkey-key"},
    ),
    ("internlm.internlm", "InternLM", {"INTERNLM_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("together.together", "Together", {"TOGETHER_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("vercel.v0", "V0", {"V0_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("openrouter.openrouter", "OpenRouter", {"OPENROUTER_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("inception.inception", "Inception", {"INCEPTION_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "siliconflow.siliconflow",
        "Siliconflow",
        {"SILICONFLOW_API_KEY": "sentinel"},
        ("api_key",),
        "_get_client_params",
        {},
    ),
    (
        "azure.openai_chat",
        "AzureOpenAI",
        {"AZURE_OPENAI_API_KEY": "sentinel", "AZURE_OPENAI_ENDPOINT": "https://sentinel.example/"},
        ("api_key", "azure_endpoint"),
        "_get_client_params",
        {},
    ),
    (
        "azure.claude",
        "Claude",
        {"ANTHROPIC_FOUNDRY_API_KEY": "sentinel", "ANTHROPIC_FOUNDRY_RESOURCE": "sentinel-resource"},
        ("api_key", "resource"),
        "_get_client_params",
        {},
    ),
    ("deepinfra.deepinfra", "DeepInfra", {"DEEPINFRA_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "azure.ai_foundry",
        "AzureAIFoundry",
        {"AZURE_API_KEY": "sentinel", "AZURE_ENDPOINT": "https://sentinel.example/"},
        ("api_key", "azure_endpoint"),
        "_get_client_params",
        {},
    ),
    ("fireworks.fireworks", "Fireworks", {"FIREWORKS_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "cloudflare.cloudflare",
        "Cloudflare",
        {"CLOUDFLARE_API_TOKEN": "sentinel", "CLOUDFLARE_ACCOUNT_ID": "sentinel-account"},
        ("api_key", "account_id", "gateway_id"),
        "_get_client_params",
        {},
    ),
    ("meta.llama_openai", "LlamaOpenAI", {"LLAMA_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "vllm.vllm",
        "VLLM",
        {"VLLM_API_KEY": "sentinel", "VLLM_BASE_URL": "http://sentinel:8000/v1/"},
        ("api_key", "base_url"),
        "_get_client_params",
        {},
    ),
    (
        "tuning_engines.tuning_engines",
        "TuningEngines",
        {"TUNING_ENGINES_API_KEY": "sentinel"},
        ("api_key",),
        "_get_client_params",
        {},
    ),
    ("xai.xai", "xAI", {"XAI_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "ibm.watsonx",
        "WatsonX",
        {"IBM_WATSONX_API_KEY": "sentinel", "IBM_WATSONX_PROJECT_ID": "sentinel-project"},
        ("api_key", "project_id"),
        "_get_client_params",
        {},
    ),
    ("perplexity.perplexity", "Perplexity", {"PERPLEXITY_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("xiaomi.mimo", "MiMo", {"MIMO_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("nvidia.nvidia", "Nvidia", {"NVIDIA_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("aimlapi.aimlapi", "AIMLAPI", {"AIMLAPI_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "aws.claude",
        "Claude",
        {
            "AWS_ACCESS_KEY_ID": "sentinel-access-key",
            "AWS_SECRET_ACCESS_KEY": "sentinel-secret-key",
            "AWS_SESSION_TOKEN": "sentinel-session-token",
            "AWS_REGION": "sentinel-region",
        },
        ("aws_access_key", "aws_secret_key", "aws_session_token", "aws_region"),
        "_get_client_params",
        {},
    ),
    ("anthropic.claude", "Claude", {"ANTHROPIC_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("huggingface.huggingface", "HuggingFace", {"HF_TOKEN": "sentinel"}, ("api_key",), "get_client_params", {}),
    (
        "cerebras.cerebras_openai",
        "CerebrasOpenAI",
        {"CEREBRAS_API_KEY": "sentinel"},
        ("api_key",),
        "_get_client_params",
        {},
    ),
    ("cometapi.cometapi", "CometAPI", {"COMETAPI_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "litellm.litellm_openai",
        "LiteLLMOpenAI",
        {"LITELLM_API_KEY": "sentinel"},
        ("api_key",),
        "_get_client_params",
        {},
    ),
    ("litellm.chat", "LiteLLM", {"LITELLM_API_KEY": "sentinel"}, ("api_key",), "get_request_params", {}),
    ("mistral.mistral", "MistralChat", {"MISTRAL_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("neosantara.neosantara", "Neosantara", {"NEOSANTARA_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    # These resolve independently at each call site rather than through a shared
    # `_get_client_params` (EGPT-171's PR description calls them out by name), so `get_client()`
    # (and, for bedrock, `get_async_client()` too -- a separate call site with its own history of
    # this exact bug) is the method under test rather than a params-building helper.
    ("cohere.chat", "Cohere", {"CO_API_KEY": "sentinel"}, ("api_key",), "get_client", {}),
    (
        "aws.bedrock",
        "AwsBedrock",
        {
            "AWS_ACCESS_KEY_ID": "sentinel-access-key",
            "AWS_SECRET_ACCESS_KEY": "sentinel-secret-key",
            "AWS_SESSION_TOKEN": "sentinel-session-token",
            "AWS_REGION": "sentinel-region",
        },
        ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "aws_region"),
        "get_client",
        {},
    ),
    (
        "aws.bedrock",
        "AwsBedrock",
        {
            "AWS_ACCESS_KEY_ID": "sentinel-access-key",
            "AWS_SECRET_ACCESS_KEY": "sentinel-secret-key",
            "AWS_SESSION_TOKEN": "sentinel-session-token",
            "AWS_REGION": "sentinel-region",
        },
        ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "aws_region"),
        "get_async_client",
        {},
    ),
    ("google.gemini", "Gemini", {"GOOGLE_API_KEY": "sentinel"}, ("api_key",), "get_client", {}),
    (
        "google.gemini_interactions",
        "GeminiInteractions",
        {"GOOGLE_API_KEY": "sentinel"},
        ("api_key",),
        "get_client",
        {},
    ),
    # Added to close the coverage gap a second review round (2026-08-20) found: 11 of the ~44
    # converted providers, including OpenAIChat itself -- the shared base ~24 others delegate
    # through via super()._get_client_params(...) -- had no case here.
    ("cerebras.cerebras", "Cerebras", {"CEREBRAS_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("dashscope.dashscope", "DashScope", {"DASHSCOPE_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("deepseek.deepseek", "DeepSeek", {"DEEPSEEK_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("groq.groq", "Groq", {"GROQ_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "langdb.langdb",
        "LangDB",
        {
            "LANGDB_API_KEY": "sentinel",
            "LANGDB_PROJECT_ID": "sentinel-project",
            "LANGDB_API_BASE_URL": "https://sentinel.example/v1",
        },
        ("api_key", "project_id", "base_host_url"),
        "_get_client_params",
        {},
    ),
    ("meta.llama", "Llama", {"LLAMA_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("minimax.minimax", "MiniMax", {"MINIMAX_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("moonshot.moonshot", "MoonShot", {"MOONSHOT_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("n1n.n1n", "N1N", {"N1N_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    ("openai.chat", "OpenAIChat", {"OPENAI_API_KEY": "sentinel"}, ("api_key",), "_get_client_params", {}),
    (
        "openrouter.responses",
        "OpenRouterResponses",
        {"OPENROUTER_API_KEY": "sentinel"},
        ("api_key",),
        "_get_client_params",
        {},
    ),
]


class TestNoProviderMutatesConnectionAttrsAfterClientBuild:
    """The gap two reviews of PR #108 (EGPT-171) flagged: only ``OpenAIResponses`` and
    ``Neosantara`` got an explicit "does not mutate self" regression assertion, out of ~44
    changed providers. Everything else relied on the one-time sweep the PR described, not on a
    test -- so a new provider (or an edit to an existing one) reintroducing the mutation would
    pass CI silently. This is the runtime backstop `same_model`'s docstring now points to,
    alongside the structural guard on `Model.__setattr__` that raises the moment a provider
    tries to reintroduce the mutation, rather than only failing here on the next test run.

    Each case sets the env var(s) a provider falls back to, builds an instance, snapshots the
    connection attrs before calling whichever method actually performs resolution, and asserts
    those attrs are unchanged afterward -- the exact shape of the bug EGPT-171 fixed. Comparing
    against a snapshot (rather than asserting ``None``) covers both flavors of provider in the
    tree: most declare ``api_key: Optional[str] = None`` and resolve lazily inside the method
    under test, but a few (e.g. ``langdb``, ``tuning_engines``, ``cometapi``) resolve eagerly via
    ``field(default_factory=lambda: getenv(...))``, so the attr is already non-``None`` by the
    time this test constructs the instance.
    """

    @pytest.mark.parametrize(
        "module_suffix,cls_name,env,attrs,call_method,extra_kwargs",
        _PROVIDER_MUTATION_CASES,
        ids=[f"{case[0]}.{case[1]}:{case[4]}" for case in _PROVIDER_MUTATION_CASES],
    )
    def test_resolution_does_not_write_back_onto_self(
        self, module_suffix, cls_name, env, attrs, call_method, extra_kwargs, monkeypatch
    ):
        import importlib

        try:
            module = importlib.import_module(f"egpt.integrations.models.{module_suffix}")
            cls = getattr(module, cls_name)
        except Exception as e:
            pytest.skip(f"{module_suffix}.{cls_name} not importable in this environment: {e}")

        for var, value in env.items():
            monkeypatch.setenv(var, value)

        instance = cls(id="test-model", **extra_kwargs)
        before = {attr: getattr(instance, attr, None) for attr in attrs}

        getattr(instance, call_method)()

        for attr in attrs:
            assert getattr(instance, attr, None) == before[attr], (
                f"{cls_name}.{call_method}() must not mutate {attr} on self (EGPT-171 regression)"
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
        """The double defines only `__copy__`, deliberately.

        An earlier version also defined `model_copy`, which meant it exited through the pydantic
        branch -- a path no in-tree `Model` reaches, since they are all dataclasses. The test read
        as covering the fallback while the live route stayed untested: the same shape as this
        file's earlier `BareModel.api_key` gap. Raised in review of #107.
        """
        import logging

        from egpt.models.base import carry_over_pin

        class Uncopyable(BareModel):
            def __copy__(self):
                raise RuntimeError("cannot copy this model")

        pin = Uncopyable(id="P")
        assert not hasattr(pin, "model_copy"), "the double must take the copy.copy path every real Model takes"

        with caplog.at_level(logging.WARNING):
            carried = carry_over_pin(pin)

        assert carried is pin, "the fallback keeps the run working rather than raising"
        assert any("share the original's pin" in r.getMessage() for r in caplog.records), (
            "the degrade must be visible: it re-admits the hazard this helper closes"
        )

    def test_a_bad_model_copy_signature_still_gets_a_real_copy(self, caplog):
        """A pydantic-shaped subclass whose `model_copy` omits `deep=` must not degrade.

        Ordering `model_copy` first meant its `TypeError` was swallowed and the *shared* original
        returned -- this helper's own hazard, in a case where `copy.copy` works fine. Raised in
        review of #107.
        """
        import logging

        from egpt.models.base import carry_over_pin

        class BadSignature(BareModel):
            def model_copy(self, *, update=None):  # no `deep=`
                raise TypeError("model_copy() got an unexpected keyword argument 'deep'")

        pin = BadSignature(id="P")
        with caplog.at_level(logging.WARNING):
            carried = carry_over_pin(pin)

        assert carried is not pin, "copy.copy succeeds, so the shared-object degrade must not fire"
        assert same_model(carried, pin)
        assert not any("share the original's pin" in r.getMessage() for r in caplog.records), (
            "nothing degraded, so nothing should have warned"
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
