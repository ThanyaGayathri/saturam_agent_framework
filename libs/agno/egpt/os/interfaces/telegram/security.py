import hmac
import os
import warnings
from typing import Optional

from egpt.settings import require, resolve_bool
from egpt.utils.log import log_warning


def _is_dev_mode() -> bool:
    if resolve_bool("telegram_skip_secret_validation"):
        return True
    # Deprecated: APP_ENV is a generic deployment-environment variable, and a
    # signature-check bypass should not ride on it implicitly. EGPT_TELEGRAM_SKIP_
    # SECRET_VALIDATION replaces it as an explicit, single-purpose flag.
    if os.getenv("APP_ENV", "").lower() == "development":
        warnings.warn(
            "APP_ENV=development is deprecated for bypassing Telegram webhook secret "
            "validation; set EGPT_TELEGRAM_SKIP_SECRET_VALIDATION=true instead.",
            FutureWarning,
            stacklevel=2,
        )
        return True
    return False


def get_webhook_secret_token() -> str:
    return require("telegram_webhook_secret_token", hint="(required outside development mode)")


def validate_webhook_secret_token(secret_token_header: Optional[str]) -> bool:
    if _is_dev_mode():
        log_warning("Bypassing secret token validation in development mode")
        return True

    if not secret_token_header:
        return False

    expected = get_webhook_secret_token()
    return hmac.compare_digest(secret_token_header, expected)
