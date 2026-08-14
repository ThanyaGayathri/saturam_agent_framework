import hashlib
import hmac
import time
from typing import Optional

from fastapi import HTTPException

from egpt.settings import resolve


def verify_slack_signature(
    body: bytes, timestamp: str, slack_signature: str, signing_secret: Optional[str] = None
) -> bool:
    # Per-instance secret takes priority; fall back to a live env read for single-app
    # setups. Read live (not at import time) so setenv after import -- e.g. a .env
    # loaded during app startup -- is honored instead of permanently 500ing.
    secret = signing_secret if signing_secret is not None else resolve("slack_signing_secret")
    if not secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not set")

    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False

    # Reject stale requests to block replay attacks (Slack's recommended window)
    if abs(time.time() - ts) > 60 * 5:
        return False

    try:
        body_str = body.decode("utf-8")
    except UnicodeDecodeError:
        return False

    sig_basestring = f"v0:{timestamp}:{body_str}"
    my_signature = "v0=" + hmac.new(secret.encode("utf-8"), sig_basestring.encode("utf-8"), hashlib.sha256).hexdigest()

    return hmac.compare_digest(my_signature, slack_signature)
