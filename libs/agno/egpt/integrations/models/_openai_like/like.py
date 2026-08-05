from dataclasses import dataclass
from typing import Optional

from egpt.integrations.models.openai.chat import OpenAIChat
from egpt.models.base import Model


@dataclass
class OpenAILike(OpenAIChat):
    """
    A class for to interact with any provider using the OpenAI API schema.

    Args:
        id (str): The id of the OpenAI model to use. Defaults to "not-provided".
        name (str): The name of the OpenAI model to use. Defaults to "OpenAILike".
        api_key (Optional[str]): The API key to use. Defaults to "not-provided".
    """

    id: str = "not-provided"
    name: str = "OpenAILike"
    api_key: Optional[str] = "not-provided"

    # Lets core/orchestration detect an OpenAI-schema proxy provider without
    # importing this class (ADR 0001 allowed-import matrix).
    is_openai_compatible_proxy: bool = True

    default_role_map = {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "tool": "tool",
    }

    def get_provider(self) -> str:
        return Model.get_provider(self)
