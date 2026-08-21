from dataclasses import dataclass
from os import getenv
from typing import Optional

from egpt.exceptions import ModelAuthenticationError
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

    def _resolve_required_api_key(self, env_var: str) -> str:
        """Resolve this provider's API key from `self.api_key` or `env_var`, raising if unset.

        Fails fast with the provider's own env var name rather than falling back to
        `OpenAIChat`'s `OPENAI_API_KEY` -- without this, a misconfigured proxy provider would
        silently send an OpenAI key to a non-OpenAI endpoint.
        """
        api_key = self.api_key or getenv(env_var)
        if not api_key:
            raise ModelAuthenticationError(
                message=f"{env_var} not set. Please set the {env_var} environment variable.",
                model_name=self.name,
            )
        return api_key
