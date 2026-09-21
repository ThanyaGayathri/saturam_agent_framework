"""The diagnostic kind of Analytics Automation (K25): why a KPI changed, computed fresh on
every run. ``parts`` reads a KPI's formula into the pieces a decomposition needs; ``decompose``
is the arithmetic -- contribution, rate and mix effects, surprise against a slice's own
history, the support floor, the ranking -- over rows, with nothing to be persuaded by. The
executor in ``executors/diagnostics.py`` fetches the rows and calls these.
"""

from egpt_retail.diagnostics.decompose import (
    Driver,
    DimensionResult,
    SENSITIVITY,
    decompose_dimension,
    rank_dimensions,
    robust_z,
    support_floor,
)
from egpt_retail.diagnostics.parts import FormulaParts, parts_of

__all__ = [
    "Driver",
    "DimensionResult",
    "FormulaParts",
    "SENSITIVITY",
    "decompose_dimension",
    "parts_of",
    "rank_dimensions",
    "robust_z",
    "support_floor",
]
