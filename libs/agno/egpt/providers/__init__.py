"""Model provider seam: dual-mode resolution over the model layer (Epic 3).

Names are exported lazily (PEP 562) for the same reason ``egpt/registry/__init__.py`` does it:
Python runs a package ``__init__`` before any submodule, so importing the concrete providers
eagerly here would drag ``egpt.models.base`` and the settings stack into every
``import egpt.providers.model_provider`` -- and, once EGPT-3.3/3.4 land, would give the
package an import-time dependency on modules that resolve integrations.

The ``TYPE_CHECKING`` imports are not redundant with ``__getattr__``: without them mypy sees a
module-level ``__getattr__`` returning ``Any`` and silently degrades every imported name to
``Any`` at the call sites.

``get_default_model_provider`` returns ``None`` until something calls the setter. That default
is what makes EGPT-3.1's seam a no-op on every agent nobody configured: the check in
``egpt/agent/_init.py`` costs one ``is None`` and imports nothing.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from egpt.providers.mode_a import LocalModelProvider
    from egpt.providers.model_provider import (
        AsyncModelProvider,
        BaseModelProvider,
        DetailedModelProvider,
        ModelCapabilities,
        ModelProvider,
        ModelProviderConfigError,
        ModelResolveRequest,
        ModelResolveResult,
        ProviderMode,
        supports_detailed_resolve,
    )

__all__ = [
    "AsyncModelProvider",
    "BaseModelProvider",
    "DetailedModelProvider",
    "LocalModelProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelProviderConfigError",
    "ModelResolveRequest",
    "ModelResolveResult",
    "ProviderMode",
    "get_default_model_provider",
    "reset_provider_state_for_tests",
    "set_default_model_provider",
    "aresolve_via",
    "supports_detailed_resolve",
]

# The process-wide default provider. None means "no mode configured": every agent resolves its
# model exactly as it did before EGPT-3.1.
_DEFAULT_PROVIDER: Optional["ModelProvider"] = None


def set_default_model_provider(provider: Optional["ModelProvider"]) -> None:
    """Install (or clear, with ``None``) the process-wide default provider.

    Deliberately a module global rather than a registry entry: there is exactly one answer per
    process, and a test that installs a fake must be able to restore the previous value in a
    ``finally``. Returns nothing; read it back with ``get_default_model_provider``.

    Warns if any component already resolved its model. Resolution happens once per Agent or
    Team object, so installing a provider after ``EgptOS(agents=[...])`` has been constructed
    -- e.g. from a FastAPI startup hook, the natural place when the provider needs settings or
    a DB handle -- is a silent no-op. Silent is the problem: in a Mode A deployment the
    operator finds out from the egress alert or the bill, not from the logs.
    """
    global _DEFAULT_PROVIDER
    if provider is not None and _RESOLVED_WITHOUT_PROVIDER:
        from egpt.utils.log import log_warning

        log_warning(
            f"A model provider was installed after {_RESOLVED_WITHOUT_PROVIDER} component(s) had already "
            "resolved a model; those keep the model they resolved with. Install the provider before "
            "constructing EgptOS or any Agent/Team, or rebuild them afterwards."
        )
    _DEFAULT_PROVIDER = provider


# How many components resolved a model while no provider was configured. A count, not a set of
# ids: `generate_id_from_name(None)` mints a fresh id per anonymous Agent, so a set would retain
# one string per construction, unbounded, on the no-provider path -- which is every deployment
# that has not adopted Epic 3 yet. The ids bought only the phrasing of a warning that fires at
# most once, and an int says the same thing.
_RESOLVED_WITHOUT_PROVIDER: int = 0


def note_resolved_without_provider(component_id: Optional[str]) -> None:
    """Record that a component resolved its model with no provider configured (EGPT-3.1).

    Feeds the ordering warning in ``set_default_model_provider``. Stops counting the moment a
    provider is installed, because from then on resolution goes through the seam and there is
    nothing to warn about. ``component_id`` is accepted but unused -- kept in the signature so
    a future diagnostic can start recording it without touching the call site.
    """
    global _RESOLVED_WITHOUT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        _RESOLVED_WITHOUT_PROVIDER += 1


def reset_provider_state_for_tests() -> None:
    """Clear the provider global and the resolution counter.

    The counter is process-global and never otherwise reset, so without this a test that
    installs a provider after any earlier test resolved an agent emits the ordering warning --
    noise that reads like a real misconfiguration. For test fixtures only, matching
    ``discovery._reset_for_tests``.
    """
    global _DEFAULT_PROVIDER, _RESOLVED_WITHOUT_PROVIDER
    _DEFAULT_PROVIDER = None
    _RESOLVED_WITHOUT_PROVIDER = 0


def get_default_model_provider() -> Optional["ModelProvider"]:
    """The process-wide default provider, or ``None`` when none is configured."""
    return _DEFAULT_PROVIDER


# Lazily re-exported from model_provider. Kept as a tuple so __getattr__ stays a membership
# test rather than a chain of comparisons as EGPT-3.2..3.5 add names.
_LAZY_FROM_MODEL_PROVIDER = (
    "AsyncModelProvider",
    "BaseModelProvider",
    "DetailedModelProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelProviderConfigError",
    "ModelResolveRequest",
    "ModelResolveResult",
    "ProviderMode",
    "aresolve_via",
    "supports_detailed_resolve",
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_FROM_MODEL_PROVIDER:
        from egpt.providers import model_provider

        return getattr(model_provider, name)
    if name == "LocalModelProvider":
        # EGPT-3.3. Kept out of the tuple above because it lives in its own module: importing
        # it eagerly would pull the settings stack into every `import egpt.providers`.
        from egpt.providers.mode_a import LocalModelProvider

        return LocalModelProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
