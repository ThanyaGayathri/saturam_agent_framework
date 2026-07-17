import warnings

from egpt.client.os import EgptOSClient

_warned_agentos_client = False


def __getattr__(name: str):
    global _warned_agentos_client
    if name == "AgentOSClient":
        if not _warned_agentos_client:
            _warned_agentos_client = True
            warnings.warn(
                "AgentOSClient is deprecated; use EgptOSClient instead.",
                FutureWarning,
                stacklevel=2,
            )
        return EgptOSClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + ["AgentOSClient"])


__all__ = ["EgptOSClient"]
