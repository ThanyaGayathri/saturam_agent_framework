from typing import TYPE_CHECKING

from egpt.integrations.models.google.gemini import Gemini

if TYPE_CHECKING:
    from egpt.integrations.models.google.gemini_interactions import GeminiInteractions

__all__ = [
    "Gemini",
    "GeminiInteractions",
]


def __getattr__(name: str):
    """Lazy import of GeminiInteractions to avoid requiring google-genai>=2.0.0 for standard Gemini usage."""
    if name == "GeminiInteractions":
        from egpt.integrations.models.google.gemini_interactions import GeminiInteractions

        return GeminiInteractions
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
