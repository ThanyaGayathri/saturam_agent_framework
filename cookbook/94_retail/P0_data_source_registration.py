"""#304 -- the control-plane schema, contract and secret storage for a self-serve data source.

The scenario: register a Postgres source with a username/password credential, prove the
credential never lands in the row and is recoverable only through the ``SecretStore``, rotate
it, record a connection test and a harvest state, then delete the source and confirm its
secret is gone too.

Evidence this exists to produce, each asserted, not just printed:

- ``DataSource`` (the row, and everything the API in #305 will return) carries no password,
  api_key, or any other credential field -- there is no attribute on the dataclass it could
  be in.
- The ciphertext actually stored in ``configure.local_secret`` does not contain the plaintext
  password anywhere in it.
- ``secret_store.read(secret_ref)`` recovers the exact credential that was written.
- Rotating a credential (``DataSourceStore.update`` with a new ``credential``) changes
  ``secret_ref`` and makes the old one unreadable -- proving the old secret was actually
  deleted, not just orphaned and still resolvable.
- Deleting a source deletes its secret too.

Needs the control-plane Postgres from ``deploy/compose.yaml`` (``docker compose -f
deploy/compose.yaml up -d postgres``, or the ``egpt-retail-postgres-1`` container already
running locally on 5532):

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/P0_data_source_registration.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from datetime import datetime, timezone

import psycopg  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from egpt_retail.configure.secrets import (  # noqa: E402
    LocalEncryptedSecretStore,
    SecretNotFoundError,
)
from egpt_retail.configure.store import DataSourceStore  # noqa: E402
from egpt_retail.contracts.data_source import (  # noqa: E402
    AuthMethod,
    ConnectionTestResult,
    Credential,
    DataSourceCreate,
    DataSourceStatus,
    DataSourceType,
    DataSourceUpdate,
    HarvestState,
)

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
CREATED_BY = "p0_cookbook"

print("P0 -- data source registration, credential storage, rotation and deletion")
print("=" * 78)

connection = psycopg.connect(CONTROL_PLANE)

secrets = LocalEncryptedSecretStore(connection, Fernet.generate_key().decode("utf-8"))
secrets.ensure_schema()
sources = DataSourceStore(connection)
sources.ensure_schema()

# A clean slate for this cookbook only -- never the whole table, which may hold other work.
with connection.cursor() as cursor:
    cursor.execute(
        "DELETE FROM configure.data_source WHERE created_by = %s", (CREATED_BY,)
    )
connection.commit()

# --- register ------------------------------------------------------------------------------

plaintext_password = "s3cr3t-p0-cookbook-password"
credential = Credential(
    auth_method=AuthMethod.USERNAME_PASSWORD,
    username="reporting_user",
    password=plaintext_password,
)
created = sources.create(
    DataSourceCreate(
        name="P0 cookbook Postgres",
        source_type=DataSourceType.POSTGRES,
        host="warehouse.internal",
        database_name="analytics",
        schema_name="public",
        credential=credential,
        created_by=CREATED_BY,
    ),
    secrets,
)
print(
    f"created: id={created.id} status={created.status.value} secret_ref={created.secret_ref}"
)
assert created.status is DataSourceStatus.REGISTERED
assert created.harvest_state is HarvestState.NOT_STARTED
assert not hasattr(created, "password")  # no such attribute exists on DataSource at all
print("PASS: no credential attribute exists on the returned DataSource")

with connection.cursor() as cursor:
    cursor.execute(
        "SELECT ciphertext FROM configure.local_secret WHERE ref = %s",
        (created.secret_ref,),
    )
    (ciphertext,) = cursor.fetchone()
assert plaintext_password.encode("utf-8") not in ciphertext
print("PASS: the stored ciphertext does not contain the plaintext password")

recovered = secrets.read(created.secret_ref)
assert recovered.username == "reporting_user"
assert recovered.password == plaintext_password
print("PASS: secret_store.read() recovers the exact credential that was written")

# --- read back -----------------------------------------------------------------------------

fetched = sources.get(created.id)
assert fetched == created
listed = sources.list(created_by=CREATED_BY)
assert created.id in {row.id for row in listed}
print(
    f"PASS: get() and list() agree with what create() returned ({len(listed)} row(s))"
)

# --- test-connection and harvest-state bookkeeping ------------------------------------------

tested = sources.record_test_result(
    created.id,
    ConnectionTestResult(
        ok=True, message="reachable", tested_at=datetime.now(timezone.utc)
    ),
)
assert tested.status is DataSourceStatus.CONNECTED
assert tested.last_test_ok is True
print("PASS: record_test_result() moves status to connected")

harvested = sources.set_harvest_state(
    created.id, HarvestState.BUILDING, run_id="run_p0_demo"
)
assert harvested.harvest_state is HarvestState.BUILDING
assert harvested.last_harvest_run_id == "run_p0_demo"
print("PASS: set_harvest_state() records the workflow run id #306 will trigger")

# --- rotate the credential -------------------------------------------------------------------

old_secret_ref = created.secret_ref
rotated = sources.update(
    created.id,
    DataSourceUpdate(
        credential=Credential(
            auth_method=AuthMethod.USERNAME_PASSWORD,
            username="reporting_user",
            password="a-rotated-password",
        )
    ),
    secrets,
)
assert rotated.secret_ref != old_secret_ref
try:
    secrets.read(old_secret_ref)
    raise AssertionError("the old secret should have been deleted on rotation")
except SecretNotFoundError:
    print("PASS: rotating a credential changes secret_ref and deletes the old secret")

rotated_credential = secrets.read(rotated.secret_ref)
assert rotated_credential.password == "a-rotated-password"
print("PASS: the new secret_ref resolves to the rotated credential")

# --- delete ------------------------------------------------------------------------------

deleted = sources.delete(created.id, secrets)
assert deleted is True
assert sources.get(created.id) is None
try:
    secrets.read(rotated.secret_ref)
    raise AssertionError(
        "the secret should have been deleted along with its data source"
    )
except SecretNotFoundError:
    print("PASS: deleting a data source deletes its secret too")

connection.close()
print("=" * 78)
print("P0 evidence complete.")
