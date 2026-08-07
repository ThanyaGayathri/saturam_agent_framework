"""One real (unstubbed) MySQL engine construction, driver-gated.

``tests/unit/db/test_db_constructor_contracts.py`` deliberately stubs the engine
factories so it runs on driverless environments, which removed the suite's last real
``mysql+pymysql`` dialect resolution -- the integration suite that would otherwise
cover it is the subject of aicoe-tracker#154. This file restores that single smoke
path: real ``create_engine``, real dialect resolution, no connection opened
(SQLAlchemy connects lazily).
"""

import pytest

pytest.importorskip("pymysql", reason="real dialect resolution needs the driver")

from egpt.integrations.db.mysql.mysql import MySQLDb  # noqa: E402


def test_mysql_engine_builds_with_the_real_dialect():
    db = MySQLDb(db_url="mysql+pymysql://u:p@host-a:3306/db")

    assert db.db_engine is not None
    assert db.db_engine.url.drivername == "mysql+pymysql"
    # The deterministic id must agree with what the stubbed contracts file pins.
    assert db.id == MySQLDb(db_url="mysql+pymysql://u:p@host-a:3306/db").id
