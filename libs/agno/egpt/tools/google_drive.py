"""Backward-compatibility stub. Use egpt.integrations.tools.google.drive instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.google_drive' is deprecated. "
    "Use 'from egpt.integrations.tools.google.drive import GoogleDriveTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.integrations.tools.google.drive import *  # noqa: F401, F403, E402
from egpt.integrations.tools.google.drive import GoogleDriveTools  # noqa: F811, E402

__all__ = ["GoogleDriveTools"]
