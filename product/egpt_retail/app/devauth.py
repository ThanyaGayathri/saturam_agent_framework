"""Offline RS256 identity for local verification -- a key pair, a JWKS file, and minted tokens.

The target deployment verifies tokens against Entra ID's JWKS. Locally there is no issuer, and
standing one up to prove 200 versus 403 buys nothing: the framework's JWT middleware verifies
RS256 against whatever JWKS file it is pointed at, so an offline key pair mounted as that file
produces the same code path with a scope claim we control. This is stronger evidence than a
login flow, because the scopes on the token are explicit.

Nothing here is used in production. ``AKS_CUTOVER.md`` replaces the mounted file with the
identity provider's JWKS URL.

Claims follow the middleware's defaults: ``sub``, ``scopes`` (a list), ``aud``, ``exp``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "generate_rsa_jwks",
    "write_dev_jwks",
    "awrite_dev_jwks",
    "mint_token",
    "main",
]

DEFAULT_KID = "egpt-local-dev"


def _b64url(value: int, length: int) -> str:
    return (
        base64.urlsafe_b64encode(value.to_bytes(length, "big"))
        .rstrip(b"=")
        .decode("ascii")
    )


def generate_rsa_jwks(kid: str = DEFAULT_KID) -> Tuple[str, Dict[str, object]]:
    """A fresh RSA-2048 key pair as (private key PEM, JWKS document). Pure construction."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    numbers = private_key.public_key().public_numbers()
    jwks: Dict[str, Any] = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64url(numbers.n, (numbers.n.bit_length() + 7) // 8),
                "e": _b64url(numbers.e, (numbers.e.bit_length() + 7) // 8),
            }
        ]
    }
    return pem, jwks


def write_dev_jwks(directory: Path, kid: str = DEFAULT_KID) -> Tuple[Path, Path]:
    """Write ``private.pem`` and ``jwks.json`` into ``directory``. Returns both paths."""
    directory.mkdir(parents=True, exist_ok=True)
    pem, jwks = generate_rsa_jwks(kid)
    private_path = directory / "private.pem"
    jwks_path = directory / "jwks.json"
    private_path.write_text(pem)
    private_path.chmod(0o600)
    jwks_path.write_text(json.dumps(jwks, indent=2) + "\n")
    return private_path, jwks_path


async def awrite_dev_jwks(directory: Path, kid: str = DEFAULT_KID) -> Tuple[Path, Path]:
    """Async variant. The writes happen off the event loop."""
    return await asyncio.to_thread(write_dev_jwks, directory, kid)


def mint_token(
    private_pem: str,
    scopes: Sequence[str],
    subject: str = "alice",
    audience: Optional[str] = None,
    kid: str = DEFAULT_KID,
    ttl_seconds: int = 3600,
) -> str:
    """An RS256 token the middleware will accept against the matching JWKS. Pure construction."""
    import jwt

    now = int(time.time())
    claims: Dict[str, object] = {
        "sub": subject,
        "scopes": list(scopes),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if audience:
        claims["aud"] = audience
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m egpt_retail.app.devauth")
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser(
        "keygen", help="Write private.pem and jwks.json into a directory."
    )
    keygen.add_argument("directory", type=Path)
    keygen.add_argument("--kid", default=DEFAULT_KID)
    mint = sub.add_parser("mint", help="Print a token signed with private.pem.")
    mint.add_argument("private_pem", type=Path)
    mint.add_argument("--sub", default="alice")
    mint.add_argument("--scope", action="append", default=[], help="Repeatable.")
    mint.add_argument("--aud", default=None)
    mint.add_argument("--kid", default=DEFAULT_KID)
    args = parser.parse_args(argv)
    if args.command == "keygen":
        private_path, jwks_path = write_dev_jwks(args.directory, args.kid)
        print(f"{private_path}\n{jwks_path}")
        return 0
    print(
        mint_token(
            args.private_pem.read_text(),
            args.scope,
            subject=args.sub,
            audience=args.aud,
            kid=args.kid,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
