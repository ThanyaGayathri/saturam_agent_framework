"""Reading the shipped ``.sql`` files.

The transforms are files, not strings in Python, so something has to read them. That is all
this module does. It reads through :mod:`importlib.resources` rather than ``Path(__file__)``
so the SQL is found in an installed wheel as well as in an editable checkout -- which is why
``product/pyproject.toml`` must carry a ``package-data`` glob for every directory under
``sql/``, and why ``product/tests/test_r4_sql_assets.py`` asserts it does.

Reads are cached after the first call. The files are shipped, immutable for the life of the
process, and executed once per load at most -- so there is no I/O worth an asynchronous
variant here, and an ``async def`` returning a cached string would be the dead weight
``CLAUDE.md`` names when it grants the exemption. The I/O that does deserve both variants is
the object store (``egpt_retail.storage``) and the database, not this.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import Tuple

from egpt_retail.warehouse.schema import (
    DDL_SCRIPTS,
    check_script,
    conform_script,
    defect_script,
)

__all__ = [
    "SQL_ROOT",
    "read_sql",
    "ddl_scripts",
    "conform_sql",
    "check_sql",
    "defect_sql",
]

#: Package-relative root of every shipped statement. ``ddl/`` is applied by
#: ``warehouse-migrate``; ``conform/`` and ``checks/`` are executed per load.
SQL_ROOT = "sql"


@lru_cache(maxsize=None)
def read_sql(relative_path: str) -> str:
    """One shipped ``.sql`` file, by its path relative to ``sql/``.

    ``read_sql("conform/crm_contact.sql")``. Raises ``FileNotFoundError`` naming the full
    package path when it is missing, because the likeliest cause is a ``package-data`` glob
    that does not cover the file -- a failure that appears only in an installed copy, and
    which a bare ``KeyError`` would not point at.
    """
    if relative_path.startswith("/") or ".." in relative_path.split("/"):
        raise ValueError(
            f"relative_path must stay inside {SQL_ROOT}/, got {relative_path!r}"
        )
    resource = files("egpt_retail.warehouse").joinpath(f"{SQL_ROOT}/{relative_path}")
    if not resource.is_file():
        raise FileNotFoundError(
            f"egpt_retail/warehouse/{SQL_ROOT}/{relative_path} is not present. In an "
            "installed copy this means product/pyproject.toml's package-data does not cover "
            "it; test_r4_sql_assets.py exists to catch exactly that."
        )
    return resource.read_text(encoding="utf-8")


def ddl_scripts() -> Tuple[Tuple[str, str], ...]:
    """Every DDL script in application order, as ``(filename, sql)`` pairs.

    Ordered, not sorted at read time: the order is a dependency order declared in
    :data:`~egpt_retail.warehouse.schema.DDL_SCRIPTS`, and sorting filenames would make the
    numeric prefixes load-bearing by accident rather than by statement.
    """
    return tuple((name, read_sql(f"ddl/{name}")) for name in DDL_SCRIPTS)


def conform_sql(stream_id: str) -> str:
    """The conformance statement for one landed stream."""
    return read_sql(f"conform/{conform_script(stream_id)}")


def check_sql(gate: str) -> str:
    """The measurement statement for one quality gate."""
    return read_sql(f"checks/{check_script(gate)}")


def defect_sql(name: str) -> str:
    """A defect-detection statement, by name."""
    return read_sql(f"defects/{defect_script(name)}")
