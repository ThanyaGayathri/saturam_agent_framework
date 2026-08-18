import hashlib
import hmac
from typing import Optional

from fastapi import HTTPException

# First direct egpt.settings import from egpt/integrations/* (PR #82 review) -- egpt.settings is
# a dependency-free config leaf with no upward layer imports, so this is not a layering breach;
# it's only in forbidden_modules as an indirect-chain safeguard (see pyproject.toml's
# ignore_imports entry for this file). A new precedent, not an existing pattern to copy blindly --
# future integration authors should confirm the same reasoning applies before following it.
from egpt.settings import resolve, resolve_bool
from egpt.utils.log import log_warning


def validate_webhook_signature(payload: bytes, signature_header: Optional[str]) -> bool:
    app_secret = resolve("whatsapp_app_secret")
    if not app_secret:
        # Explicit opt-out: operator must deliberately set this for local dev
        if resolve_bool("whatsapp_skip_signature_validation"):
            log_warning("WHATSAPP_SKIP_SIGNATURE_VALIDATION=true — signature check disabled")
            return True
        raise HTTPException(
            status_code=500,
            detail="WHATSAPP_APP_SECRET not set. Set WHATSAPP_SKIP_SIGNATURE_VALIDATION=true for local development.",
        )

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    # Header format: "sha256=<hex>"; strip prefix to compare digests
    expected_signature = signature_header.removeprefix("sha256=")

    hmac_obj = hmac.new(app_secret.encode(), payload, hashlib.sha256)
    calculated_signature = hmac_obj.hexdigest()

    # Constant-time comparison prevents timing side-channels
    return hmac.compare_digest(calculated_signature, expected_signature)
