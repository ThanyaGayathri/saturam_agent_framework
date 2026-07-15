from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION_NAME = "egptctl"
CLI_NAME = "egpt"

try:
    __version__ = version(DISTRIBUTION_NAME)
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["CLI_NAME", "DISTRIBUTION_NAME", "__version__"]
