"""``egpt-retail pipelines`` -- the same app, with the scheduler poller running.

Scheduled workflow runs (nightly gold in R5, alert evaluation in R12) execute on this Deployment
rather than the API pods, by pointing the scheduler's callback at itself. The poller is safe to run
on many replicas -- ``claim_due_schedule`` is ``FOR UPDATE SKIP LOCKED`` -- but every replica must
hold the same ``EGPT_RETAIL_INTERNAL_SERVICE_TOKEN``; ``app/main.py`` refuses to start without one.
"""

from __future__ import annotations

from egpt_retail.entrypoints.api import main as serve

__all__ = ["main"]


def main(host: str = "0.0.0.0", port: int = 7777) -> int:
    return serve(host=host, port=port, role="pipelines")
