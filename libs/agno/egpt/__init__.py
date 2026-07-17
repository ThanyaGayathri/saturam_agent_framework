from importlib.metadata import PackageNotFoundError, version

from egpt.brand import ENV_PREFIX, LEGACY_ENV_PREFIX, PRODUCT_NAME, PRODUCT_OS_NAME

try:
    __version__ = version("egpt")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "ENV_PREFIX",
    "LEGACY_ENV_PREFIX",
    "PRODUCT_NAME",
    "PRODUCT_OS_NAME",
    "__version__",
]
