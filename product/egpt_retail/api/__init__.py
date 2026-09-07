"""The Enterprise GPT API contract -- Lane R's R3 deliverable.

Every endpoint the screens will call, with request and response shapes, published as a
schema rather than a document. The OpenAPI source lives in ``openapi/``; ``models/`` is
generated from it; ``stub/`` serves each operation's declared examples so a screen can be
pointed at the contract before any of it is implemented.
"""
