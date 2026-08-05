try:
    from egpt.os.middleware.jwt import (
        JWTMiddleware,
        TokenSource,
    )
except ImportError:

    class JWTMiddleware:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "`PyJWT` not installed. Install it with `pip install PyJWT` (included in the `egpt[os]` extra)."
            )

    class TokenSource:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "`PyJWT` not installed. Install it with `pip install PyJWT` (included in the `egpt[os]` extra)."
            )


from egpt.os.middleware.trailing_slash import TrailingSlashMiddleware

__all__ = [
    name
    for name in (
        "JWTMiddleware",
        "TokenSource",
        "TrailingSlashMiddleware",
    )
    if name in globals()
]
