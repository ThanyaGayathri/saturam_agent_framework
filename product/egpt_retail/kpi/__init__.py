"""The KPI layer: definitions, the registry that holds them, and feasibility.

``retail_metrics.json`` is the harvested catalogue of retail metric *templates* -- an input,
not a registry, and not executable. ``store`` is the registry itself (K11): authoritative in
Postgres, mirrored into the graph so a concept reaches its metrics by traversal.

Feasibility lives in ``contracts.kpi`` as a pure set difference and is deliberately not
stored anywhere: a cached verdict goes stale the moment a harvest runs.
"""
