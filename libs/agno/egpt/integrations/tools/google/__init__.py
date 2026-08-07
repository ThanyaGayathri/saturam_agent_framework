__all__ = [
    "AuthConfig",
    "GoogleAuth",
    "GoogleSlidesTools",
    "GoogleBigQueryTools",
    "GoogleCalendarTools",
    "GoogleDriveTools",
    "GmailTools",
    "GoogleMapTools",
    "GoogleSheetsTools",
]


def __getattr__(name: str):
    if name == "AuthConfig":
        from egpt.integrations.tools.google.auth import AuthConfig

        return AuthConfig
    if name == "GoogleAuth":
        from egpt.integrations.tools.google.auth import GoogleAuth

        return GoogleAuth
    if name == "GoogleSlidesTools":
        from egpt.integrations.tools.google.slides import GoogleSlidesTools

        return GoogleSlidesTools
    if name == "GoogleBigQueryTools":
        from egpt.integrations.tools.google.bigquery import GoogleBigQueryTools

        return GoogleBigQueryTools
    if name == "GoogleCalendarTools":
        from egpt.integrations.tools.google.calendar import GoogleCalendarTools

        return GoogleCalendarTools
    if name == "GoogleDriveTools":
        from egpt.integrations.tools.google.drive import GoogleDriveTools

        return GoogleDriveTools
    if name == "GmailTools":
        from egpt.integrations.tools.google.gmail import GmailTools

        return GmailTools
    if name == "GoogleMapTools":
        from egpt.integrations.tools.google.maps import GoogleMapTools

        return GoogleMapTools
    if name == "GoogleSheetsTools":
        from egpt.integrations.tools.google.sheets import GoogleSheetsTools

        return GoogleSheetsTools
    raise AttributeError(f"module 'egpt.integrations.tools.google' has no attribute {name!r}")
