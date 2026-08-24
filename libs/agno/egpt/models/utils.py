from collections import Counter
from typing import TYPE_CHECKING, Any, Dict, NamedTuple, Optional, Tuple, Union

from egpt.models.base import Model
from egpt.registry import discovery

if TYPE_CHECKING:
    from egpt.registry.registry import Registry


class _ProviderIndices(NamedTuple):
    """The three lookups `resolve_provider_key` needs, derived from the registry."""

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


def resolve_provider_key(model_provider: Optional[str], model_name: Optional[str] = None) -> str:
    """Resolve a serialized (provider, name) pair to a stable provider key.

    The provider string is authoritative. `name` is used only to disambiguate among classes that
    report the same display `provider` (e.g. AzureOpenAI vs AzureAIFoundry, or OpenAIResponses vs
    the OpenAI-compatible CometAPI). A `name` whose class reports a different provider is treated
    as a user-supplied label and ignored, so e.g. OpenAIChat(name="Gemini") still resolves to
    OpenAI rather than Google.

    Public because the component-config validator (``egpt.registry.schema``) has to answer
    "is this provider resolvable?" without importing the provider. Re-deriving the alias
    handling there off ``discovery.entries("model")`` metadata would be a second
    implementation of this function, and the two would drift.
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
        registration = discovery.get("model", resolve_provider_key(model_provider))
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

    return _get_model_class(model_id, resolve_provider_key(model_provider))


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

    provider_key = resolve_provider_key(model_data.get("provider"), model_data.get("name"))
    model = _get_model_class(model_id, provider_key)
    # A serialized OpenAI-schema proxy carries no endpoint (`Model.to_dict` stores only
    # id/name/provider, deliberately -- `to_dict` also feeds telemetry, and an internal hostname
    # must not leave the deployment). Such a model would otherwise fall back to the vendor SDK
    # default, i.e. leave the VPC, so refuse the placeholder credential that hides the problem:
    # None restores `OpenAIChat`'s own env fallback, and Mode A reconfigures both explicitly.
    # getattr, not attribute access, for the read: `api_key` is declared by the concrete
    # OpenAI-schema classes, not by the `Model` base this function is typed against. The write
    # goes through `_set_connection_attr` -- `Model.__setattr__` blocks a second raw assignment
    # to a connection attr, and this is the one sanctioned exception to that guard.
    if getattr(model, "is_openai_compatible_proxy", False) and getattr(model, "api_key", None) == "not-provided":
        model._set_connection_attr("api_key", None)
    return model


def proxy_is_missing_its_endpoint(model: Any) -> bool:
    """Whether ``model`` is a proxy that would silently reach **public OpenAI** without an endpoint.

    True means the rebuilt client sends this tenant's prompts to ``https://api.openai.com/v1``
    under whatever ``OPENAI_API_KEY`` the process holds, with no error to notice.

    The class survives a ``to_dict``/``get_model_from_dict`` round trip; the endpoint does not.
    ``Model.to_dict`` persists only ``{id, name, provider}`` deliberately -- it also feeds
    ``agent/_telemetry.py``, so widening it would ship internal hostnames off-box, and
    ``test_to_dict_does_not_carry_base_url_so_mode_a_must_reconfigure_it`` guards that.

    **Only bare ``OpenAILike`` qualifies, and narrowing to it is the point.** An earlier version
    fired on anything with ``is_openai_compatible_proxy`` and no construction-time endpoint, which
    is every subclass -- ``VLLM``, ``AzureOpenAI``, ``Cloudflare``, ``LangDB`` -- because they
    inherit the flag. Those four resolve their endpoint *lazily* inside their own
    ``_get_client_params`` from environment or derived config, so the attributes this function can
    see are ``None`` on a perfectly-configured private deployment. Measured, with the standard
    Azure environment set::

        get_model_from_dict({"id": ..., "name": "AzureOpenAI", "provider": "Azure"})
            construction-time base_url / azure_endpoint : None / None
            client actually connects to                 : https://priv.openai.azure.com/openai/

    Refusing that restore broke every AgentOS request touching a stored Azure/vLLM/LangDB/
    Cloudflare agent -- for deployments that had no public egress at all.

    And with *nothing* configured, those four do not leak either; they raise
    ``ModelAuthenticationError`` naming the variable to set (``VLLM_API_KEY``,
    ``CLOUDFLARE_API_TOKEN``, ...). Loud and safe. Bare ``OpenAILike`` is the one that overrides no
    client params, inherits the OpenAI SDK default, and clears its ``"not-provided"`` api_key so
    the process key applies -- silent and unsafe. That asymmetry is the whole justification for
    this guard, so the test for it is "does this class resolve its own endpoint", not "does it
    have one right now".

    ``azure_endpoint`` counts alongside ``base_url`` for the same reason it always did: a caller
    who set either knows where to connect.
    """
    if not getattr(model, "is_openai_compatible_proxy", False):
        return False
    if getattr(model, "base_url", None) or getattr(model, "azure_endpoint", None):
        return False
    # A subclass that overrides `_get_client_params` resolves its own endpoint (and raises its own
    # error when it cannot). Only the class that inherits the OpenAI SDK default silently lands on
    # public OpenAI, so only that class is refused. Checked on the type rather than the instance:
    # this asks what the class *will* do at client-build time, which no attribute can answer.
    # Walk the MRO up to (but not including) OpenAILike: a subclass that defines its own
    # `_get_client_params` resolves its own endpoint. OpenAILike's own definition -- if any -- does
    # not count, because inheriting *that* is exactly the leaking case.
    for klass in type(model).__mro__:
        if klass.__name__ == "OpenAILike":
            break
        if "_get_client_params" in klass.__dict__:
            return False
    return True


def _refuse_endpointless_proxy(model: Any, described_as: str) -> None:
    """Raise if ``model`` would silently reach public OpenAI. Shared by both restore branches.

    One helper rather than a check per branch, because the string spelling
    (``"openai-like:qwen"``) and the dict spelling of the same model must answer the same way --
    they did not, and the string form restored pointing at ``https://api.openai.com/v1`` while the
    dict form was refused. See :func:`proxy_is_missing_its_endpoint` for why only bare
    ``OpenAILike`` qualifies.
    """
    if not proxy_is_missing_its_endpoint(model):
        return
    # "Model" first, so `registry/schema.py::_hydration_error_path` attributes this to the `model`
    # field rather than to the config as a whole, should a caller opt in from a validation context.
    raise ValueError(
        f"Model {described_as!r} cannot be restored: it is a bare OpenAI-schema proxy and the "
        "serialized form carries no endpoint, so the rebuilt client would connect to "
        "https://api.openai.com/v1 under this process's OPENAI_API_KEY rather than the server it "
        "was saved against. Model.to_dict stores only id/name/provider because it also feeds "
        "telemetry. Supply base_url when rebuilding, register the live model instance, or pass "
        "require_endpoint=False if an unconfigured proxy is genuinely intended."
    )


def resolve_model(model_data: Any, registry: Optional["Registry"] = None, *, require_endpoint: bool = True) -> Any:
    """Reconstruct a model from its serialized config, preferring a registered live instance.

    Rebuilding from a serialized dict only round-trips ``id``/``name``/``provider`` (see
    ``Model.to_dict``), so connection params like ``azure_endpoint``/``base_url`` and any
    credentials are lost. When the model is present in the registry, its live, fully-configured
    instance is reused; otherwise we fall back to rebuilding from the dict (or a ``provider:id``
    string). Values that are neither a model dict nor a string are returned unchanged.

    Shared by Agent and Team reconstruction so both resolve models identically.

    With ``require_endpoint=True``, raises ``ValueError`` when the rebuilt model would silently
    reach public OpenAI -- see :func:`proxy_is_missing_its_endpoint`, which is the single place
    this rationale lives. Applied identically to the dict and string spellings.

    **Defaults on; the write-side validator opts out.** ``resolve_model`` is not only the
    restore-to-run path: ``registry/schema.py::_dry_run`` reaches it through ``spec.hydrate`` ->
    ``from_dict`` to prove a config hydrates, which is the components-API *write-side* validator.
    Refusing there would make ``OpenAILike`` uncreatable rather than merely unrestorable, and
    unreachable-by-design at that -- ``Model.to_dict``'s ``{id, name, provider}`` triple *is* the
    config schema, so there is no endpoint a caller could add to satisfy the check.

    The polarity matters and is the whole point: defaulting *off* would leave every real
    restore path silently unprotected, which is the bug rather than a fix. So the guard is on
    by default and exactly one caller -- ``component_spec()``'s validator lambda, which is
    proving shape rather than preparing to run -- turns it off.

    The check is not in ``get_model_from_dict`` for a related reason: that function is the
    general-purpose reconstructor and must keep rebuilding *any* provider's class from its
    identity triple, which ``test_to_dict_round_trip_preserves_class`` requires for every key.
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
        model = get_model_from_dict(model_data)
        if require_endpoint and model is not None:
            _refuse_endpointless_proxy(model, model_data["id"])
        return model
    elif isinstance(model_data, str):
        # Guarded identically to the dict branch above. `validate_config` accepts both spellings,
        # and leaving this one unchecked meant `"openai-like:qwen"` restored against public OpenAI
        # while the dict spelling of the same model was refused -- the leak this guard exists to
        # close, still open through its sibling.
        model = get_model(model_data)
        if require_endpoint and model is not None:
            _refuse_endpointless_proxy(model, model_data)
        return model
    return model_data
