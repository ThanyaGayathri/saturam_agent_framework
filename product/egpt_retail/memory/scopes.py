"""The scopes the consolidator drains, from settings.

Follows Lane K's ``MEMORY_SCOPE.md`` rather than inventing: a scheduled run writes as
``user_id="__scheduler__"`` -- the identity the framework already assigns (``os/auth.py``),
reserves (``os/middleware/jwt.py``) and defends (``os/service_accounts.py``) -- so the
consolidator drains that partition, per agent that writes memory.

**Phase zero covers the ``__scheduler__`` partition only, and says so.** Consolidating a person's
memory needs the set of users, which is a database question, and there is no slug on any run
context until K1 stamps it. Scope narrowing is conjunctive over every set slot
(``agent/_managers.py:555-559``), so a scope carrying ``user_id="__scheduler__"`` reaches exactly
the episodes written under that user and nothing a person wrote. That is the intended boundary,
not a leak. Whether the consolidation scope should carry ``agent_id`` at all -- one pass per
agent partition, as here, or one per tenant -- is Lane K's call and is raised as an open
question in ``cookbook/94_retail/TEST_LOG.md``.

``consolidation_scope_source`` returns the **callable** form of ``ScopeSource``, which the worker
re-consults every tick, so the P1 upgrade from "scopes from config" to "scopes from a query over
the episodic tier" is a change inside one function with no worker change and no restart.

An unset slug yields an empty list and does **not** raise. The entrypoint decides what empty
means, and R1's evidence requires that it means "nothing to consolidate", not an error.
"""

from __future__ import annotations

import asyncio
from typing import Callable, List, Sequence

from egpt.memory.types import Scope

from egpt_retail.app.settings import RetailSettings

__all__ = [
    "SYSTEM_USER_ID",
    "build_consolidation_scopes",
    "abuild_consolidation_scopes",
    "consolidation_scope_source",
]

#: The framework's own scheduler principal. Not a new reserved word: ``INTERNAL_SCHEDULER_USER_ID``
#: in ``egpt/os/middleware/jwt.py``. Spelled here rather than imported so this module, like the
#: rest of the memory seam, does not pull the OS middleware into a batch process.
SYSTEM_USER_ID = "__scheduler__"


def build_consolidation_scopes(
    settings: RetailSettings, agent_ids: Sequence[str]
) -> List[Scope]:
    """One write scope per memory-writing agent, under the scheduler identity.

    ``Scope(tenant_id=slug, agent_id=<id>, user_id="__scheduler__")`` with ``session_id=None`` is
    the shape ``memory/types.py:295-321`` accepts: the tenant is mandatory and non-blank, every
    other set slot is a non-blank string, and ``None`` is the documented "unset" spelling.
    """
    slug = (settings.retail_customer_slug or "").strip()
    if not slug:
        return []
    return [
        Scope(tenant_id=slug, agent_id=agent_id, user_id=SYSTEM_USER_ID)
        for agent_id in agent_ids
    ]


async def abuild_consolidation_scopes(
    settings: RetailSettings, agent_ids: Sequence[str]
) -> List[Scope]:
    """Async variant. Pure today; kept so the P1 query-backed version has a home that awaits."""
    return await asyncio.to_thread(build_consolidation_scopes, settings, agent_ids)


def consolidation_scope_source(
    settings: RetailSettings, agent_ids: Sequence[str]
) -> Callable[[], Sequence[Scope]]:
    """The callable ``ScopeSource`` the worker re-consults every tick."""

    def scopes() -> Sequence[Scope]:
        return build_consolidation_scopes(settings, agent_ids)

    return scopes
