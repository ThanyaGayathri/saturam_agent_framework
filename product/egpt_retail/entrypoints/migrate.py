"""``egpt-retail migrate`` -- control-plane schema migrations to the latest version, then exit.

Deployed as a Helm ``pre-install,pre-upgrade`` hook Job (R2), never as an initContainer: with N
replicas an initContainer is N concurrent migrations.

What it does and does not touch:

* It migrates the **control plane only**. ``MigrationManager._TABLE_TYPE_TO_ATTR`` covers the
  framework's relational tables; the warehouse (landing, silver, gold) holds none of them, and its
  DDL is R4's, executed as SQL beside the transforms.
* On a fresh database it is **not** a no-op: verified by hand, ``up()`` applied 2.7.0 through
  2.10.0 and created the memory-tier tables (``egpt_memory_link`` and friends) on first run, then
  stored version 2.10.0 per table. The second run skipped every table as already at target. So
  the Job does real work on a clean install and is idempotent by skip afterwards; the evidence
  records both runs.
* The pgvector memory table is not a migration concern: it self-provisions and is invisible to
  the runner.
* R7's two control-plane tables (``egpt_answer_provenance``, ``retail_deliveries``) are applied
  after ``MigrationManager.up()``, over a bare ``Engine`` rather than through the manager --
  the same reasoning as the warehouse bullet above: neither is one of
  ``MigrationManager._TABLE_TYPE_TO_ATTR``'s curated types, and it should stay that way
  (tracker #178).
"""

from __future__ import annotations

import asyncio
import logging

from egpt.answer_provenance import aapply_provenance_ddl
from egpt.db.migrations.manager import MigrationManager

from egpt_retail.app.db import build_control_plane_db, build_control_plane_engine
from egpt_retail.app.settings import RetailSettings
from egpt_retail.delivery import aapply_delivery_ddl

__all__ = ["main", "amain"]

log = logging.getLogger("egpt_retail.migrate")


async def amain() -> int:
    """Run the migrations. The async entry, because ``MigrationManager.up`` is async-only."""
    settings = RetailSettings()
    db = build_control_plane_db(settings)
    manager = MigrationManager(db)
    log.info(
        "migrate: control plane %s -> latest schema %s",
        db.id,
        manager.latest_schema_version,
    )
    await manager.up()

    engine = build_control_plane_engine(settings)
    try:
        for name in await aapply_provenance_ddl(engine):
            log.info("migrate: applied %s", name)
        for name in await aapply_delivery_ddl(engine):
            log.info("migrate: applied %s", name)
    finally:
        engine.dispose()

    log.info("migrate: done")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(amain())
