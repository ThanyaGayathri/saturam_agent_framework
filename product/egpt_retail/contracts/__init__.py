"""The P0 contracts, agreed before the code that depends on them is written.

``kpi``      -- the KPI formula shape (K11's definition half). Lane K to Lane R.
``semantic`` -- the semantic read API (K5's read surface, stubbed in P0). Lane K to Lane R.
``landing``  -- the landing contract and storage layout (C1). Lane C to Lane R.

All three follow the change protocol in ``docs/plan/delivery-plan.md``: additive changes are
free, breaking changes need both lanes to agree and a version bump, and every change is
recorded in ``product/egpt_retail/contracts/CHANGELOG.md``.
"""
