import warnings

from egpt.os.app import EgptOS
from egpt.os.config import MCP_BUILTIN_TAGS, MCPBuiltinTag, MCPServerConfig

_warned_agentos = False


def __getattr__(name: str):
    global _warned_agentos
    if name == "AgentOS":
        if not _warned_agentos:
            _warned_agentos = True
            warnings.warn(
                "AgentOS is deprecated; use EgptOS instead.",
                FutureWarning,
                stacklevel=2,
            )
        return EgptOS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + ["AgentOS"])


__all__ = ["EgptOS", "MCPServerConfig", "MCPBuiltinTag", "MCP_BUILTIN_TAGS"]
