"""Backward-compatibility stub. Use egpt.integrations.tools.google.maps instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.google_maps' is deprecated. "
    "Use 'from egpt.integrations.tools.google.maps import GoogleMapTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.integrations.tools.google.maps import *  # noqa: F401, F403, E402
from egpt.integrations.tools.google.maps import GoogleMapTools  # noqa: F811, E402

__all__ = ["GoogleMapTools"]
