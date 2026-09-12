"""``GET``/``PUT /business-areas``: the business hierarchy, with which areas the connected
data supports.

Two sources, composed here and kept apart everywhere else. The hierarchy itself -- the
areas, their labels and subject areas -- is the ``business_area`` vocabulary plus the
customer's overlay (:mod:`egpt_retail.ontology.business_areas`), read from disk on every
call so a ``PUT`` is visible to the next ``GET`` without a restart. Whether an area is
*supported* is K9's graph query (:mod:`egpt_retail.semantic.areas`): an area with at least
one confirmed mapping behind it, derived, never asserted -- the same computation
``detect_business_areas`` (``executors/areas.py``) runs as a workflow step.

When the graph cannot be asked, this router answers 503 rather than rendering every area
``supported: false``: "unsupported" is a claim about the data, and an unreachable graph
cannot make it. The contract's own stub takes the same line for an unloaded semantic reader.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, FrozenSet, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from egpt_retail.api import models as wire
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.semantic import semantic_driver
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.semantic import SemanticReadError
from egpt_retail.contracts.vocabulary import CORE_PATH, OverlayConflict
from egpt_retail.ontology.business_areas import (
    BusinessAreaHierarchy,
    BusinessAreaNode,
    apply_overlay,
    load_business_area_hierarchy,
    read_overlay,
    write_overlay,
)
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology
from egpt_retail.semantic.areas import (
    detect_areas,
    evidence_from_mappings,
    supported_areas,
)
from egpt_retail.semantic.store import SemanticGraphStore

__all__ = ["router"]

router = APIRouter()


def _overlay_path(settings: RetailSettings) -> Optional[Path]:
    raw = (settings.retail_ontology_overlay or "").strip()
    return Path(raw) if raw else None


def _hierarchy(settings: RetailSettings) -> BusinessAreaHierarchy:
    try:
        return load_business_area_hierarchy(overlay_path=_overlay_path(settings))
    except ValueError as exc:
        # A core file or overlay that fails its own rules is a deployment fault, not a
        # request fault: nothing the caller sends can fix it, so it is not a 4xx.
        raise HTTPException(
            status_code=503,
            detail=f"The business-area vocabulary cannot be read: {exc}",
        ) from exc


def _is_graph_error(exc: BaseException) -> bool:
    """The driver's own connectivity and auth failures. Imported here, not at module level,
    the way every other product module reaches for neo4j."""
    from neo4j.exceptions import DriverError, Neo4jError

    return isinstance(exc, (DriverError, Neo4jError))


def _unavailable(exc: BaseException) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "The semantic layer cannot be read, so which areas the connected data supports "
            f"cannot be answered: {exc}"
        ),
    )


def _supported(settings: RetailSettings) -> FrozenSet[str]:
    """K9, in one graph round trip: the areas some accepted concept with a confirmed table
    mapping falls in, grouped by the area the *ontology* puts the concept in."""
    overlay = _overlay_path(settings)
    try:
        ontology = load_ontology(
            ARTIFACT_PATH,
            overlay_path=overlay if overlay is not None and overlay.exists() else None,
        )
        driver = semantic_driver(settings)
        try:
            store = SemanticGraphStore(
                driver=driver, database=settings.retail_neo4j_database
            )
            evidence = evidence_from_mappings(ontology, store.mapped_concepts())
        finally:
            driver.close()
    except (RuntimeError, SemanticReadError) as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        # The overlay failed the ontology's own rules -- the same deployment fault
        # `_hierarchy` reports, and not something the caller can fix.
        raise HTTPException(
            status_code=503, detail=f"The ontology overlay cannot be read: {exc}"
        ) from exc
    except Exception as exc:
        if _is_graph_error(exc):
            raise _unavailable(exc) from exc
        raise
    return frozenset(supported_areas(detect_areas(ontology, evidence)))


def _area_out(area: BusinessAreaNode, supported: bool) -> wire.BusinessArea:
    return wire.BusinessArea(
        id=area.id,
        label=area.label,
        subject_areas=list(area.subject_areas),
        description=area.description,
        supported=supported,
        origin=wire.TermOrigin(area.origin.value),
    )


def _hierarchy_out(
    hierarchy: BusinessAreaHierarchy,
    supported: FrozenSet[str],
    *,
    only_supported: bool,
) -> wire.BusinessAreaHierarchy:
    areas: List[wire.BusinessArea] = [
        _area_out(area, area.id in supported)
        for area in hierarchy
        if not only_supported or area.id in supported
    ]
    return wire.BusinessAreaHierarchy(vocabulary_version=hierarchy.version, areas=areas)


@router.get("/business-areas", response_model=wire.BusinessAreaHierarchy)
def get_business_areas(
    supported: bool = False,
    _: None = Depends(require_scopes("config:read")),
) -> wire.BusinessAreaHierarchy:
    settings = RetailSettings()
    hierarchy = _hierarchy(settings)
    return _hierarchy_out(hierarchy, _supported(settings), only_supported=supported)


@router.put("/business-areas", response_model=wire.BusinessAreaHierarchy)
def set_business_areas(
    body: wire.BusinessAreaHierarchyWrite,
    _: None = Depends(require_scopes("configure:write")),
) -> wire.BusinessAreaHierarchy:
    settings = RetailSettings()
    path = _overlay_path(settings)
    if path is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "EGPT_RETAIL_ONTOLOGY_OVERLAY is not set, so this deployment has no overlay "
                "to apply business areas to. Point it at the deployment's overlay file; the "
                "file is created on the first write."
            ),
        )
    core_payload: Any = json.loads(CORE_PATH.read_text())
    areas = [
        BusinessAreaNode(
            id=item.id,
            label=item.label,
            description=item.description or "",
            subject_areas=tuple(item.subject_areas or ()),
        )
        for item in body.areas
    ]
    try:
        payload = apply_overlay(read_overlay(path), areas, core_payload)
    except OverlayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    write_overlay(path, payload)
    return _hierarchy_out(
        _hierarchy(settings), _supported(settings), only_supported=False
    )
