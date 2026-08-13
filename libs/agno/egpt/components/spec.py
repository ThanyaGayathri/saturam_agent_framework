"""The component contract core validates against (EGPT-2.2).

``egpt.registry.schema`` owns the schema and every check it can answer from the discovery seam
and the ``Registry``, but it lives in core and so cannot name ``Agent`` / ``Team`` / ``Workflow``.
This module is the other half: it is in orchestration, where importing the component classes is
allowed, and it hands core the ``ComponentSpec`` that closes the dry-run hydration check.

It sits here rather than under ``egpt.os`` -- where it started -- because two callers need it
now and only one of them is an HTTP handler. Importing anything under ``egpt.os`` executes
``from egpt.os.app import EgptOS`` and pulls in FastAPI, which a seed script running against a
database has no business requiring. ``egpt.os.routers.components.validation`` re-exports both
functions, so the router and its tests are unaffected.
"""

from typing import Any, Dict, List, Optional

from egpt.agent import Agent
from egpt.db.base import BaseDb
from egpt.registry import Registry
from egpt.registry.policy import CapabilityPolicy
from egpt.registry.schema import ComponentSpec, validate_config
from egpt.team import Team
from egpt.workflow import Workflow

__all__ = ["component_spec_for", "validate_component_config"]


def component_spec_for(
    component_type: str,
    *,
    registry: Optional[Registry] = None,
    db: Optional[BaseDb] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    tenant_id: Optional[str] = None,
) -> ComponentSpec:
    """The `ComponentSpec` for a component type, built by the class that owns the contract.

    tenant_id (EGPT-2.5) scopes the member/step hydration inside the dry run for teams and
    workflows, so a write-time validation cannot probe another tenant's component ids. Agents
    take no tenant: their spec strips `db`, so their dry run cannot reach the database.
    """
    if component_type == "agent":
        return Agent.component_spec(registry=registry)
    if component_type == "team":
        return Team.component_spec(registry=registry, db=db, tenant_id=tenant_id)
    if component_type == "workflow":
        return Workflow.component_spec(registry=registry, db=db, links=links, tenant_id=tenant_id)
    raise ValueError(f"Unknown component type '{component_type}'.")


def validate_component_config(
    config: Dict[str, Any],
    component_type: str,
    *,
    db: Optional[BaseDb] = None,
    registry: Optional[Registry] = None,
    component_id: Optional[str] = None,
    unresolved_db_id: Optional[str] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    policy: Optional[CapabilityPolicy] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a config and return the version-stamped dict to persist.

    `policy` and `tenant_id` add the EGPT-2.3 capability check on top of the EGPT-2.2 schema
    check. Both default to None, which skips it -- a deployment that has configured no policy
    validates exactly as it did before.

    Raises:
        ConfigValidationError: the config is not a valid definition, or names a capability this
            tenant may not use. Callers must catch this **above** any `except ValueError` arm --
            it subclasses `ValueError`, so a handler in the wrong order flattens the field-level
            errors into a plain 400 string.
    """
    spec = component_spec_for(component_type, registry=registry, db=db, links=links, tenant_id=tenant_id)
    return validate_config(
        config,
        component_type,
        registry=registry,
        spec=spec,
        unresolved_db_id=unresolved_db_id,
        self_component_id=component_id,
        policy=policy,
        tenant_id=tenant_id,
    )
