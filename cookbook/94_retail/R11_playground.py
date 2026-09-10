"""R11 -- the playground, four modes. Storage plus the deterministic mode router.

The delivery plan's own verification scenario: ask the same question in all four modes,
ask the use-case mode about a specific past run, and ask the business-area mode something
outside its area.

No model call is made here (no Azure credential is assumed) -- what this cookbook proves is
the *deterministic* half: which tools each mode's agent would even be allowed to call, built
by ``playground.modes.resolve_mode_scope`` and wired through the ``AgentFactory`` in
``app.components.build_playground_agent_factory``. That half is where the guarantee lives
("declined or scoped, not answered from everything" cannot depend on the model choosing to
behave); the model's prose on top of it is the interface's job, not this package's.

Five sections:

1. Storage: saved prompts, chat filters, preferences, reports -- CRUD against the real
   control-plane database, through ``PlaygroundStore``.
2. `data` mode: resolves to the gold-read tool. Verified via ``read_gold``'s own refusal
   when no warehouse is configured, which is the honest thing to show without one.
3. `knowledge` mode: resolves to K10's unified retrieval tool, called live against Neo4j.
4. `use_case` mode: two runs are written for two different use cases (framework provenance,
   real Postgres). The mode scoped to one use case finds its own run by reference and
   declines the other's, without ever seeing it.
5. `business_area` mode: scoped to `inventory`, from the shipped ontology (real Neo4j
   graph *not* required for this one -- the ontology is a local artifact). A concept inside
   the area resolves; a concept from `customer` is declined.

Run:
    .venvs/demo/bin/python cookbook/94_retail/R11_playground.py

Needs: the local Postgres (``cookbook/scripts/run_pgvector.sh``) and Neo4j
(``EGPT_RETAIL_NEO4J_URI``/``_PASSWORD``, pointed at whichever container is running) for
sections 1, 3 and 4. Section 5 needs neither. Section 2 needs neither either -- it is
demonstrating the refusal, not a real read.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt.answer_provenance import ProvenanceRecord, awrite_provenance  # noqa: E402
from egpt.factory import RequestContext  # noqa: E402
from egpt_retail.app.components import (  # noqa: E402
    PlaygroundModeInput,
    build_playground_agent_factory,
)
from egpt_retail.app.db import (  # noqa: E402
    build_control_plane_db,
    build_control_plane_engine,
    psycopg_dsn,
)
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.playground.modes import PlaygroundMode  # noqa: E402
from egpt_retail.playground.store import PlaygroundStore  # noqa: E402
from egpt_retail.routing.providers import build_tier_providers  # noqa: E402
from sqlalchemy import text  # noqa: E402

print("R11 -- the playground, four modes")
print("=" * 78)

settings = RetailSettings()

# --- 1. Storage --------------------------------------------------------------------------

print()
print("1. Saved prompts, filters, preferences and reports -- real Postgres")
print("-" * 78)

connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
store = PlaygroundStore(connection)
store.ensure_schema()

owner = "cookbook_r11"
prompt = store.save_prompt(
    owner, "Which suppliers are slipping on on-time delivery?", "Supplier watch"
)
same = store.save_prompt(
    owner, "Which suppliers are slipping on on-time delivery?", "Supplier watch"
)
print(
    "   OK " if prompt.id == same.id else "   FAIL",
    "saving the same text twice is a no-op",
)

store.set_filters(store.get_filters(owner))  # exercises the upsert path with defaults
active_report_id, reports = store.list_reports(owner)
print(
    "   OK " if reports else "   FAIL",
    "a fresh owner gets one empty report, so pin has a target",
)

item = store.pin_block(
    owner,
    active_report_id,
    block={"id": "blk_1", "kind": "finding", "title": "On-time rate fell"},
)
again = store.pin_block(
    owner,
    active_report_id,
    block={"id": "blk_1", "kind": "finding", "title": "On-time rate fell"},
)
print(
    "   OK " if item.id == again.id else "   FAIL",
    "pinning a block already in the report is a no-op",
)

connection.close()

# --- 2. `data` mode: resolves to the gold-read tool -------------------------------------------

print()
print("2. `data` mode resolves a real tool, backed by R6's guard and R5's read_gold")
print("-" * 78)

db = build_control_plane_db(settings)
providers = build_tier_providers(settings)
factory = build_playground_agent_factory(settings, db, providers)

data_agent = factory.factory(
    RequestContext(input=PlaygroundModeInput(mode=PlaygroundMode.DATA))
)
tool_names = {getattr(t, "__name__", "?") for t in (data_agent.tools or [])}
print(
    "   OK " if tool_names == {"read_kpi_data"} else "   FAIL",
    "data mode:",
    sorted(tool_names),
)


async def _try_read_kpi_data() -> str:
    tool = data_agent.tools[0]
    try:
        await tool(["no_such_kpi__nope"])
        return "read (unexpected in this environment -- a warehouse is configured)"
    except RuntimeError as exc:
        return f"refused, honestly: {str(exc).splitlines()[0][:88]}"


print("   ", asyncio.run(_try_read_kpi_data()))

# --- 3. `knowledge` mode: K10's unified retrieval, live ---------------------------------------

print()
print("3. `knowledge` mode resolves K10's unified retrieval, called live")
print("-" * 78)

knowledge_agent = factory.factory(
    RequestContext(input=PlaygroundModeInput(mode=PlaygroundMode.KNOWLEDGE))
)
knowledge_tools = {
    getattr(t, "__name__", "?"): t for t in (knowledge_agent.tools or [])
}
print(
    "   OK " if "search_knowledge" in knowledge_tools else "   FAIL",
    "knowledge mode:",
    sorted(knowledge_tools),
)
try:
    result = asyncio.run(knowledge_tools["search_knowledge"]("inventory replenishment"))
    print(
        "    live K10 call returned",
        len(result["results"]),
        "result(s) -- no exception",
    )
except (
    Exception
) as exc:  # pragma: no cover -- environment-dependent (Neo4j reachability)
    print("    skipped (Neo4j unreachable in this environment):", str(exc)[:88])

# --- 4. `use_case` mode: found by reference, not search ---------------------------------------

print()
print(
    "4. `use_case` mode: a run is found by reference; another use case's run is declined"
)
print("-" * 78)

engine = build_control_plane_engine(settings)
# A clean slate before the baseline: write_provenance takes no ON CONFLICT (a second write
# for a run_id that already has one is a defect, not something to overwrite silently), so a
# second run of this cookbook against a database still holding the first run's rows would
# otherwise fail on a duplicate key rather than re-demonstrate anything.
with engine.begin() as connection:
    connection.execute(
        text("DELETE FROM egpt_answer_provenance WHERE run_id IN (:a, :b)"),
        {"a": "r11_cookbook_run_a", "b": "r11_cookbook_run_b"},
    )
now = datetime.now(timezone.utc)
asyncio.run(
    awrite_provenance(
        engine,
        ProvenanceRecord(
            run_id="r11_cookbook_run_a",
            use_case_id="r11_cookbook_uc_a",
            produced_at=now,
            semantic_snapshot="cookbook.1",
            model_tier="none",
        ),
    )
)
asyncio.run(
    awrite_provenance(
        engine,
        ProvenanceRecord(
            run_id="r11_cookbook_run_b",
            use_case_id="r11_cookbook_uc_b",
            produced_at=now,
            semantic_snapshot="cookbook.1",
            model_tier="none",
        ),
    )
)
engine.dispose()

use_case_agent = factory.factory(
    RequestContext(
        input=PlaygroundModeInput(
            mode=PlaygroundMode.USE_CASE, scope_id="r11_cookbook_uc_a"
        )
    )
)
uc_tools = {getattr(t, "__name__", "?"): t for t in use_case_agent.tools}


async def _use_case_checks() -> None:
    own_run = await uc_tools["get_run"]("r11_cookbook_run_a")
    other_run = await uc_tools["get_run"]("r11_cookbook_run_b")
    listed = await uc_tools["list_runs"]()
    print(
        "   OK " if own_run.get("run_id") == "r11_cookbook_run_a" else "   FAIL",
        "its own run is found by reference",
    )
    print(
        "   OK " if "declined" in other_run else "   FAIL",
        "another use case's run is declined:",
        other_run.get("declined"),
    )
    print(
        "   OK " if len(listed["runs"]) == 1 else "   FAIL",
        "list_runs sees only this use case's run(s)",
    )


asyncio.run(_use_case_checks())

# --- 5. `business_area` mode: structural refusal, no model involved ---------------------------

print()
print("5. `business_area` mode: an out-of-area concept is declined, in the tool itself")
print("-" * 78)

area_agent = factory.factory(
    RequestContext(
        input=PlaygroundModeInput(
            mode=PlaygroundMode.BUSINESS_AREA, scope_id="inventory"
        )
    )
)
lookup = area_agent.tools[0]
in_area = lookup("Inventory")
out_of_area = lookup("Customer")
print(
    "   OK " if "description" in in_area else "   FAIL",
    "in-area concept resolves:",
    in_area.get("name"),
)
print(
    "   OK " if "declined" in out_of_area else "   FAIL",
    "out-of-area concept declined:",
    out_of_area.get("declined"),
)

unknown_area = factory.factory(
    RequestContext(
        input=PlaygroundModeInput(
            mode=PlaygroundMode.BUSINESS_AREA, scope_id="not_a_real_area"
        )
    )
)
print(
    "   OK " if not unknown_area.tools else "   FAIL",
    "an unknown area refuses before any agent tooling exists",
)

print()
print("=" * 78)
print("R11 cookbook complete.")
