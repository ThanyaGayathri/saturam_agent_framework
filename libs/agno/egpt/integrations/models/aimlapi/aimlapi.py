from dataclasses import dataclass, field
from os import getenv
from typing import Any, Dict, Optional

from egpt.integrations.models.openai_like import OpenAILike
from egpt.models.message import Message


@dataclass
class AIMLAPI(OpenAILike):
    """
    A class for using models hosted on AIMLAPI.

    Attributes:
        id (str): The model id. Defaults to "gpt-4o-mini".
        name (str): The model name. Defaults to "AIMLAPI".
        provider (str): The provider name. Defaults to "AIMLAPI".
        api_key (Optional[str]): The API key.
        base_url (str): The base URL. Defaults to "https://api.aimlapi.com/v1".
        max_tokens (int): The maximum number of tokens. Defaults to 4096.
    """

    id: str = "gpt-4o-mini"
    name: str = "AIMLAPI"
    provider: str = "AIMLAPI"

    api_key: Optional[str] = field(default_factory=lambda: getenv("AIMLAPI_API_KEY"))
    base_url: str = "https://api.aimlapi.com/v1"
    max_tokens: int = 4096

    def _get_client_params(self) -> Dict[str, Any]:
        """
        Returns client parameters for API requests, checking for AIMLAPI_API_KEY.

        Returns:
            Dict[str, Any]: A dictionary of client parameters for API requests.
        """
        api_key = self._resolve_required_api_key("AIMLAPI_API_KEY")
        return super()._get_client_params(api_key=api_key)

    def _format_message(self, message: Message) -> Dict[str, Any]:
        """
        Minimal additional formatter that only replaces None with empty string.

        Args:
            message (Message): The message to format.

        Returns:
            Dict[str, Any]: The formatted message, where 'content = None' is replaced with the empty string.
        """
        formatted: dict = super()._format_message(message)

        formatted["content"] = "" if formatted.get("content") is None else formatted["content"]

        return formatted
