from dataclasses import dataclass
from typing import Any, Dict, Optional

from egpt.integrations.models.openai_like import OpenAILike


@dataclass
class Together(OpenAILike):
    """
    A class for interacting with Together API.

    Attributes:
        id (str): The id of the Together model to use. Default is "MiniMaxAI/MiniMax-M2.7".
        name (str): The name of this chat model instance. Default is "Together"
        provider (str): The provider of the model. Default is "Together".
        api_key (str): The api key to authorize request to Together.
        base_url (str): The base url to which the requests are sent. Defaults to "https://api.together.xyz/v1".
    """

    id: str = "MiniMaxAI/MiniMax-M2.7"
    name: str = "Together"
    provider: str = "Together"
    api_key: Optional[str] = None
    base_url: str = "https://api.together.xyz/v1"

    def _get_client_params(self) -> Dict[str, Any]:
        """
        Returns client parameters for API requests, checking for TOGETHER_API_KEY.

        Returns:
            Dict[str, Any]: A dictionary of client parameters for API requests.
        """
        api_key = self._resolve_required_api_key("TOGETHER_API_KEY")
        return super()._get_client_params(api_key=api_key)
