"""The Lane K contracts, agreed in P0 and built against by Lane R.

``kpi``      -- the KPI formula shape (K11's definition half, agreed in P0).
``semantic`` -- the semantic read API (K5's read surface, stubbed in P0).

Both follow the change protocol in ``docs/plan/delivery-plan.md``: additive changes are
free, breaking changes need both lanes to agree and a version bump, and every change is
recorded in ``product/egpt_retail/contracts/CHANGELOG.md``.
"""
