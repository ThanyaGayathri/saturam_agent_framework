"""Backward-compatibility stub. Use egpt.tools.google.calendar instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.googlecalendar' is deprecated. "
    "Use 'from egpt.tools.google.calendar import GoogleCalendarTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.tools.google.calendar import *  # noqa: F401, F403, E402
from egpt.tools.google.calendar import GoogleCalendarTools  # noqa: F811, E402

__all__ = ["GoogleCalendarTools"]
