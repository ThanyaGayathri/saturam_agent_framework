"""Writing delivery records (R7, G4).

Write-only today: nothing reads this table back yet (R11's playground, P4, is the natural
owner of rendering a delivered answer -- this table exists so ``output_ref`` is a real,
dereferenceable id now, not scope for building R11's own storage early).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.delivery.ddl import DELIVERY_TABLE

__all__ = ["write_delivery", "awrite_delivery"]

_INSERT = f"""
INSERT INTO {DELIVERY_TABLE}
    (id, run_id, use_case_id, channel, dry_run, content, delivered_at)
VALUES
    (:id, :run_id, :use_case_id, :channel, :dry_run, CAST(:content AS jsonb), :delivered_at)
"""


def write_delivery(
    engine: Engine,
    *,
    id: str,
    run_id: Optional[str],
    use_case_id: Optional[str],
    channel: str,
    dry_run: bool,
    content: Dict[str, Any],
    delivered_at: Optional[datetime],
) -> None:
    """Write one delivery row."""
    params = {
        "id": id,
        "run_id": run_id,
        "use_case_id": use_case_id,
        "channel": channel,
        "dry_run": dry_run,
        "content": json.dumps(content, default=str),
        "delivered_at": delivered_at,
    }
    with engine.begin() as connection:
        connection.execute(text(_INSERT), params)


async def awrite_delivery(
    engine: Engine,
    *,
    id: str,
    run_id: Optional[str],
    use_case_id: Optional[str],
    channel: str,
    dry_run: bool,
    content: Dict[str, Any],
    delivered_at: Optional[datetime],
) -> None:
    """:func:`write_delivery` off the event loop."""
    await asyncio.to_thread(
        write_delivery,
        engine,
        id=id,
        run_id=run_id,
        use_case_id=use_case_id,
        channel=channel,
        dry_run=dry_run,
        content=content,
        delivered_at=delivered_at,
    )
