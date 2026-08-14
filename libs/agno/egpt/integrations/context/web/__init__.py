from egpt.integrations.context.web.exa import ExaBackend
from egpt.integrations.context.web.exa_mcp import ExaMCPBackend
from egpt.integrations.context.web.parallel import ParallelBackend
from egpt.integrations.context.web.parallel_mcp import ParallelMCPBackend
from egpt.integrations.context.web.provider import DEFAULT_WEB_INSTRUCTIONS, WebContextProvider

__all__ = [
    "DEFAULT_WEB_INSTRUCTIONS",
    "ExaBackend",
    "ExaMCPBackend",
    "ParallelBackend",
    "ParallelMCPBackend",
    "WebContextProvider",
]
