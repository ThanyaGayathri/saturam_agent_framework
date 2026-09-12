# Azure migration -- test log

One folder, one log, per `docs/plan/azure-migration-plan.md`. Entries are appended, tagged by
package (AZ-A/AZ-B/AZ-C/AZ-D/AZ-P0). Do not overwrite another package's entry.

Note on merge conflicts: several AZ packages create this file fresh from the same base, so the
only conflict git raises across them is "added by both" on this shared header -- the resolution
is always "keep one header, concatenate the package sections," never picking a side. No entry is
ever lost to a merge; see any of the individual package PRs for the same note at the point it was
first hit.

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

---

### AZ_A_blob_roundtrip.py

**Package:** AZ-A
**Status:** PASS

**Description:** Ran against a real local Azurite instance (`mcr.microsoft.com/azure-storage/azurite:latest`,
started with `docker run -d --name egpt-azurite -p 10000:10000 -p 10001:10001 -p 10002:10002
mcr.microsoft.com/azure-storage/azurite:latest azurite --blobHost 0.0.0.0 --queueHost 0.0.0.0
--tableHost 0.0.0.0`). Uploaded C1's four committed landing fixtures
(`product/egpt_retail/contracts/_fixtures/landing/raw/...`) into a fresh `egpt-landing`
container via Azurite's well-known development connection string. Listed the container
through `AzureBlobObjectStore.list_objects`, read every object back through `get_bytes`, and
diffed both against `LocalDirectoryStore` reading the identical fixtures off disk. Checked the
async twins (`alist_objects`/`aget_bytes`) agree with their sync counterparts. Deliberately
tried a path that escapes the landing root (`raw/../../../etc/passwd`), and a half-configured
credential pair (`account_name` with no `account_key`).

**Result:** All four fixture files listed by `AzureBlobObjectStore` matched
`LocalDirectoryStore`'s listing exactly, and every object's bytes were identical between the
two backends. The async variants returned the same results as their sync twins. The escaping
path was refused with `ValueError: 'raw/../../../etc/passwd' resolves outside the landing
root egpt-landing/` -- the same shape `LocalDirectoryStore._resolve` raises. The
half-configured credential pair was refused at construction with `RuntimeError:
EGPT_RETAIL_STORAGE_ACCOUNT_KEY is not set while its partner is...`, before any client or
network call was made.

Full script output:

```
AZ-A -- Azure Blob storage backend, against Azurite
==============================================================================
-- 1. Upload C1's fixtures into a fresh Azurite container --
   uploaded 4 fixture files
-- 2. List and read through AzureBlobObjectStore --
   Azure Blob listed 4 objects
   local directory listed 4 objects
   all 4 objects byte-for-byte identical to LocalDirectoryStore
-- 3. Async variants agree with their sync twins --
   alist_objects/aget_bytes agree with list_objects/get_bytes
-- 4. A path escaping the landing root is refused --
   refused as expected: 'raw/../../../etc/passwd' resolves outside the landing root egpt-landing/
-- 5. A half-configured credential pair is refused at construction --
   refused as expected: EGPT_RETAIL_STORAGE_ACCOUNT_KEY is not set while its partner is. Set both to use an explicit shared key, or neither to use the ambient credential chain (managed identity on Container Apps). One of the two is a typo, not a configuration.

==============================================================================
PASS -- Azure Blob listing and get_bytes match LocalDirectoryStore's output
byte-for-byte on the same fixtures. The escaping path was refused with the same
ValueError shape LocalDirectoryStore uses. The half-configured credential pair
was refused at construction.
```

Also verified: `product/tests/test_r4_storage.py` extended with seven unit tests for
`AzureBlobObjectStore`'s constructor validation (half-configured shared-key pair, both/neither
accepted, connection-string-only construction, no-account-addressable refusal, path-escape
refusal) and `build_object_store`'s new explicit `elif` dispatch (unrecognized provider now
raises `ValueError` instead of silently falling through to S3), and 11 more from review round 1
for the shared `validate_object_path` allowlist -- nine refused path shapes parametrized across
all three backends (absolute, backslash-traversal, drive letter, `..`, `.`, `//`, NUL,
whitespace), an accepted-shapes test pinned against a real committed landing fixture path, and
a message-level test for the absolute-path branch. 32/32 passed, no regression
in the existing local/S3 tests. `MYPYPATH=libs/agno .venv/bin/mypy product/egpt_retail
--config-file product/pyproject.toml` found zero errors (including the new
`azure_blob.py`; no mypy override was needed for `azure.storage.blob` or
`azure.identity` -- both ship their own type stubs, unlike `boto3`). `ruff format` and
`ruff check` clean on every file touched.
