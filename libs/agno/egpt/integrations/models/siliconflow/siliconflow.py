from dataclasses import dataclass
from typing import Any, Dict, Optional

from egpt.integrations.models.openai_like import OpenAILike


@dataclass
class Siliconflow(OpenAILike):
    """
    A class for interacting with Siliconflow API.

    Attributes:
        id (str): The id of the Siliconflow model to use. Default is "Qwen/QwQ-32B".
        name (str): The name of this chat model instance. Default is "Siliconflow".
        provider (str): The provider of the model. Default is "Siliconflow".
        api_key (str): The api key to authorize request to Siliconflow.
        base_url (str): The base url to which the requests are sent. Defaults to "https://api.siliconflow.cn/v1".
    """

    id: str = "Qwen/QwQ-32B"
    name: str = "Siliconflow"
    provider: str = "Siliconflow"
    api_key: Optional[str] = None
    base_url: str = "https://api.siliconflow.cn/v1"

    def _get_client_params(self) -> Dict[str, Any]:
        """
        Returns client parameters for API requests, checking for SILICONFLOW_API_KEY.

        Returns:
            Dict[str, Any]: A dictionary of client parameters for API requests.
        """
        api_key = self._resolve_required_api_key("SILICONFLOW_API_KEY")
        return super()._get_client_params(api_key=api_key)
