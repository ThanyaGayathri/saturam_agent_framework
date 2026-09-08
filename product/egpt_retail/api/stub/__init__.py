"""The phase-zero stub server: the API contract, running.

A contract nobody can execute is one everybody interprets differently. This package stands
the contract up as a FastAPI application that serves each operation's declared example, so
a prototype screen can be pointed at it before any endpoint is implemented.
"""

from egpt_retail.api.stub.server import aserve, create_app, mint_token, serve

__all__ = ["create_app", "mint_token", "serve", "aserve"]
