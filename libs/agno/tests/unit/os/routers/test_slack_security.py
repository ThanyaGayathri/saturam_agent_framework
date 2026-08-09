import hashlib
import hmac
import time

import pytest

pytest.importorskip("slack_sdk", reason="the Slack interface needs the Slack SDK")

from fastapi import HTTPException  # noqa: E402

from egpt.os.interfaces.slack.security import verify_slack_signature  # noqa: E402


def _sign(body: bytes, timestamp: str, secret: str) -> str:
    sig_base = f"v0:{timestamp}:{body.decode()}"
    return "v0=" + hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256).hexdigest()


class TestVerifySlackSignature:
    def test_explicit_secret(self):
        body = b'{"test": true}'
        ts = str(int(time.time()))
        secret = "explicit-secret"
        sig = _sign(body, ts, secret)
        assert verify_slack_signature(body, ts, sig, signing_secret=secret) is True

    def test_env_fallback(self, monkeypatch: pytest.MonkeyPatch):
        body = b'{"test": true}'
        ts = str(int(time.time()))
        env_secret = "env-secret-value"
        sig = _sign(body, ts, env_secret)
        # The signing secret is read live (not frozen at import), so setting it here
        # after the module has already been imported must still take effect.
        monkeypatch.setenv("EGPT_SLACK_SIGNING_SECRET", env_secret)
        assert verify_slack_signature(body, ts, sig) is True

    def test_empty_string_not_fallback(self, monkeypatch: pytest.MonkeyPatch):
        body = b'{"test": true}'
        ts = str(int(time.time()))
        monkeypatch.setenv("EGPT_SLACK_SIGNING_SECRET", "env-secret")
        with pytest.raises(HTTPException) as exc_info:
            verify_slack_signature(body, ts, "v0=fake", signing_secret="")
        assert exc_info.value.status_code == 500

    def test_missing_secret_raises_500(self, monkeypatch: pytest.MonkeyPatch):
        body = b'{"test": true}'
        ts = str(int(time.time()))
        monkeypatch.delenv("EGPT_SLACK_SIGNING_SECRET", raising=False)
        monkeypatch.delenv("AGNO_SLACK_SIGNING_SECRET", raising=False)
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            verify_slack_signature(body, ts, "v0=fake")
        assert exc_info.value.status_code == 500

    def test_stale_timestamp_rejected(self):
        body = b'{"test": true}'
        secret = "test-secret"
        stale_ts = str(int(time.time()) - 400)
        sig = _sign(body, stale_ts, secret)
        assert verify_slack_signature(body, stale_ts, sig, signing_secret=secret) is False

    def test_wrong_signature_rejected(self):
        body = b'{"test": true}'
        ts = str(int(time.time()))
        assert verify_slack_signature(body, ts, "v0=deadbeef", signing_secret="secret") is False

    def test_non_utf8_body_rejected(self):
        body = b"\x80\x81\x82\xff"
        ts = str(int(time.time()))
        assert verify_slack_signature(body, ts, "v0=deadbeef", signing_secret="secret") is False
