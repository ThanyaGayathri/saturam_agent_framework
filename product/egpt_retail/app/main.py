"""The uvicorn import target ``egpt_retail.app.main:app``.

Module-level ``agent_os`` and ``app``, because ``EgptOS.serve`` takes an import string and
``uvicorn --workers`` needs a module attribute, not a factory. The role is read from the
environment at import time: ``EGPT_RETAIL_ROLE=api`` serves requests, ``pipelines`` serves the
same routes and also runs the scheduler poller. That is the only difference between the two
Deployments, and it is a contention choice rather than a correctness one -- the poller claims
schedules with ``FOR UPDATE SKIP LOCKED`` and could run on every API pod -- made so nightly gold
materialisation (R5) never contends with interactive traffic.

The actual construction -- ``build_agent_os`` and ``OS_ID`` -- lives in ``app/factory.py``, kept
side-effect-free on import so it can be imported (by tests, or any other caller) without also
running the module-level assembly below. This module stays a thin uvicorn target: importing it
*is* what assembles the OS, on purpose, and ``serve()`` is what runs it (``entrypoints/api.py``,
``entrypoints/pipelines.py``); the lifespans it starts are the framework's own.
"""

from __future__ import annotations

from egpt_retail.app.factory import OS_ID, build_agent_os
from egpt_retail.app.settings import RetailSettings

__all__ = ["OS_ID", "build_agent_os", "settings", "agent_os", "app"]

settings = RetailSettings()
agent_os = build_agent_os(settings)
app = agent_os.get_app()
