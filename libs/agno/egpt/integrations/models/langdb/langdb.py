from dataclasses import dataclass, field
from os import getenv
from typing import Any, Dict, Optional

from egpt.exceptions import ModelAuthenticationError
from egpt.integrations.models.openai_like import OpenAILike


@dataclass
class LangDB(OpenAILike):
    """
    A class for using models hosted on LangDB.

    Attributes:
        id (str): The model id. Defaults to "gpt-4o".
        name (str): The model name. Defaults to "LangDB".
        provider (str): The provider name. Defaults to "LangDB".
        api_key (Optional[str]): The API key. Defaults to getenv("LANGDB_API_KEY").
        project_id (Optional[str]): The project id. Defaults to None.
    """

    id: str = "gpt-4o"
    name: str = "LangDB"
    provider: str = "LangDB"

    api_key: Optional[str] = field(default_factory=lambda: getenv("LANGDB_API_KEY"))
    project_id: Optional[str] = field(default_factory=lambda: getenv("LANGDB_PROJECT_ID"))

    base_host_url: str = field(default_factory=lambda: getenv("LANGDB_API_BASE_URL", "https://api.us-east-1.langdb.ai"))

    base_url: Optional[str] = None
    label: Optional[str] = None
    default_headers: Optional[dict] = None

    def _get_client_params(self) -> Dict[str, Any]:
        api_key = self.api_key
        if not api_key:
            api_key = getenv("LANGDB_API_KEY")
            if not api_key:
                raise ModelAuthenticationError(
                    message="LANGDB_API_KEY not set. Please set the LANGDB_API_KEY environment variable.",
                    model_name=self.name,
                )

        if not self.project_id:
            raise ModelAuthenticationError(
                message="LANGDB_PROJECT_ID not set. Please set the LANGDB_PROJECT_ID environment variable.",
                model_name=self.name,
            )

        base_url = self.base_url or f"{self.base_host_url}/{self.project_id}/v1"

        client_params = super()._get_client_params(api_key=api_key, base_url=base_url)

        # Add label header if present, without mutating self.default_headers.
        if self.label and "default_headers" not in client_params:
            client_params["default_headers"] = {"x-label": self.label}

        return client_params
