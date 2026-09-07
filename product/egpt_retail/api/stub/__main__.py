"""``python -m egpt_retail.api.stub`` -- run the contract stub locally.

Environment:
    EGPT_STUB_HOST         default 127.0.0.1
    EGPT_STUB_PORT         default 7778 (7777 is the real EgptOS, R1)
    EGPT_STUB_JWT_SECRET   when set, every operation requires a Bearer token signed with it
                           whose `scope` claim covers the operation's x-egpt-scopes
    EGPT_STUB_NO_FIXTURE   when set to 1, the semantic reads answer 503 (an unloaded stub)
"""

import os

from egpt_retail.api.stub.server import serve

if __name__ == "__main__":
    serve(
        host=os.environ.get("EGPT_STUB_HOST", "127.0.0.1"),
        port=int(os.environ.get("EGPT_STUB_PORT", "7778")),
        jwt_secret=os.environ.get("EGPT_STUB_JWT_SECRET") or None,
        load_fixture=os.environ.get("EGPT_STUB_NO_FIXTURE", "0") != "1",
    )
