"""K9 -- business area detection as a workflow step. Deterministic, like feasibility (K11).

Registered as ``detect_business_areas``. One executor, one name: the registry resolves by
``__name__`` over a flat list and nothing warns on a collision, so this name is reserved for
this function (``contracts/EXECUTOR_REGISTRY.md``).

There is no agent here and there is not going to be one. Whether a deployment can answer about
inventory is a graph query over confirmed mappings, and the moment a model decides it, the
answer stops being checkable -- the same argument that keeps feasibility deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.settings import RetailSettings
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology
from egpt_retail.semantic.areas import detect_areas, summary
from egpt_retail.semantic.store import SemanticGraphStore

__all__ = ["detect_business_areas"]


async def detect_business_areas(step_input: StepInput) -> StepOutput:
    """Which business areas this deployment supports, and the evidence for each.

    Returns every area the ontology covers, not only the supported ones: a screen showing
    only what works cannot tell a customer what connecting one more source would buy them.
    """
    options = step_input.additional_data or {}
    settings = RetailSettings()
    overlay: Optional[str] = (
        options.get("ontology_overlay") or settings.retail_ontology_overlay
    )

    from neo4j import GraphDatabase

    driver: Any = GraphDatabase.driver(
        settings.retail_neo4j_uri,
        auth=(settings.retail_neo4j_user, settings.retail_neo4j_password or ""),
    )
    try:
        store = SemanticGraphStore(
            driver=driver, database=settings.retail_neo4j_database
        )
        ontology = load_ontology(
            ARTIFACT_PATH, overlay_path=Path(overlay) if overlay else None
        )
        statuses = detect_areas(ontology, store.business_area_evidence())
    finally:
        driver.close()

    return StepOutput(
        content={
            **summary(statuses),
            "areas": [
                {
                    "name": status.name,
                    "supported": status.supported,
                    "concepts": list(status.concepts),
                    "tables": list(status.tables),
                    "reason": status.reason,
                }
                for status in statuses
            ],
        }
    )
