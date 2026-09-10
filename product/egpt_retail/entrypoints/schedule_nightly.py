"""``egpt-retail schedule-nightly`` -- register the nightly refresh schedule (R5, D7).

Writes one row to the control plane's schedule table: ``nightly-refresh`` at 01:00 UTC,
calling ``POST /workflows/nightly-refresh/runs`` on the pipelines service, one retry. The
scheduler poller (``EGPT_RETAIL_ROLE=pipelines``) picks it up on its next poll; nothing here
talks to the running server, so this runs as a Helm ``post-install`` hook with only
``EGPT_RETAIL_DB_URL`` -- the same shape as ``migrate``.

Idempotent: ``if_exists="update"`` overwrites the existing schedule with these values, so a
changed cron or payload lands on the next upgrade rather than needing a delete first.

The payload carries ``message`` because the workflow run endpoint requires it as a form
field, and ``additional_data`` as a dict -- which the endpoint now JSON-parses back
(``egpt.os.utils.get_request_kwargs``); before that fix a scheduled run's bag arrived as a
string and every R4 executor refused it.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List, Optional

from egpt.scheduler.manager import ScheduleManager

from egpt_retail.app.components import NIGHTLY_REFRESH_WORKFLOW_ID
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings

__all__ = ["NIGHTLY_SCHEDULE_NAME", "DEFAULT_NIGHTLY_CRON", "nightly_payload", "main"]

log = logging.getLogger(__name__)

NIGHTLY_SCHEDULE_NAME = "nightly-refresh"
#: 01:00 UTC: after the upstream exports land (the landing contract's nightly drop), before
#: the working day, and two hours before the 03:00 harvest CronJob reads the silver it built.
DEFAULT_NIGHTLY_CRON = "0 1 * * *"


def nightly_payload(as_of: Optional[str] = None) -> Dict[str, Any]:
    """The run request. ``message`` is required by the endpoint; the bag is what R4 and R5 read."""
    bag: Dict[str, Any] = {"trigger": "schedule"}
    if as_of:
        bag["as_of"] = as_of
    return {"message": "nightly refresh", "additional_data": bag}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="egpt-retail schedule-nightly")
    parser.add_argument(
        "--cron", default=DEFAULT_NIGHTLY_CRON, help="Cron expression, UTC."
    )
    parser.add_argument(
        "--if-exists",
        default="update",
        choices=("raise", "skip", "update"),
        help="What to do when the schedule already exists (default: update in place).",
    )
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=3 * 3600)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = RetailSettings()
    db = build_control_plane_db(settings)
    manager = ScheduleManager(db)
    try:
        schedule = manager.create(
            name=NIGHTLY_SCHEDULE_NAME,
            cron=args.cron,
            endpoint=f"/workflows/{NIGHTLY_REFRESH_WORKFLOW_ID}/runs",
            method="POST",
            description="Landing to silver with quality gates, then gold materialisation (R5, D7).",
            payload=nightly_payload(),
            timezone="UTC",
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            retry_delay_seconds=300,
            if_exists=args.if_exists,
        )
    finally:
        manager.close()
    log.info(
        "schedule-nightly: %s id=%s cron=%r next_run_at=%s payload=%s",
        schedule.name,
        schedule.id,
        schedule.cron_expr,
        schedule.next_run_at,
        json.dumps(schedule.payload),
    )
    return 0
