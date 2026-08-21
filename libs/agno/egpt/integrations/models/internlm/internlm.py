from dataclasses import dataclass, field
from os import getenv
from typing import Any, Dict, Optional

from egpt.integrations.models.openai_like import OpenAILike


@dataclass
class InternLM(OpenAILike):
    """
    Class for interacting with the InternLM API.

    Attributes:
        id (str): The ID of the language model. Defaults to "internlm2.5-latest".
        name (str): The name of the model. Defaults to "InternLM".
        provider (str): The provider of the model. Defaults to "InternLM".
        api_key (Optional[str]): The API key for the InternLM API.
        base_url (Optional[str]): The base URL for the InternLM API.
    """

    id: str = "internlm2.5-latest"
    name: str = "InternLM"
    provider: str = "InternLM"

    api_key: Optional[str] = field(default_factory=lambda: getenv("INTERNLM_API_KEY"))
    base_url: Optional[str] = "https://internlm-chat.intern-ai.org.cn/puyu/api/v1/chat/completions"

    def _get_client_params(self) -> Dict[str, Any]:
        """
        Returns client parameters for API requests, checking for INTERNLM_API_KEY.

        Returns:
            Dict[str, Any]: A dictionary of client parameters for API requests.
        """
        api_key = self._resolve_required_api_key("INTERNLM_API_KEY")
        return super()._get_client_params(api_key=api_key)
