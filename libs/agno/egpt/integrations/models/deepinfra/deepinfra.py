from dataclasses import dataclass, field
from os import getenv
from typing import Any, Dict, Optional

from egpt.integrations.models.openai_like import OpenAILike


@dataclass
class DeepInfra(OpenAILike):
    """
    A class for interacting with DeepInfra models.

    Attributes:
        id (str): The model id. Defaults to "meta-llama/Llama-2-70b-chat-hf".
        name (str): The model name. Defaults to "DeepInfra".
        provider (str): The provider name. Defaults to "DeepInfra".
        api_key (Optional[str]): The API key.
        base_url (str): The base URL. Defaults to "https://api.deepinfra.com/v1/openai".
    """

    id: str = "meta-llama/Llama-2-70b-chat-hf"
    name: str = "DeepInfra"
    provider: str = "DeepInfra"

    api_key: Optional[str] = field(default_factory=lambda: getenv("DEEPINFRA_API_KEY"))
    base_url: str = "https://api.deepinfra.com/v1/openai"

    supports_native_structured_outputs: bool = False

    def _get_client_params(self) -> Dict[str, Any]:
        """
        Returns client parameters for API requests, checking for DEEPINFRA_API_KEY.

        Returns:
            Dict[str, Any]: A dictionary of client parameters for API requests.
        """
        api_key = self._resolve_required_api_key("DEEPINFRA_API_KEY")
        return super()._get_client_params(api_key=api_key)
