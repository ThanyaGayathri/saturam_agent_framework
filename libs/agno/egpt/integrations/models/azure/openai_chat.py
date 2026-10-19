from copy import copy, deepcopy
from dataclasses import dataclass
from os import getenv
from typing import Any, Dict, Optional

import httpx

from egpt.exceptions import ModelAuthenticationError
from egpt.integrations.models.openai_like import OpenAILike
from egpt.utils.log import log_warning

try:
    from openai import AsyncAzureOpenAI as AsyncAzureOpenAIClient
    from openai import AzureOpenAI as AzureOpenAIClient
except ImportError:
    raise ImportError("`openai` not installed. Please install using `pip install openai`")

#: The dated Azure OpenAI API version an ``AzureOpenAI`` model sends when ``api_version`` is
#: unset. A pin, deliberately: the model does not read ``AZURE_OPENAI_API_VERSION`` on its own,
#: so exporting that variable changes nothing until a caller passes it through
#: ``resolve_api_version`` -- a deployment that moves every Azure call to a preview version
#: (which can change structured-output and reasoning-parameter behaviour) does so by choice.
DEFAULT_API_VERSION = "2024-10-21"


def resolve_api_version(explicit: Optional[str] = None) -> str:
    """``explicit``, else the ``AZURE_OPENAI_API_VERSION`` environment variable, else
    ``DEFAULT_API_VERSION``.

    The opt-in chain, for a caller that wants the environment to name the version:
    ``AzureOpenAI(id=..., api_version=resolve_api_version())``. ``AzureOpenAI`` itself never
    consults the variable, so a request field that only a newer version accepts can be
    validated against exactly the version the caller is about to send.
    """
    return explicit or getenv("AZURE_OPENAI_API_VERSION") or DEFAULT_API_VERSION


@dataclass
class AzureOpenAI(OpenAILike):
    """
    Azure OpenAI Chat model

    Args:

        id (str): The model name to use.
        name (str): The model name to use.
        provider (str): The provider to use.
        api_key (Optional[str]): The API key to use.
        api_version (Optional[str]): The API version to use; ``DEFAULT_API_VERSION`` when
            unset. Pass ``resolve_api_version()`` to take it from ``AZURE_OPENAI_API_VERSION``.
        azure_endpoint (Optional[str]): The Azure endpoint to use.
        azure_deployment (Optional[str]): The Azure deployment to use.
        base_url (Optional[str]): The base URL to use.
        azure_ad_token (Optional[str]): The Azure AD token to use.
        azure_ad_token_provider (Optional[Any]): The Azure AD token provider to use.
        organization (Optional[str]): The organization to use.
        client (Optional[AzureOpenAIClient]): The OpenAI client to use.
        async_client (Optional[AsyncAzureOpenAIClient]): The OpenAI client to use.
    """

    id: str
    name: str = "AzureOpenAI"
    provider: str = "Azure"

    supports_native_structured_outputs: bool = True

    api_key: Optional[str] = None
    api_version: Optional[str] = None
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    base_url: Optional[str] = None
    azure_ad_token: Optional[str] = None
    azure_ad_token_provider: Optional[Any] = None

    default_headers: Optional[Dict[str, str]] = None
    default_query: Optional[Dict[str, Any]] = None

    client: Optional[AzureOpenAIClient] = None
    async_client: Optional[AsyncAzureOpenAIClient] = None

    def __deepcopy__(self, memo: dict) -> "AzureOpenAI":
        """Create a deep copy that preserves client references.

        Azure OpenAI clients may carry authentication state (e.g. AD tokens,
        token providers) that cannot be reconstructed from the model's own
        fields alone. Preserving the client references avoids authentication
        errors when the copied model is used (e.g. in the reasoning flow).
        """

        cls = self.__class__
        new_model = cls.__new__(cls)
        memo[id(self)] = new_model

        for k, v in self.__dict__.items():
            if k in {"response_format", "_tools", "_functions"}:
                continue
            # Preserve client references instead of nullifying them
            if k in {"client", "async_client", "http_client"}:
                setattr(new_model, k, v)
                continue
            try:
                setattr(new_model, k, deepcopy(v, memo))
            except Exception:
                try:
                    setattr(new_model, k, copy(v))
                except Exception:
                    setattr(new_model, k, v)

        return new_model

    def _get_client_params(self) -> Dict[str, Any]:
        _client_params: Dict[str, Any] = {}

        api_key = self.api_key or getenv("AZURE_OPENAI_API_KEY")
        azure_endpoint = self.azure_endpoint or getenv("AZURE_OPENAI_ENDPOINT")
        azure_deployment = self.azure_deployment or getenv("AZURE_OPENAI_DEPLOYMENT")

        if not (api_key or self.azure_ad_token or self.azure_ad_token_provider):
            raise ModelAuthenticationError(
                message="Azure OpenAI authentication not configured. Please provide one of:"
                "AZURE_OPENAI_API_KEY environment variable, azure_ad_token, or azure_ad_token_provider",
                model_name=self.name,
            )

        params_mapping = {
            "api_key": api_key,
            "api_version": self.api_version or DEFAULT_API_VERSION,
            "organization": self.organization,
            "azure_endpoint": azure_endpoint,
            "azure_deployment": azure_deployment,
            "base_url": self.base_url,
            "azure_ad_token": self.azure_ad_token,
            "azure_ad_token_provider": self.azure_ad_token_provider,
        }
        if self.default_headers is not None:
            _client_params["default_headers"] = self.default_headers
        if self.default_query is not None:
            _client_params["default_query"] = self.default_query

        _client_params.update({k: v for k, v in params_mapping.items() if v is not None})
        if self.client_params:
            _client_params.update(self.client_params)
        return _client_params

    def get_client(self) -> AzureOpenAIClient:
        """
        Get the OpenAI client.

        Returns:
            AzureOpenAIClient: The OpenAI client.

        """
        if self.client is not None and not self.client.is_closed():
            return self.client

        _client_params: Dict[str, Any] = self._get_client_params()

        if self.http_client:
            if isinstance(self.http_client, httpx.Client):
                _client_params["http_client"] = self.http_client
            else:
                log_warning("http_client is not an instance of httpx.Client. Ignoring and using OpenAI SDK default.")
        # When no custom http_client is provided, let the OpenAI SDK use its own default client.
        # The SDK defaults to HTTP/1.1 which avoids transient 400 errors caused by HTTP/2
        # protocol edge cases with OpenAI's infrastructure.

        # Create client
        self.client = AzureOpenAIClient(**_client_params)
        return self.client

    def get_async_client(self) -> AsyncAzureOpenAIClient:
        """
        Returns an asynchronous OpenAI client.

        Returns:
            AsyncAzureOpenAIClient: An instance of the asynchronous OpenAI client.
        """
        if self.async_client and not self.async_client.is_closed():
            return self.async_client

        _client_params: Dict[str, Any] = self._get_client_params()

        if self.http_client:
            if isinstance(self.http_client, httpx.AsyncClient):
                _client_params["http_client"] = self.http_client
            else:
                log_warning(
                    "http_client is not an instance of httpx.AsyncClient. Ignoring and using OpenAI SDK default."
                )
        # When no custom http_client is provided, let the OpenAI SDK use its own default client.
        # The SDK defaults to HTTP/1.1 which avoids transient 400 errors caused by HTTP/2
        # protocol edge cases with OpenAI's infrastructure.

        self.async_client = AsyncAzureOpenAIClient(**_client_params)
        return self.async_client
