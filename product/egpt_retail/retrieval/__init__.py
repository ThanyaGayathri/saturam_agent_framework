"""K10 -- one way to retrieve, across every store this deployment has.

The framework owns the fan-out, the labelling and the fusion
(``egpt.memory.retrieval.unified``). This package owns the two legs only a retail deployment
has: the shipped ontology (K3) and the customer's semantic layer (K5).
"""

from egpt_retail.retrieval.legs import (
    ontology_leg,
    retail_legs,
    retail_unified_read,
    semantic_leg,
)

__all__ = ["ontology_leg", "retail_legs", "retail_unified_read", "semantic_leg"]
