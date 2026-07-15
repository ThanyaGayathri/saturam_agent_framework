"""Backward-compatibility stub. Use egpt.tools.google.sheets instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.googlesheets' is deprecated. "
    "Use 'from egpt.tools.google.sheets import GoogleSheetsTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.tools.google.sheets import *  # noqa: F401, F403, E402
from egpt.tools.google.sheets import GoogleSheetsTools  # noqa: F811, E402

__all__ = ["GoogleSheetsTools"]
