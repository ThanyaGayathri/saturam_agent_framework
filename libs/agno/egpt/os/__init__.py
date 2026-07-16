import warnings

from egpt.os.app import EgptOS
from egpt.os.config import MCP_BUILTIN_TAGS, MCPBuiltinTag, MCPServerConfig


def __getattr__(name: str):
    if name == "AgentOS":
        warnings.warn(
            "AgentOS is deprecated; use EgptOS instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return EgptOS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["EgptOS", "AgentOS", "MCPServerConfig", "MCPBuiltinTag", "MCP_BUILTIN_TAGS"]
