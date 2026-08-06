"""Backward-compatibility stub. Use egpt.integrations.tools.google.bigquery instead."""

import warnings

warnings.warn(
    "Importing from 'egpt.tools.google_bigquery' is deprecated. "
    "Use 'from egpt.integrations.tools.google.bigquery import GoogleBigQueryTools' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from egpt.integrations.tools.google.bigquery import *  # noqa: F401, F403, E402
from egpt.integrations.tools.google.bigquery import GoogleBigQueryTools, _clean_sql  # noqa: F811, E402

__all__ = ["GoogleBigQueryTools", "_clean_sql"]
