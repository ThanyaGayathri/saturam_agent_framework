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
    "ModelCapabilities",
    "ModelProvider",
    "ModelProviderConfigError",
    "ModelResolveRequest",
    "ModelResolveResult",
    "ProviderMode",
    "get_default_model_provider",
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
            f"A model provider was installed after {len(_RESOLVED_WITHOUT_PROVIDER)} component(s) had already "
            "resolved a model; those keep the model they resolved with. Install the provider before "
            "constructing EgptOS or any Agent/Team, or rebuild them afterwards."
        )
    _DEFAULT_PROVIDER = provider


# Ids of components that resolved a model while no provider was configured. Ids, not the
# objects: holding an Agent here would keep every short-lived per-request copy alive.
_RESOLVED_WITHOUT_PROVIDER: set = set()


def note_resolved_without_provider(component_id: Optional[str]) -> None:
    """Record that a component resolved its model with no provider configured (EGPT-3.1).

    Feeds the ordering warning in ``set_default_model_provider``. Cheap and bounded in the only
    way that matters: a deployment that never installs a provider is the pre-Epic-3 path, and
    the set stops growing the moment one is installed.
    """
    if _DEFAULT_PROVIDER is None and component_id is not None:
        _RESOLVED_WITHOUT_PROVIDER.add(component_id)


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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
