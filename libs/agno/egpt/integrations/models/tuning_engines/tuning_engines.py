from dataclasses import dataclass, field
from os import getenv
from typing import Any, Dict, Optional

from egpt.integrations.models.openai_like import OpenAILike


@dataclass
class TuningEngines(OpenAILike):
    """
    Class for interacting with Tuning Engines as an OpenAI-compatible endpoint.

    Tuning Engines provides a governed AI control plane with OpenAI-compatible
    model access, routing, policy controls, traces, and usage reporting.

    Attributes:
        id (str): The model alias to use. Defaults to "gpt-4o".
        name (str): The name of the API. Defaults to "Tuning Engines".
        provider (str): The provider name. Defaults to "Tuning Engines".
        api_key (Optional[str]): The Tuning Engines inference key.
        base_url (str): The API base URL. Defaults to "https://api.tuningengines.com/v1".
    """

    id: str = "gpt-4o"
    name: str = "Tuning Engines"
    provider: str = "Tuning Engines"

    api_key: Optional[str] = field(default_factory=lambda: getenv("TUNING_ENGINES_API_KEY"))
    base_url: str = field(default_factory=lambda: getenv("TUNING_ENGINES_BASE_URL", "https://api.tuningengines.com/v1"))

    def _get_client_params(self) -> Dict[str, Any]:
        # _resolve_required_api_key fails fast on TUNING_ENGINES_API_KEY rather than falling
        # back to the base class's OPENAI_API_KEY, which would silently send an OpenAI key to
        # the Tuning Engines endpoint.
        api_key = self._resolve_required_api_key("TUNING_ENGINES_API_KEY")
        return super()._get_client_params(api_key=api_key)
