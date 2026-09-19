"""#305 -- ``api/routers/sources.py`` wired for real: the actual FastAPI router, the actual
``DataSourceStore``, and the actual ``LocalEncryptedSecretStore``, all against the real
control-plane Postgres. ``test_p1_sources_router.py`` proves the router's own logic with
in-memory fakes; this is the other half -- that the real pieces actually fit together, and
that ``POST /configure/sources/{id}/test`` really does refuse a loopback host before
attempting a connection, over real HTTP, not just at the unit level.

Needs the control-plane Postgres from ``deploy/compose.yaml`` (or the
``egpt-retail-postgres-1`` container already running locally on 5532):

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/P1_sources_router.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from egpt_retail.api.routers import sources as sources_module  # noqa: E402
from egpt_retail.configure.secrets import LocalEncryptedSecretStore  # noqa: E402
from egpt_retail.configure.store import DataSourceStore  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
CREATED_BY = "p1_cookbook"

print("P1 -- the real sources router, against real Postgres")
print("=" * 78)

connection = psycopg.connect(CONTROL_PLANE)
secrets = LocalEncryptedSecretStore(connection, Fernet.generate_key().decode("utf-8"))
secrets.ensure_schema()
store = DataSourceStore(connection)
store.ensure_schema()

with connection.cursor() as cursor:
    cursor.execute(
        "DELETE FROM configure.data_source WHERE created_by = %s", (CREATED_BY,)
    )
connection.commit()

app = FastAPI()
app.include_router(sources_module.router)
app.dependency_overrides[sources_module._source_store] = lambda: store
app.dependency_overrides[sources_module._secret_store] = lambda: secrets


@app.middleware("http")
async def _fake_auth(request, call_next):
    # A stand-in for AuthMiddleware -- this cookbook proves the router's own wiring against
    # real storage, not the authorization layer, which api/routers/*.py's own live tests
    # already cover.
    request.state.authorization_enabled = True
    request.state.scopes = ["config:read", "configure:write"]
    request.state.admin_scope = None
    request.state.user_id = CREATED_BY
    return await call_next(request)


client = TestClient(app)

write_body = {
    "source_type": "postgres",
    "name": "P1 cookbook Postgres",
    "host": "warehouse.internal",
    "database_name": "analytics",
    "schema": "silver",
    "auth_method": "username_password",
    "username": "reporting_user",
    "password": "s3cr3t-p1-cookbook-password",
}

created = client.post("/configure/sources", json=write_body).json()
assert "password" not in created and "api_key" not in created
assert created["has_credentials"] is True
assert created["username"] == "reporting_user"
print(
    f"POST /configure/sources -> 201, id={created['id']}, no credential in the body. PASS"
)

fetched = client.get(f"/configure/sources/{created['id']}").json()
assert fetched == created
print("GET  /configure/sources/{id} -> matches what POST returned. PASS")

listing = client.get("/configure/sources").json()
assert created["id"] in {row["id"] for row in listing}
print(f"GET  /configure/sources -> {len(listing)} row(s), includes the new one. PASS")

patched = client.patch(
    f"/configure/sources/{created['id']}", json={"name": "Renamed by P1 cookbook"}
).json()
assert patched["name"] == "Renamed by P1 cookbook"
assert patched["host"] == created["host"]
print("PATCH /configure/sources/{id} -> renamed, other fields untouched. PASS")

# --- test-connection: a REAL HTTP call to the REAL router, refusing a REAL loopback host ---
#
# A second row, on localhost -- 'warehouse.internal' above never resolves at all, which
# would refuse for a slower, less interesting reason (a real ~5s connection timeout inside
# the driver, not the SSRF guard). localhost resolves and is loopback, so the guard is what
# refuses it, immediately, before any connection is attempted.

loopback_write = dict(write_body, name="P1 cookbook loopback probe", host="localhost")
loopback_source = client.post("/configure/sources", json=loopback_write).json()
loopback_test = client.post(f"/configure/sources/{loopback_source['id']}/test").json()
assert loopback_test["ok"] is False
assert "loopback" in loopback_test["detail"] or "link-local" in loopback_test["detail"]
print(
    f"POST .../test against host='localhost' -> refused pre-connection: "
    f"{loopback_test['detail']!r}. PASS"
)

# InternalSource has no `status` field on the wire (has_credentials/last_harvest_id are the
# only connection-adjacent facts the contract exposes) -- reading the store directly is the
# only way to prove record_test_result actually persisted, which the router's HTTP response
# alone cannot show.
after_test = store.get(loopback_source["id"])
assert after_test is not None and after_test.status.value == "error"
assert after_test.last_test_ok is False
print(
    "Test result actually persisted on the row (status=error, last_test_ok=False). PASS"
)

assert client.delete(f"/configure/sources/{created['id']}").status_code == 204
assert client.delete(f"/configure/sources/{loopback_source['id']}").status_code == 204
assert client.get(f"/configure/sources/{created['id']}").status_code == 404
print("DELETE /configure/sources/{id} -> 204, then 404 on re-fetch. PASS")

connection.close()
print("=" * 78)
print("P1 evidence complete.")
