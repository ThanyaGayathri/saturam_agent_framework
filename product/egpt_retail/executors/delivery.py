"""``deliver_in_app`` (R7, G4): posts the answer in-app.

Minimal by design: a dedicated ``retail_deliveries`` row, not new channel/report plumbing --
R11's playground (P4) owns rendering a delivered answer; building that storage early here
would be scope creep into R11. This is also the one executor the dry-run flag touches: every
other R7 step runs for real regardless.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.db import build_control_plane_engine
from egpt_retail.app.settings import RetailSettings
from egpt_retail.delivery import awrite_delivery

__all__ = ["DRY_RUN_KEY", "deliver_in_app"]

#: The convention key a caller sets via ``Workflow.arun(additional_data={DRY_RUN_KEY: True})``
#: to preview a delivery without sending it. Exported so both the workflow composer and this
#: module agree on the exact string.
DRY_RUN_KEY = "dry_run"


def _delivery_payload(
    *, dry_run: bool, channel: str, explanation: Any
) -> Dict[str, Any]:
    """The content shape returned to the caller and stored in the row. Pure and separated out
    specifically so the dry-run-vs-real content difference is unit-testable with no database.
    """
    return {
        "channel": channel,
        "dry_run": dry_run,
        "explanation": explanation,
        "status": "previewed" if dry_run else "delivered",
    }


async def deliver_in_app(step_input: StepInput) -> StepOutput:
    """Deliver -- or, under the dry-run flag, report what would have been delivered.

    Reads ``step_input.previous_step_content`` (the immediately-preceding step's output)
    rather than a name-keyed lookup, matching the API contract's example plan where this step
    always directly follows ``record_provenance`` -- which forwards whatever it received
    under ``explanation``. ``additional_data["explanation"]`` still overrides it, for a
    caller (or the cookbook's offline harness) that wants to hand this a value directly.
    """
    options: Dict[str, Any] = step_input.additional_data or {}
    dry_run = bool(options.get(DRY_RUN_KEY, False))

    previous = step_input.previous_step_content
    provenance_ref: Optional[str] = (
        previous.get("provenance_ref") if isinstance(previous, Mapping) else None
    )
    previous_explanation = (
        previous.get("explanation") if isinstance(previous, Mapping) else None
    )
    explanation = options.get("explanation", previous_explanation)

    payload = _delivery_payload(
        dry_run=dry_run, channel="in_app", explanation=explanation
    )
    delivery_id = str(uuid.uuid4())

    engine = build_control_plane_engine(RetailSettings())
    try:
        await awrite_delivery(
            engine,
            id=delivery_id,
            run_id=provenance_ref,
            use_case_id=options.get("use_case_id"),
            channel="in_app",
            dry_run=dry_run,
            content=payload,
            delivered_at=None if dry_run else datetime.now(timezone.utc),
        )
    finally:
        engine.dispose()

    return StepOutput(content={"output_ref": delivery_id, **payload})
