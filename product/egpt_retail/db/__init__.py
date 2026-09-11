"""Small, shared database-adjacent utilities for retail's own Postgres-backed stores.

Not a store itself, and not the framework's ``egpt.db`` -- each store here (``ProposalStore``,
``RehearsalReviewStore``, and others) still owns its own connection lifetime, schema and
queries. This package exists for the boilerplate that recurs across those stores rather than
for any one store's actual data.
"""

from __future__ import annotations

from egpt_retail.db.schema_utils import aensure_schema, ensure_schema

__all__ = ["ensure_schema", "aensure_schema"]
