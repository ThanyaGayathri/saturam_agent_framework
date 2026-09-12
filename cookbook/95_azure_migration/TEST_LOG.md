# Azure migration -- test log

One folder, one log, per `docs/plan/azure-migration-plan.md`. Entries are appended, tagged by
package (AZ-A/AZ-B/AZ-C/AZ-D/AZ-P0). Do not overwrite another package's entry.

```
### AZ_D_entra_scopes.py

**Package:** AZ-D
**Status:** PASS

**Description:** Ran against a real local HTTP server serving a real RSA-signed JWKS document
(no mocks) through the real `AuthMiddleware`. Three scenarios: (A) Entra's `scp` claim -- a
space-delimited string -- resolved via `scopes_claim="scp"`, `scopes_claim_format=
"space_delimited"`; (B) Entra's `roles` claim -- already a list -- resolved via
`scopes_claim="roles"` (format has no effect on a list); (C) the framework's own default
`scopes` claim, completely unconfigured, run over a JWKS *URL* instead of a JWKS file (gap
#2), proving the default behaviour is unaffected by the JWKS-URL code path.

**Result:**
```
Serving a real JWKS document over HTTP at http://127.0.0.1:60764/jwks.json

--- Scenario A: scp (space-delimited) ---
status=200 body={'user_id': 'alice', 'scopes': ['agents.read', 'agents.run']}
PASS: resolved scopes ['agents.read', 'agents.run'] == expected ['agents.read', 'agents.run']

--- Scenario B: roles (list, unaffected by format) ---
status=200 body={'user_id': 'bob', 'scopes': ['Retail.Admin', 'Retail.Viewer']}
PASS: resolved scopes ['Retail.Admin', 'Retail.Viewer'] == expected ['Retail.Admin', 'Retail.Viewer']

--- Scenario C: default scopes claim, unchanged behaviour, over a JWKS URL ---
status=200 body={'user_id': 'carol', 'scopes': ['agents:read']}
PASS: resolved scopes ['agents:read'] == expected ['agents:read']

All AZ-D scenarios passed: scopes_claim/scopes_claim_format expansion and the
JWKS URL path both resolve real RS256 tokens to the expected internal scopes,
through the real AuthMiddleware, with zero mocks.
```

All three hand-computed expected-scope sets matched exactly. Also verified separately (unit
tests, `libs/agno/tests/unit/os/test_scopes_claim.py` and `test_jwks_url.py`): the OLD
behaviour (default `scopes` claim, a bare string coercing to a one-element list) is unchanged
when the new fields are left at their defaults, a malformed claim shape (int, `None`, dict)
still resolves to `[]` rather than crashing, and a genuine local JWKS file path still goes
through the original `open()`+`json.load()` code path (no `PyJWKClient` involved). Full
`libs/agno/tests/unit/os/` suite: 2271 passed (3 pre-existing, unrelated failures needing
`OPENAI_API_KEY`, confirmed present identically on `origin/main` before this change).
```
