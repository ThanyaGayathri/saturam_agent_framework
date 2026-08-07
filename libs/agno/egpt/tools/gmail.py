"""Backward-compatibility stub. Use egpt.integrations.tools.google.gmail instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.gmail' is deprecated. Use 'from egpt.integrations.tools.google.gmail import GmailTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.integrations.tools.google.gmail import *  # noqa: F401, F403, E402
from egpt.integrations.tools.google.gmail import GmailTools  # noqa: F811, E402

__all__ = ["GmailTools"]
