from dataclasses import dataclass, field
from os import getenv
from typing import Any, Dict, Optional

from egpt.integrations.models.openai_like import OpenAILike


@dataclass
class LiteLLMOpenAI(OpenAILike):
    """
    A class for interacting with LiteLLM.

    Attributes:
        id (str): The id of the LiteLLM model. Default is "gpt-4o".
        name (str): The name of this chat model instance. Default is "LiteLLM".
        provider (str): The provider of the model. Default is "LiteLLM".
        base_url (str): The base url to which the requests are sent.
    """

    id: str = "gpt-4o"
    name: str = "LiteLLMOpenAI"
    provider: str = "LiteLLM"

    api_key: Optional[str] = field(default_factory=lambda: getenv("LITELLM_API_KEY"))
    base_url: str = "http://0.0.0.0:4000"

    def _get_client_params(self) -> Dict[str, Any]:
        """
        Returns client parameters for API requests, checking for LITELLM_API_KEY.

        Returns:
            Dict[str, Any]: A dictionary of client parameters for API requests.
        """
        api_key = self._resolve_required_api_key("LITELLM_API_KEY")
        return super()._get_client_params(api_key=api_key)
