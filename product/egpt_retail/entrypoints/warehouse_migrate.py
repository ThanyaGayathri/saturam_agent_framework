"""``egpt-retail warehouse-migrate`` -- create or update the warehouse schema (R4).

Separate from ``migrate``, which is the control plane's, for three reasons that all bite on
AKS. The warehouse is a different Flexible Server with different credentials and a different
network path (D5). The control-plane migration runs as a ``pre-install`` Helm hook that gates
the entire release, and must not be blocked by warehouse reachability. And ``migrate.py``'s
stated scope -- "it migrates the control plane only" -- stays true rather than becoming a
docstring nobody trusts.

Idempotent: run it as often as you like. There is no version ledger, because every script it
applies is ``CREATE ... IF NOT EXISTS`` or ``CREATE OR REPLACE``.
"""

from __future__ import annotations

import logging

from egpt_retail.app.db import build_warehouse_engine
from egpt_retail.app.settings import RetailSettings
from egpt_retail.warehouse.ddl import apply_warehouse_ddl

__all__ = ["main"]

log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = RetailSettings()
    engine = build_warehouse_engine(settings)
    try:
        applied = apply_warehouse_ddl(engine)
    finally:
        # The process exits straight after, but an engine left open holds a pooled connection
        # that the Helm hook Job's pod would sit on until it is reaped. R1's review found the
        # same leak in the consolidator; not repeating it here.
        engine.dispose()
    log.info("warehouse-migrate: applied %d scripts", len(applied))
    log.info("warehouse-migrate: done")
    return 0
