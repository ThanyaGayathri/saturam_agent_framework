"""Delivery-record storage for R7's ``deliver_in_app`` executor (G4).

Retail-specific -- unlike the provenance record, "delivering in-app" is not a generic
framework concept, so this stays product code. Mirrors ``egpt_retail.warehouse``'s own split
between DDL/storage and the executor that calls into it.
"""

from egpt_retail.delivery.ddl import (
    DELIVERY_TABLE,
    aapply_delivery_ddl,
    apply_delivery_ddl,
)
from egpt_retail.delivery.store import awrite_delivery, write_delivery

__all__ = [
    "DELIVERY_TABLE",
    "aapply_delivery_ddl",
    "apply_delivery_ddl",
    "awrite_delivery",
    "write_delivery",
]
