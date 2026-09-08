"""``egpt-retail api`` -- serve the OS without the scheduler poller.

Goes through ``EgptOS.serve`` rather than a raw ``uvicorn.run`` because ``serve`` forces
``lifespan="on"`` (``os/app.py:2079``), which is what starts ``db_lifespan`` (table provisioning)
and, for the pipelines role, ``scheduler_lifespan``. ``EGPT_AGENT_OS_HOST`` / ``EGPT_AGENT_OS_PORT``
override the arguments inside ``serve`` (``os/app.py:2033-2036``); that is how the chart sets them.
"""

from __future__ import annotations

import os

__all__ = ["main"]

APP_IMPORT = "egpt_retail.app.main:app"


def main(host: str = "0.0.0.0", port: int = 7777, role: str = "api") -> int:
    # The role is read by app/main.py at import time, so it is pinned before the import.
    os.environ["EGPT_RETAIL_ROLE"] = role
    from egpt_retail.app.main import agent_os

    agent_os.serve(app=APP_IMPORT, host=host, port=port, access_log=True)
    return 0
