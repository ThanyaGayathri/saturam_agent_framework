"""K1 -- memory scope and wiring, end to end (G1, G2).

The package's three scenarios, from the delivery plan:

1. Tell an agent something in one conversation, start a new one, ask it back.
2. Remove the customer identifier from configuration and restart.
3. Trigger a scheduled run and inspect what scope it wrote under.

Scenario 2 is the one that matters. The failure G1 describes is not an error -- it is an
agent that answers perfectly and remembers nothing, with no log line saying so. The evidence
is that startup *refuses* rather than continuing, so the fault is found in a minute instead
of a month.

Needs Postgres and a model:

    ./cookbook/scripts/run_pgvector.sh
    set -a && source azure_key.env && set +a

Run:
    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K1_memory_wiring.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.memory.startup import MemoryNotWired, assert_memory_wired  # noqa: E402
from egpt.memory.types import Scope  # noqa: E402
from egpt.os.middleware.fixed_tenant import FixedTenantMiddleware  # noqa: E402
from egpt_retail.app.components import build_memory_provider  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.memory.scopes import (  # noqa: E402
    SYSTEM_USER_ID,
    build_consolidation_scopes,
)

SLUG = os.environ.get("EGPT_RETAIL_CUSTOMER_SLUG", "middleby")
os.environ.setdefault("EGPT_RETAIL_PROVIDER_MODE", "local")
# Its own memory table, not the deployment's. The table is created at the embedder's
# dimension and the framework refuses to reuse one of a different width, so a cookbook
# running Mode A (768) would leave the deployment's Mode B (1024) table unusable.
os.environ.setdefault("EGPT_RETAIL_MEMORY_TABLE", "memory_vectors_k1_cookbook")
# AZ-B flipped the default memory backend to Azure AI Search; this cookbook runs
# against the compose Postgres, so it says so explicitly.
os.environ.setdefault("EGPT_RETAIL_MEMORY_BACKEND", "pgvector")
MEMORY_TABLE = os.environ["EGPT_RETAIL_MEMORY_TABLE"]


def _store_and_exit() -> int:
    """Process A. Writes two facts, flushes the provider, exits. Never reads."""
    from egpt.agent import Agent
    from egpt.integrations.models.azure import AzureOpenAI
    from egpt.run.base import RunContext

    settings = RetailSettings(retail_customer_slug=SLUG)
    provider = build_memory_provider(settings)
    if provider is None:
        return 2
    agent = Agent(
        id="k1_recall_probe",
        name="k1_recall_probe",
        model=AzureOpenAI(id=os.environ["AZURE_OPENAI_DEPLOYMENT"]),
        memory_provider=provider,
        instructions="Acknowledge in one short sentence.",
    )
    agent.run(
        "Remember this: our warehouse in Rotterdam is the one that handles all EU returns.",
        run_context=RunContext(
            run_id="k1-run-1", tenant_id=SLUG, user_id="alice", session_id="k1-conv-1"
        ),
        user_id="alice",
        session_id="k1-conv-1",
    )
    # A scheduled run writes as the scheduler identity, with no person (MEMORY_SCOPE.md).
    agent.run(
        "Remember this: the nightly refresh on 2026-09-08 loaded 310 quote requests.",
        run_context=RunContext(
            run_id="k1-run-nightly",
            tenant_id=SLUG,
            user_id=SYSTEM_USER_ID,
            session_id="nightly-1",
        ),
        user_id=SYSTEM_USER_ID,
        session_id="nightly-1",
    )
    provider.close()  # flush the background writeback before the process ends
    return 0


if "--store" in sys.argv:
    raise SystemExit(_store_and_exit())

print("K1 -- memory scope and wiring")
print("=" * 78)

# --- 1. The provider is built, and it is the per-tenant entry point ----------------------

print()
print("1. The per-tenant memory builder is no longer orphaned")
print("-" * 78)
settings = RetailSettings(retail_customer_slug=SLUG)
provider = build_memory_provider(settings)
if provider is not None:
    provider.close()  # section 1 only needed to prove it builds; 2b builds its own
print(f"  slug {SLUG!r} -> provider: {type(provider).__name__ if provider else None}")
print(
    f"  provider mode (shared with the knowledge layers): {settings.retail_provider_mode.value}"
)
wired = provider is not None

# --- 2. Startup refuses when the identifier is missing -----------------------------------

print()
print("2. Remove the identifier from configuration")
print("-" * 78)


class _Agent:
    """Stands in for a registered agent: the checks read two attributes."""

    def __init__(self, id=None, name=None, memory_provider=None):
        self.id = id
        self.name = name
        self.memory_provider = memory_provider


unset = RetailSettings(retail_customer_slug=None)
print(f"  slug unset -> provider: {build_memory_provider(unset)}")

refused = False
try:
    assert_memory_wired(
        [_Agent(id="synonym_proposer", memory_provider=build_memory_provider(unset))],
        tenant_id=unset.retail_customer_slug,
    )
except MemoryNotWired as exc:
    refused = True
    print("  startup REFUSED:")
    for line in str(exc).splitlines()[1:3]:
        print("   ", line.strip())

# Every fault at once, not one restart per fault.
try:
    assert_memory_wired(
        [_Agent(memory_provider=object()), _Agent(id="b")], tenant_id=""
    )
except MemoryNotWired as exc:
    faults = [ln for ln in str(exc).splitlines() if ln.strip().startswith("- ")]
    print(f"  a deployment with three faults reports all {len(faults)} at once")

# And it passes when the deployment is correct.
passes = True
try:
    assert_memory_wired(
        [_Agent(id="synonym_proposer", memory_provider=provider)], tenant_id=SLUG
    )
except MemoryNotWired as exc:
    passes = False
    print("  UNEXPECTED refusal on a correct deployment:", exc)
print(f"  a correctly configured deployment starts: {passes}")

# --- 2b. Scenario 1, done properly: store in ONE process, recall in ANOTHER ------------
#
# The first version of this ran both conversations in one process on one Agent object, with
# ``enable_user_memories=True`` -- a soon-deprecated in-process path. It "recalled" Rotterdam
# and proved nothing: the memory store could have been empty and the answer identical. The
# only recall that demonstrates the provider is one across a process boundary, with the
# writer gone and the store queried directly for what it holds.

print()
print("2b. Recall across a PROCESS boundary, and the scope the store actually holds")
print("-" * 78)
recalled = False
alice_rows = scheduler_rows = 0
if provider is None or not os.environ.get("AZURE_OPENAI_API_KEY"):
    print("  SKIPPED: needs a memory provider and AZURE_OPENAI_API_KEY")
else:
    import subprocess

    import psycopg

    # Process A: store, flush, exit. Two conversations -- Alice's, and one written the way a
    # scheduled run writes, as the framework's scheduler identity (MEMORY_SCOPE.md).
    stored = subprocess.run(
        [sys.executable, __file__, "--store"],
        capture_output=True,
        text=True,
        env={**os.environ, "EGPT_RETAIL_PROVIDER_MODE": "local"},
    )
    if stored.returncode != 0:
        print("  storing process FAILED:", stored.stderr[-400:])
    else:
        print("  process A stored two facts and exited; this process never saw them")

    # Process B (this one): a fresh provider and agent, no shared object with A.
    from egpt.agent import Agent
    from egpt.integrations.models.azure import AzureOpenAI
    from egpt.run.base import RunContext

    fresh = build_memory_provider(settings)
    agent = Agent(
        id="k1_recall_probe",
        name="k1_recall_probe",
        model=AzureOpenAI(id=os.environ["AZURE_OPENAI_DEPLOYMENT"]),
        memory_provider=fresh,
        instructions="Answer in one short sentence, using what you remember about this user.",
    )
    answer = agent.run(
        "Which warehouse handles our EU returns?",
        run_context=RunContext(
            run_id="k1-run-2", tenant_id=SLUG, user_id="alice", session_id="k1-conv-2"
        ),
        user_id="alice",
        session_id="k1-conv-2",
    )
    text = str(answer.content)
    recalled = "rotterdam" in text.lower()
    print(f"  process B asked cold, answered: {text[:90]!r}")
    print(f"  recalled across the process boundary: {recalled}")
    fresh.close()

    # What the store holds, by scope. This is scenario 3's evidence: a scheduled run's rows
    # carry the scheduler identity as user_id, exactly as the convention says.
    raw = settings.retail_db_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(raw) as conn:
        rows = conn.execute(
            "select coalesce(agent_id,'*'), coalesce(user_id,'*'), count(*) "
            f"from ai.{MEMORY_TABLE} where tenant_id=%s group by 1,2 order by 2",
            (SLUG,),
        ).fetchall()
    print(f"  scopes in {MEMORY_TABLE} for this tenant:")
    for agent_id, user_id, n in rows:
        print(f"    {SLUG}/{agent_id}/{user_id}  x{n}")
        if user_id == "alice":
            alice_rows += n
        if user_id == SYSTEM_USER_ID:
            scheduler_rows += n

# --- 3. The middleware stamps the tenant, and never overrides a real one -----------------

print()
print("3. The tenant reaches the run context")
print("-" * 78)


class _State:
    pass


class _Request:
    def __init__(self, **kw):
        self.state = _State()
        for k, v in kw.items():
            setattr(self.state, k, v)


async def _through(request):
    """Run the middleware's dispatch over one request."""
    middleware = FixedTenantMiddleware(app=None, tenant_id=SLUG)

    async def _next(_):
        return "ok"

    await middleware.dispatch(request, _next)
    return request


import asyncio  # noqa: E402

plain = asyncio.run(_through(_Request()))
print(
    f"  unauthenticated request -> tenant_id={plain.state.tenant_id!r}, claims={plain.state.claims}"
)

claimed = asyncio.run(
    _through(_Request(tenant_id="acme-from-idp", claims={"tenant_id": "acme-from-idp"}))
)
print(
    f"  request with a verified claim -> tenant_id={claimed.state.tenant_id!r} (untouched)"
)
never_overrides = claimed.state.tenant_id == "acme-from-idp"

# The claim is what the run path actually reads.
stamps_claim = plain.state.claims.get("tenant_id") == SLUG

# --- 4. What a scheduled run writes under ------------------------------------------------

print()
print("4. Scheduled runs, per MEMORY_SCOPE.md")
print("-" * 78)
scopes = build_consolidation_scopes(settings, ["synonym_proposer"])
for scope in scopes:
    print(f"  {scope.key()}")
interactive = Scope(
    tenant_id=SLUG, agent_id="synonym_proposer", user_id="alice", session_id="s1"
)
print(f"  interactive, for contrast: {interactive.key()}")
system_writes_as_scheduler = bool(scopes) and scopes[0].user_id == SYSTEM_USER_ID
print(f"  scheduled runs write as {SYSTEM_USER_ID!r}: {system_writes_as_scheduler}")
print("  (the isolation matrix this rests on is K01_memory_scope_convention.py)")

# --- 5. Verdict --------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if not wired:
    failures.append("no memory provider was built for a configured slug")
if not refused:
    failures.append("startup did not refuse with the customer identifier removed")
if not passes:
    failures.append("startup refused a correctly configured deployment")
if not stamps_claim:
    failures.append("the middleware did not stamp the claim the run path reads")
if not never_overrides:
    failures.append(
        "the middleware overrode a verified tenant claim, which is a cross-tenant risk"
    )
if not system_writes_as_scheduler:
    failures.append("scheduled runs do not write under the scheduler identity")
if provider is not None and os.environ.get("AZURE_OPENAI_API_KEY"):
    if not recalled:
        failures.append(
            "a fact stored by another process was not recalled -- the wiring is present but "
            "memory is not actually working"
        )
    if alice_rows == 0:
        failures.append(
            "the store holds no rows under alice's scope, so any recall was in-process"
        )
    if scheduler_rows == 0:
        failures.append(
            f"the store holds no rows under {SYSTEM_USER_ID!r}, so the scheduled-run scope "
            "was never exercised"
        )

if failures:
    print("K1 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Memory is wired. A configured deployment starts and remembers; an unconfigured one"
)
print("refuses to start rather than remembering nothing quietly.")
