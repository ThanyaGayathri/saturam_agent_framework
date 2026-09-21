"""Credential references, resolved by the platform and never sent by the interface (K16).

A configured source carries ``credentialRef`` -- ``env:gmail``, ``env:salesforce`` -- and
the platform resolves it where the platform runs: from the process environment locally (the
gitignored ``gmail.env`` / ``salesforce.env`` the demo sources in), from a vault in the
cloud when that lands. The interface stores the reference and shows the person which
connection a use case uses; it never holds a password or a client secret.

``resolve_credentials`` returns exactly the mapping the document executors read
(``documents.mailbox.mailbox_from`` / ``documents.salesforce.client_from`` accept both).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

__all__ = ["resolve_credentials", "CredentialUnavailable"]


class CredentialUnavailable(ValueError):
    """The reference names a store or a key this deployment does not have."""


_MAILBOX_ENV = {
    "user": "GMAIL_USER",
    "password": "GMAIL_APP_PASSWORD",
    "imap_host": "GMAIL_IMAP_HOST",
    "smtp_host": "GMAIL_SMTP_HOST",
    "smtp_port": "GMAIL_SMTP_PORT",
}
_SALESFORCE_ENV = {
    "login_url": "SF_LOGIN_URL",
    "client_id": "SF_CLIENT_ID",
    "client_secret": "SF_CLIENT_SECRET",
}


def resolve_credentials(
    source: Mapping[str, Any], *, kind: str, environ: Optional[Mapping[str, str]] = None
) -> Dict[str, Any]:
    """The connection mapping for one configured source.

    ``kind`` is ``mailbox`` or ``salesforce``. A source with explicit connection fields
    (``user``/``password``, ``login_url``/``client_id``/``client_secret``) is returned as is;
    otherwise its ``credentialRef`` is resolved. Only the ``env:`` scheme exists today.
    """
    env = environ if environ is not None else os.environ
    out: Dict[str, Any] = {
        k: v for k, v in source.items() if k not in {"credentialRef", "credential_ref"}
    }
    ref = str(source.get("credentialRef") or source.get("credential_ref") or "").strip()
    keys = _MAILBOX_ENV if kind == "mailbox" else _SALESFORCE_ENV
    if all(
        out.get(k) for k in keys if k not in {"smtp_port", "imap_host", "smtp_host"}
    ):
        return out
    if not ref:
        ref = "env:gmail" if kind == "mailbox" else "env:salesforce"
    scheme, _, name = ref.partition(":")
    if scheme != "env":
        raise CredentialUnavailable(
            f"credential store {scheme!r} is not available in this deployment"
        )
    prefix = {"gmail": "GMAIL", "mailbox": "GMAIL", "salesforce": "SF", "sf": "SF"}.get(
        name.lower()
    )
    if prefix is None:
        raise CredentialUnavailable(f"no environment credentials named {name!r}")
    missing = []
    for field_name, env_name in keys.items():
        value = env.get(env_name)
        if value:
            out.setdefault(field_name, value)
        elif field_name in {
            "user",
            "password",
            "login_url",
            "client_id",
            "client_secret",
        }:
            missing.append(env_name)
    if missing:
        raise CredentialUnavailable(
            f"{ref}: {', '.join(missing)} not set in the environment"
        )
    if kind == "salesforce":
        out.setdefault("type", "salesforce")
    return out
