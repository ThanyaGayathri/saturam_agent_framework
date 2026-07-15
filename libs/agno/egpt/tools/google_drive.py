"""Backward-compatibility stub. Use egpt.tools.google.drive instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.google_drive' is deprecated. "
    "Use 'from egpt.tools.google.drive import GoogleDriveTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.tools.google.drive import *  # noqa: F401, F403, E402
from egpt.tools.google.drive import GoogleDriveTools  # noqa: F811, E402

__all__ = ["GoogleDriveTools"]
