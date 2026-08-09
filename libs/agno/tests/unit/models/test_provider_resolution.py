"""Tests for provider-key resolution used when reconstructing models from a serialized dict.

These guard the round-trip ``Model.to_dict() -> get_model_from_dict()`` so that a model loaded
from the database (e.g. the components table) rebuilds as the correct provider class, and so a
single unsupported/misconfigured provider no longer needs special-casing per call site.
"""

import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key-for-testing")

from egpt.models.base import Model
from egpt.models.utils import (
    _canonical_provider_display,
    _get_model_class,
    get_model_from_dict,
    resolve_model,
    resolve_provider_key,
)
from egpt.registry.discovery import declarations
from tests.unit._layer_guards import (
    check_shim_parity,
    discover_subclasses,
    package_root,
    registered_defining_classes,
)

# `declarations`, not `entries`: this file guards what *this repository* ships. Reading
# `entries` would fold in any third-party provider registered through an egpt.models entry
# point, so installing an unrelated plugin -- including this suite's own AC4 fixture --
# would fail the layout guards below.
MODEL_DECLARATIONS = declarations("model")
ALL_PROVIDER_KEYS = sorted(MODEL_DECLARATIONS)


def _construct_or_skip(key):
    """Construct a provider's model, or skip if its optional SDK is not installed.

    The base test env (the ``dev`` extra) ships only the ``openai`` SDK, while the demo env ships
    all of them. Skipping on ImportError keeps these tests correct in any environment instead of
    hardcoding which providers are installable; resolution itself (no construction) is covered for
    every provider by the parametrized resolution tests below.
    """
    try:
        return _get_model_class("test-id", key)
    except ImportError as e:
        pytest.skip(f"optional SDK for '{key}' not installed: {e}")


@pytest.mark.parametrize("key", ALL_PROVIDER_KEYS)
def test_to_dict_round_trip_preserves_class(key):
    """Every installable provider rebuilds as the same class through to_dict/from_dict."""
    model = _construct_or_skip(key)
    rebuilt = get_model_from_dict(model.to_dict())
    assert type(rebuilt) is type(model)
    assert rebuilt.id == "test-id"


@pytest.mark.parametrize("key", ALL_PROVIDER_KEYS)
def test_canonical_provider_display_matches_class(key):
    """Drift guard: the provider-display override table matches what each class actually reports.

    Keeps name-vs-provider disambiguation correct if a provider's display string ever changes.
    """
    model = _construct_or_skip(key)
    assert _canonical_provider_display(key) == (model.provider or "").strip().lower()


@pytest.mark.parametrize(
    "provider, name, expected_key",
    [
        # The crash from the components table: Azure models all report provider "Azure".
        ("Azure", "AzureOpenAI", "azure-openai"),
        ("Azure", "AzureAIFoundry", "azure-ai-foundry"),
        ("AzureFoundry", "AzureFoundryClaude", "azure-foundry-claude"),
        # SDK-gated providers, validated via their serialized (provider, name) pairs.
        ("AwsBedrock", "AwsBedrock", "aws-bedrock"),
        ("AwsBedrock", "AwsBedrockAnthropicClaude", "aws-claude"),
        ("Cerebras", "Cerebras", "cerebras"),
        ("CerebrasOpenAI", "CerebrasOpenAI", "cerebras-openai"),
        ("Cohere", "cohere", "cohere"),
        ("Groq", "Groq", "groq"),
        ("HuggingFace", "HuggingFace", "huggingface"),
        ("IBM", "WatsonX", "ibm"),
        ("LiteLLM", "LiteLLM", "litellm"),
        ("LiteLLM", "LiteLLMOpenAI", "litellm-openai"),
        ("Llama", "Llama", "meta"),
        ("LlamaOpenAI", "LlamaOpenAI", "llama-openai"),
        ("Mistral", "MistralChat", "mistral"),
        ("Ollama", "Ollama", "ollama"),
        ("Ollama", "OllamaResponses", "ollama-responses"),
        ("Portkey", "Portkey", "portkey"),
        # Anthropic-backed providers (anthropic SDK not in base test env).
        ("Anthropic", "Claude", "anthropic"),
        ("AzureFoundry", "AzureFoundryClaude", "azure-foundry-claude"),
        ("VertexAI", "Claude", "vertexai-claude"),
    ],
)
def test_resolve_sdk_gated_providers(provider, name, expected_key):
    assert resolve_provider_key(provider, name) == expected_key


@pytest.mark.parametrize(
    "provider, name, expected_key",
    [
        # Display-string providers that differ from the registry key resolve via alias,
        # including the legacy/string path where no name is serialized.
        ("Azure", None, "azure-openai"),
        ("azure", None, "azure-openai"),
        ("InceptionLabs", None, "inception"),
        ("VertexAI", None, "vertexai-claude"),
        ("LlamaCpp", None, "llama-cpp"),
        ("Xiaomi MiMo", None, "xiaomi"),
        # CometAPI inherits provider "OpenAI" from OpenAILike; its name disambiguates it.
        ("OpenAI", "CometAPI", "cometapi"),
        # Tuning Engines: provider/name carry a space; both the name and string paths resolve.
        ("Tuning Engines", "Tuning Engines", "tuning-engines"),
        ("Tuning Engines", None, "tuning-engines"),
        # Plain providers whose display string already equals the key.
        ("openai", None, "openai"),
        ("anthropic", None, "anthropic"),
    ],
)
def test_resolve_provider_aliases(provider, name, expected_key):
    assert resolve_provider_key(provider, name) == expected_key


@pytest.mark.parametrize(
    "provider, name, expected_key",
    [
        # A user-supplied name that collides with another provider's default name must NOT
        # override the provider string. The name only disambiguates within the same family.
        ("OpenAI", "Gemini", "openai"),
        ("OpenAI", "Groq", "openai"),
        ("Groq", "Gemini", "groq"),
        ("Anthropic", "OpenAIChat", "anthropic"),
        # Within-family disambiguation still works (these share a display provider string).
        ("OpenAI", "OpenAIChat", "openai-chat"),
        # "OpenAIResponses" is the default name for both the "openai" and "openai-responses" keys
        # (same class), so it resolves to the canonical "openai" key -- still the OpenAIResponses class.
        ("OpenAI", "OpenAIResponses", "openai"),
        ("Azure", "AzureAIFoundry", "azure-ai-foundry"),
    ],
)
def test_name_does_not_override_conflicting_provider(provider, name, expected_key):
    assert resolve_provider_key(provider, name) == expected_key


def test_user_named_model_reconstructs_correct_provider():
    """Regression: OpenAIChat(name="Gemini") must not rebuild as the Google Gemini class."""
    pytest.importorskip("openai")  # openai is an optional extra, not a base dependency
    rebuilt = get_model_from_dict({"provider": "OpenAI", "name": "Gemini", "id": "gpt-4o"})
    assert type(rebuilt).__name__ != "Gemini"
    assert rebuilt.provider == "OpenAI"


@pytest.mark.parametrize(
    "provider, name",
    [
        ("my-gateway", "Gemini"),  # unknown provider must not be re-routed by a colliding name
        ("totally-custom", "OpenAIChat"),
    ],
)
def test_unrecognized_provider_not_overridden_by_name(provider, name):
    """A non-empty unsupported provider stays authoritative and is rejected, not name-routed."""
    assert resolve_provider_key(provider, name) == provider
    with pytest.raises(ValueError, match="is not supported"):
        get_model_from_dict({"provider": provider, "name": name, "id": "x"})


def test_empty_provider_falls_back_to_name():
    """With no provider string, the serialized name is the only signal and is used."""
    assert resolve_provider_key("", "Groq") == "groq"
    assert resolve_provider_key(None, "OpenAIChat") == "openai-chat"


def test_unsupported_provider_raises():
    with pytest.raises(ValueError, match="is not supported"):
        _get_model_class("some-id", "definitely-not-a-provider")


def test_get_model_from_dict_requires_id():
    with pytest.raises(ValueError, match="missing an 'id'"):
        get_model_from_dict({"provider": "openai"})


class _FakeRegistry:
    """Minimal registry stub exposing get_model() the way Registry does."""

    def __init__(self, model):
        self._model = model

    def get_model(self, model_id, provider=None, name=None):
        if getattr(self._model, "id", None) != model_id:
            return None
        if provider is not None and getattr(self._model, "provider", None) != provider:
            return None
        if name is not None and getattr(self._model, "name", None) != name:
            return None
        return self._model


def test_resolve_model_prefers_registry_instance():
    """A dict that matches a registered model resolves to the live instance, not a rebuild."""

    class _M:
        id = "gpt-5.5"
        provider = "OpenAI"
        name = "OpenAIResponses"

    live = _M()
    registry = _FakeRegistry(live)

    resolved = resolve_model({"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}, registry)
    assert resolved is live


def test_resolve_model_falls_back_to_dict_when_not_registered():
    """When the registry has no match, resolve_model rebuilds from the dict (unchanged behavior)."""
    registry = _FakeRegistry(None)
    rebuilt = resolve_model({"id": "gpt-4o", "provider": "OpenAI", "name": "OpenAIResponses"}, registry)
    assert isinstance(rebuilt, Model)
    assert rebuilt.id == "gpt-4o"


def test_resolve_model_without_registry_rebuilds_from_dict():
    rebuilt = resolve_model({"id": "gpt-4o", "provider": "OpenAI", "name": "OpenAIResponses"})
    assert isinstance(rebuilt, Model)
    assert rebuilt.id == "gpt-4o"


def test_resolve_model_from_string():
    rebuilt = resolve_model("openai:gpt-4o")
    assert isinstance(rebuilt, Model)
    assert rebuilt.id == "gpt-4o"


def test_resolve_model_passes_through_unhandled_values():
    """Values that are neither a model dict nor a string are returned unchanged."""
    sentinel = object()
    assert resolve_model(sentinel) is sentinel
    assert resolve_model({"no": "id"}) == {"no": "id"}


# Abstract/intermediate Model subclasses that are not concrete providers and so must not appear
# in the model declarations. Add a class here only after a deliberate decision that it is not a
# user-selectable provider. Keyed by (defining_module, class_name).
#
# OpenAILike was listed here until EGPT-3.3. Treating it as "intermediate infra" was the bug:
# it is directly selectable (it is how you point EGPT at any self-hosted OpenAI-schema server),
# and with no declaration row a serialized OpenAILike rebuilt as OpenAIResponses -- see
# test_openai_like_round_trip_preserves_class below.
_NON_PROVIDER_MODEL_CLASSES: set = set()


# Names a shim exports that its target deliberately does not. Each entry needs a reason: the
# default expectation is that a shim mirrors its target exactly.
#
# egpt.integrations.models.openai omits OpenAILike on purpose -- it subclasses OpenAIChat, so
# re-exporting it from that package deadlocks on any import order reaching openai_like first.
# The pre-EGPT-1.4 egpt.models.openai did export it, and the shim exists to preserve that
# surface, so the extra name here is the shim doing its job rather than drift.
_SHIM_EXTRA_EXPORTS = {"openai": {"OpenAILike"}}

# Names a shim declares but deliberately leaves to ``__getattr__`` rather than binding
# eagerly, because the target provides them lazily or behind an optional-dependency guard.
# Anything not listed here that appears in ``__all__`` without a matching import is drift.
_SHIM_LAZY_EXPORTS = {
    # google defers GeminiInteractions via a module-level __getattr__ so that importing the
    # package does not require google-genai>=2.0.0; binding it in the shim would force it.
    "google": {"GeminiInteractions"},
    # These three are re-exported by their target behind a try/except ImportError stub. The
    # shims leave them to __getattr__ so the optional SDK is not opened at import time.
    "aws": {"Claude"},
    "cerebras": {"CerebrasOpenAI"},
    "meta": {"LlamaOpenAI"},
}

_PROVIDER_COUNT = 40


def _models_root():
    """Provider implementations live in the integrations layer as of EGPT-1.4."""
    root = package_root() / "egpt" / "integrations" / "models"
    assert root.is_dir(), f"models root not found: {root}"
    return root


def _deprecated_models_root():
    """The pre-EGPT-1.4 `egpt/models` tree, which must now hold only compatibility shims."""
    root = package_root() / "egpt" / "models"
    assert root.is_dir(), f"deprecated models root not found: {root}"
    return root


def _declared_defining_classes(root):
    """Resolve every declared model row to the (defining_module, class) it points at.

    Uses the shared helper rather than a local copy: it follows re-export chains to a
    fixpoint, which a single hop does not (see ``_layer_guards.registered_defining_classes``).
    """
    return registered_defining_classes(root, ((reg.module, reg.attr) for reg in MODEL_DECLARATIONS.values()))


def test_every_model_subclass_is_registered():
    """Guard against drift: every concrete provider on disk must have a declaration row.

    CI fails on the same PR that adds a new Model subclass without a row, so the declarations
    stay the single source of truth without per-provider runtime wiring.
    """
    root = _models_root()
    discovered = discover_subclasses(root, "Model", include_init=False)
    covered = _declared_defining_classes(root) | _NON_PROVIDER_MODEL_CLASSES

    # Without this the guard degrades silently: if the scan root ever stops matching where
    # providers actually live, `discovered` is empty, the assertion below can never fire, and the
    # test reports PASS while checking nothing. Exactly what happened when EGPT-1.4 moved the
    # providers into the integrations layer.
    assert len(discovered) > _PROVIDER_COUNT, (
        f"Only {len(discovered)} Model subclasses discovered under {root}; expected the full "
        "provider set. The scan root is probably stale, which makes this guard vacuous."
    )

    missing = sorted(f"{name} ({module})" for (module, name) in discovered - covered)
    assert not missing, (
        "Model subclasses missing from the model declarations: "
        + ", ".join(missing)
        + ". Add each to egpt/registry/_declarations/models.py (or to the test allowlist if it is "
        "not a user-selectable provider)."
    )


def test_deprecated_models_tree_holds_only_shims():
    """No provider implementation may be added back under the deprecated `egpt/models` tree.

    That tree is compatibility shims only (EGPT-1.4). A Model subclass defined there would be
    invisible to `test_every_model_subclass_is_registered`, which now scans the integrations
    layer, so it could ship unregistered.

    `__init__.py` files are scanned too: nothing stops a provider being defined directly in
    one, and skipping them would wave that through.
    """
    shims_root = _deprecated_models_root()
    stragglers = discover_subclasses(shims_root, "Model")
    assert not stragglers, (
        "Model subclasses found under the deprecated egpt/models tree: "
        + ", ".join(sorted(f"{name} ({module})" for (module, name) in stragglers))
        + ". Provider implementations belong under egpt/integrations/models/."
    )

    shim_packages = [p for p in shims_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()]
    assert len(shim_packages) > _PROVIDER_COUNT, (
        f"Only {len(shim_packages)} shim packages found under {shims_root}; expected the full "
        "provider set. The deprecated tree is stale, which makes this guard vacuous."
    )


def test_deprecated_shims_reexport_everything_their_target_exports():
    """Each `egpt/models/<provider>` shim must export what its integrations target exports.

    The EGPT-1.4 shims were written provider by provider, and two (`azure`, `langdb`) were missed
    while their docstrings still claimed explicit re-exports -- prose is not checkable, so the
    claim and the code drifted apart. `azure` silently degraded to binding zero names, breaking
    star-imports, `dir()` and static type resolution on a path that is supposed to be a
    drop-in replacement.

    The check is two-sided: a shim must declare what its target declares *and* actually bind
    everything it declares. An `__all__`-to-`__all__` comparison alone would not have caught
    azure, whose stale `__all__` matched its target perfectly while the module bound nothing.
    """
    checked, problems = check_shim_parity(
        _models_root(),
        _deprecated_models_root(),
        extra_exports=_SHIM_EXTRA_EXPORTS,
        lazy_exports=_SHIM_LAZY_EXPORTS,
    )

    assert checked > _PROVIDER_COUNT, (
        f"Only {checked} shim/target pairs compared; expected the full provider set. One of the "
        "two roots is probably stale, which makes this guard vacuous."
    )
    assert not problems, "Compatibility shims out of sync with their targets:\n" + "\n".join(problems)
