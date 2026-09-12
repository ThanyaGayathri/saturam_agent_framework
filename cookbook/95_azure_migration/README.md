# 95 · Azure migration

Runnable checks for the Azure migration plan (`docs/plan/azure-migration-plan.md`), one folder
for the whole initiative rather than mixed into `cookbook/94_retail/`'s retail-specific log,
per that plan's own "How each deliverable is verified" section. One folder, one `TEST_LOG.md`,
entries tagged by package (AZ-A/B/C/D).

| Script | Package | What it proves |
|---|---|---|
| `AZ_A_blob_roundtrip.py` | AZ-A | `AzureBlobObjectStore` against a real local Azurite: lists and reads C1's committed landing fixtures byte-for-byte identically to `LocalDirectoryStore`, refuses a path that escapes the landing root, and refuses a half-configured credential pair at construction. Needs Docker (Azurite). |
