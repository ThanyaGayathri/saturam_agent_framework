"""Backward-compatibility stub. Use egpt.integrations.tools.google.calendar instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.googlecalendar' is deprecated. "
    "Use 'from egpt.integrations.tools.google.calendar import GoogleCalendarTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.integrations.tools.google.calendar import *  # noqa: F401, F403, E402
from egpt.integrations.tools.google.calendar import GoogleCalendarTools  # noqa: F811, E402

__all__ = ["GoogleCalendarTools"]
