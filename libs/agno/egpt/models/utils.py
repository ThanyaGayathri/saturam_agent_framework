from collections import Counter
from typing import TYPE_CHECKING, Any, Dict, NamedTuple, Optional, Tuple, Union

from egpt.models.base import Model
from egpt.registry import discovery

if TYPE_CHECKING:
    from egpt.registry.registry import Registry


class _ProviderIndices(NamedTuple):
    """The three lookups `_resolve_provider_key` needs, derived from the registry."""

    # key -> lowercased display `provider` string the class reports.
    key_to_provider: Dict[str, str]
    # Serialized `name` -> key, but only for names that identify exactly one provider.
    # Names shared across classes (e.g. "Claude") are ambiguous and omitted, so those fall
    # back to the provider.
    name_to_key: Dict[str, str]
    # Display `provider` string -> key, used as a fallback when `name` is missing or shared.
    provider_to_key: Dict[str, str]


# (registry version, indices). Rebuilt whenever the model table changes rather than
# computed once at import: an out-of-tree provider registered through an entry point must
# be reachable by its serialized (provider, name) pair, not merely by its key. Caching
# these as module constants -- as this file did before EGPT-1.9 -- would make
# get_model_from_dict blind to exactly the providers discovery just made resolvable.
_INDEX_CACHE: Tuple[int, Optional[_ProviderIndices]] = (-1, None)


def _build_indices() -> _ProviderIndices:
    registrations = discovery.entries("model")

    key_to_provider: Dict[str, str] = {}
    key_to_name: Dict[str, str] = {}
    provider_defaults: Dict[str, str] = {}
    for key, registration in registrations.items():
        provider = str(registration.metadata.get("provider_display", key))
        key_to_provider[key] = provider
        key_to_name[key] = str(registration.metadata.get("default_name", key))
        if registration.metadata.get("provider_default"):
            provider_defaults[provider] = key

    name_counts = Counter(key_to_name.values())
    name_to_key = {name: key for key, name in key_to_name.items() if name_counts[name] == 1}

    provider_to_key: Dict[str, str] = {}
    for key, provider in key_to_provider.items():
        if provider not in registrations:  # display strings that already equal a key resolve directly
            provider_to_key.setdefault(provider, key)
    # Where several classes share a display string (e.g. "azure" -> AzureOpenAI /
    # AzureAIFoundry), the row that declared itself the default wins.
    provider_to_key.update(provider_defaults)

    return _ProviderIndices(key_to_provider, name_to_key, provider_to_key)


def _indices() -> _ProviderIndices:
    global _INDEX_CACHE
    cached_version, cached = _INDEX_CACHE
    if cached is not None and cached_version == discovery.version("model"):
        return cached
    built = _build_indices()
    # Read the version *after* building: the first call bootstraps the registry, which
    # bumps it, and caching the pre-bootstrap number would rebuild on every call.
    _INDEX_CACHE = (discovery.version("model"), built)
    return built


def _canonical_provider_display(key: str) -> str:
    """The lowercased display `provider` string a given provider key's class reports."""
    return _indices().key_to_provider.get(key, key)


def _resolve_provider_key(model_provider: Optional[str], model_name: Optional[str] = None) -> str:
    """Resolve a serialized (provider, name) pair to a stable provider key.

    The provider string is authoritative. `name` is used only to disambiguate among classes that
    report the same display `provider` (e.g. AzureOpenAI vs AzureAIFoundry, or OpenAIResponses vs
    the OpenAI-compatible CometAPI). A `name` whose class reports a different provider is treated
    as a user-supplied label and ignored, so e.g. OpenAIChat(name="Gemini") still resolves to
    OpenAI rather than Google.
    """
    indices = _indices()
    provider = (model_provider or "").strip().lower()
    provider_key = provider if provider in indices.key_to_provider else indices.provider_to_key.get(provider)
    name_key = indices.name_to_key.get(model_name) if model_name else None

    if provider_key is None:
        # An empty provider string (older data, or a model whose provider was never set) leaves the
        # name as the only signal. A non-empty but unrecognized provider stays authoritative so an
        # unsupported/custom provider is rejected by _get_model_class rather than being silently
        # re-routed to a built-in class by a colliding name.
        return (name_key or provider) if not provider else provider

    # Otherwise trust the name only when its class reports this same provider string.
    if name_key is not None and _canonical_provider_display(name_key) == provider:
        return name_key
    return provider_key


def _get_model_class(model_id: str, model_provider: str) -> Model:
    # `get` rather than `resolve`: a missing registration and a missing SDK are different
    # failures, and only the first one should fall through to the alias retry. `load`
    # lets the provider's own ImportError -- which names the package to install -- through.
    registration = discovery.get("model", model_provider)
    if registration is None:
        # Allow alias forms (e.g. "azure", "inceptionlabs") to resolve too.
        registration = discovery.get("model", _resolve_provider_key(model_provider))
    if registration is None:
        raise ValueError(f"Model provider '{model_provider}' is not supported.")

    return registration.load()(id=model_id)


def _parse_model_string(model_string: str) -> Model:
    if not model_string or not isinstance(model_string, str):
        raise ValueError(f"Model string must be a non-empty string, got: {model_string}")

    if ":" not in model_string:
        raise ValueError(
            f"Invalid model string format: '{model_string}'. Model strings should be in format '<provider>:<model_id>' e.g. 'openai:gpt-4o'"
        )

    parts = model_string.split(":", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid model string format: '{model_string}'. Model strings should be in format '<provider>:<model_id>' e.g. 'openai:gpt-4o'"
        )

    model_provider, model_id = parts
    model_provider = model_provider.strip().lower()
    model_id = model_id.strip()

    if not model_provider or not model_id:
        raise ValueError(
            f"Invalid model string format: '{model_string}'. Model strings should be in format '<provider>:<model_id>' e.g. 'openai:gpt-4o'"
        )

    return _get_model_class(model_id, _resolve_provider_key(model_provider))


def get_model(model: Union[Model, str, None]) -> Optional[Model]:
    if model is None:
        return None
    elif isinstance(model, Model):
        return model
    elif isinstance(model, str):
        return _parse_model_string(model)
    else:
        raise ValueError("Model must be a Model instance, string, or None")


def get_model_from_dict(model_data: Dict[str, Any]) -> Optional[Model]:
    """Reconstruct a Model from its serialized dict (as produced by ``Model.to_dict``).

    Uses both the serialized ``provider`` and ``name`` to resolve the exact provider class,
    which is required for providers that share a display ``provider`` string (e.g. Azure).
    """
    if not isinstance(model_data, dict):
        raise ValueError("Model data must be a dictionary")

    model_id = model_data.get("id")
    if not model_id:
        raise ValueError(f"Model data is missing an 'id': {model_data}")

    provider_key = _resolve_provider_key(model_data.get("provider"), model_data.get("name"))
    return _get_model_class(model_id, provider_key)


def resolve_model(model_data: Any, registry: Optional["Registry"] = None) -> Any:
    """Reconstruct a model from its serialized config, preferring a registered live instance.

    Rebuilding from a serialized dict only round-trips ``id``/``name``/``provider`` (see
    ``Model.to_dict``), so connection params like ``azure_endpoint``/``base_url`` and any
    credentials are lost. When the model is present in the registry, its live, fully-configured
    instance is reused; otherwise we fall back to rebuilding from the dict (or a ``provider:id``
    string). Values that are neither a model dict nor a string are returned unchanged.

    Shared by Agent and Team reconstruction so both resolve models identically.
    """
    if isinstance(model_data, dict) and "id" in model_data:
        if registry is not None:
            registered_model = registry.get_model(
                model_data["id"],
                provider=model_data.get("provider"),
                name=model_data.get("name"),
            )
            if registered_model is not None:
                return registered_model
        return get_model_from_dict(model_data)
    elif isinstance(model_data, str):
        return get_model(model_data)
    return model_data
