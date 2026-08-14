"""
Admin Console
=============

Serves the built-in admin console (EGPT-2.9) at http://localhost:7777/console.

The console is a minimal authoring UI over the /components API: list definitions, edit a draft,
validate it, publish it, and roll back to an earlier published version. It is opt-in via
enable_console=True, and it ships inside the package -- there is no build step and no CDN.

The page itself is served without a credential (a browser navigating to it has none). Every call
it then makes carries a bearer token you paste in, so scopes and tenancy are enforced exactly as
they are for any other API client. This script mints a service-account token for you and prints
it, which is the same flow an operator would use.
"""

import time

from egpt.db.schemas.service_accounts import ServiceAccount
from egpt.integrations.db.sqlite import SqliteDb
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.service_accounts import generate_token
from egpt.registry import Registry

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# SQLite for a local walkthrough; use PostgresDb in production.
db = SqliteDb(db_file="tmp/admin_console.db", id="os-db")

# hydrate_published_only is the production posture: without it a runtime falls back to the latest
# draft for a component that has never been published, so "publish" would not be a gate.
registry = Registry(hydrate_published_only=True)

agent_os = EgptOS(
    db=db,
    id="admin-console-demo",
    registry=registry,
    enable_console=True,
    # A tenant claim turns on row-level tenancy, so the console only ever lists and edits the
    # components belonging to the token's tenant.
    authorization_config=AuthorizationConfig(tenant_id_claim="tid"),
)

app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Mint an admin token
# ---------------------------------------------------------------------------
def mint_admin_token(tenant_id: str) -> str:
    """A service account scoped to one tenant, with the three component scopes."""
    token, token_hash, prefix = generate_token()
    db.create_service_account(
        ServiceAccount(
            id=f"console-admin-{tenant_id}",
            name=f"console-admin-{tenant_id}",
            token_hash=token_hash,
            token_prefix=prefix,
            scopes=["components:read", "components:write", "components:delete"],
            created_at=int(time.time()),
            tenant_id=tenant_id,
        ).to_dict()
    )
    return token


# ---------------------------------------------------------------------------
# Run EgptOS App
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    admin_token = mint_admin_token("acme")

    print("Admin console:  http://localhost:7777/console")
    print("Tenant:         acme")
    print("Bearer token:   " + admin_token)
    print("")
    print("Open the console, paste the token when prompted, then:")
    print("  1. New component -> fill in a name and a model, press Validate")
    print("  2. Remove the model to see the field-level error inline, then put it back")
    print("  3. Create component -> Publish v1")
    print(
        "  4. Edit, save a new draft, publish v2, then Make current on v1 to roll back"
    )
    print("")
    print(
        "The EGPT_OS_SECURITY_KEY environment variable must be set for the auth layer to load."
    )

    agent_os.serve(app="admin_console:app", reload=False)
