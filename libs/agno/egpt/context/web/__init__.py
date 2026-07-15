from egpt.context.web.exa import ExaBackend
from egpt.context.web.exa_mcp import ExaMCPBackend
from egpt.context.web.parallel import ParallelBackend
from egpt.context.web.parallel_mcp import ParallelMCPBackend
from egpt.context.web.provider import DEFAULT_WEB_INSTRUCTIONS, WebContextProvider

__all__ = [
    "DEFAULT_WEB_INSTRUCTIONS",
    "ExaBackend",
    "ExaMCPBackend",
    "ParallelBackend",
    "ParallelMCPBackend",
    "WebContextProvider",
]
