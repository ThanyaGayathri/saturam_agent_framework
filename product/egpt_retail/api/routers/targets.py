"""``/targets`` and ``/record-mappings`` (K18): the write side as data.

The objects a target lets the platform create, and their createable fields, are read live
with the platform's own credentials so the form maps to what the org actually has. The two
shipped mappings are served as rows of the mapping shape; a suggestion is proposed
deterministically from names over the live fields. Salesforce is the one target today; the
path carries the target's name so a second one is a second client, not a second contract.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from egpt_retail.api import models as wire
from egpt_retail.app.scopes import require_scopes
from egpt_retail.documents.mapping import (
    SHIPPED_MAPPINGS,
    mapping_to_dict,
    suggest_mapping,
    suggest_objects,
)
from egpt_retail.documents.templates import template_from
from egpt_retail.usecases.credentials import CredentialUnavailable, resolve_credentials

__all__ = ["router", "target_client"]

router = APIRouter()


def target_client(target: str = "salesforce") -> Any:
    """A connected client for the target, with the platform's credentials. ``target`` is the
    path parameter on the ``/targets`` routes and a query parameter defaulting to salesforce
    on the suggest route. Overridden in tests."""
    if target != "salesforce":
        raise HTTPException(
            status_code=404,
            detail=f"No target {target!r}; salesforce is the one this deployment writes.",
        )
    from egpt_retail.documents.salesforce import client_from

    try:
        credentials = resolve_credentials({}, kind="salesforce")
        return client_from(credentials)
    except CredentialUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (
        Exception
    ) as exc:  # the token request itself failing is the target being unreachable
        raise HTTPException(status_code=503, detail=str(exc)[:300])


@router.get("/targets/{target}/objects", response_model=List[wire.TargetObject])
async def list_target_objects(
    target: str,
    client: Any = Depends(target_client),
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.TargetObject]:
    from egpt_retail.documents.salesforce import alist_objects

    try:
        rows = await alist_objects(client)
    finally:
        client.close()
    return [wire.TargetObject.model_validate(r) for r in rows]


@router.get(
    "/targets/{target}/objects/{object}", response_model=wire.TargetObjectDescription
)
async def describe_target_object(
    target: str,
    object: str,
    client: Any = Depends(target_client),
    _: None = Depends(require_scopes("use-cases:read")),
) -> wire.TargetObjectDescription:
    from egpt_retail.documents.salesforce import adescribe_object

    try:
        described = await adescribe_object(client, object)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0]))
    finally:
        client.close()
    return wire.TargetObjectDescription.model_validate(described)


@router.get("/record-mappings", response_model=List[wire.RecordMapping])
def list_record_mappings(
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.RecordMapping]:
    return [
        wire.RecordMapping.model_validate(mapping_to_dict(m))
        for m in SHIPPED_MAPPINGS.values()
    ]


@router.post(
    "/record-mappings/suggest-objects", response_model=List[wire.ObjectSuggestion]
)
async def suggest_target_objects(
    body: wire.ObjectSuggestRequest,
    client: Any = Depends(target_client),
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.ObjectSuggestion]:
    from egpt_retail.documents.salesforce import alist_objects

    try:
        template = template_from(body.template.model_dump(mode="json"))
    except (KeyError, ValueError) as exc:
        client.close()
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        objects = await alist_objects(client)
    finally:
        client.close()
    return [
        wire.ObjectSuggestion.model_validate(row)
        for row in suggest_objects(
            template, objects, description=body.description or ""
        )
    ]


@router.post("/record-mappings/suggest", response_model=wire.RecordMapping)
async def suggest_record_mapping(
    body: wire.MappingSuggestRequest,
    client: Any = Depends(target_client),
    _: None = Depends(require_scopes("use-cases:read")),
) -> wire.RecordMapping:
    from egpt_retail.documents.salesforce import adescribe_object

    try:
        template = template_from(body.template.model_dump(mode="json"))
    except (KeyError, ValueError) as exc:
        client.close()
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        parent = await adescribe_object(client, body.parent)
        child: Optional[dict] = (
            await adescribe_object(client, body.child) if body.child else None
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0]))
    finally:
        client.close()
    fields = {
        "header": [n for n, _ in template.header],
        "lines": [n for n, _ in template.lines],
    }
    suggested = suggest_mapping(fields, parent, child, label=body.label or None)
    suggested["source"] = "suggested"
    return wire.RecordMapping.model_validate(suggested)
