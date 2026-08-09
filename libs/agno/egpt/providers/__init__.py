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
        BaseModelProvider,
        DetailedModelProvider,
        ModelCapabilities,
        ModelProvider,
        ModelProviderConfigError,
        ModelResolveRequest,
        ModelResolveResult,
        ProviderMode,
    )

__all__ = [
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
]

# The process-wide default provider. None means "no mode configured": every agent resolves its
# model exactly as it did before EGPT-3.1.
_DEFAULT_PROVIDER: Optional["ModelProvider"] = None


def set_default_model_provider(provider: Optional["ModelProvider"]) -> None:
    """Install (or clear, with ``None``) the process-wide default provider.

    Deliberately a module global rather than a registry entry: there is exactly one answer per
    process, and a test that installs a fake must be able to restore the previous value in a
    ``finally``. Returns nothing; read it back with ``get_default_model_provider``.
    """
    global _DEFAULT_PROVIDER
    _DEFAULT_PROVIDER = provider


def get_default_model_provider() -> Optional["ModelProvider"]:
    """The process-wide default provider, or ``None`` when none is configured."""
    return _DEFAULT_PROVIDER


# Lazily re-exported from model_provider. Kept as a tuple so __getattr__ stays a membership
# test rather than a chain of comparisons as EGPT-3.2..3.5 add names.
_LAZY_FROM_MODEL_PROVIDER = (
    "BaseModelProvider",
    "DetailedModelProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelProviderConfigError",
    "ModelResolveRequest",
    "ModelResolveResult",
    "ProviderMode",
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_FROM_MODEL_PROVIDER:
        from egpt.providers import model_provider

        return getattr(model_provider, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
