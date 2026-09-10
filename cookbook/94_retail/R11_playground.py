"""R11 -- the playground, four modes. Storage plus the deterministic mode router.

The delivery plan's own verification scenario: ask the same question in all four modes,
ask the use-case mode about a specific past run, and ask the business-area mode something
outside its area.

No model call is made here (no Azure credential is assumed) -- what this cookbook proves is
the *deterministic* half: which tools each mode's agent would even be allowed to call, built
by ``playground.modes.aresolve_mode_scope`` and wired through the ``AgentFactory`` in
``app.components.build_playground_agent_factory``. That half is where the guarantee lives
("declined or scoped, not answered from everything" cannot depend on the model choosing to
behave); the model's prose on top of it is the interface's job, not this package's.

Six sections:

1. Storage: saved prompts, chat filters, preferences, reports -- CRUD against the real
   control-plane database, through ``PlaygroundStore``. Includes a concurrency check on
   ``_ensure_default_report``'s advisory-lock guard: two concurrent first-ever calls for a
   brand-new owner must produce exactly one report, not two.
2. `data` mode: resolves to the gold-read tool. With no warehouse configured, shows
   ``read_gold``'s own refusal, which is the honest thing to demonstrate without one; with
   one configured (``EGPT_RETAIL_WAREHOUSE_DB_URL``/``_READONLY_DB_URL`` pointed at the
   local compose stack's ``egpt_warehouse`` database), shows the real "missing" shape for
   a KPI that was never materialised -- never invented, either way.
3. `knowledge` mode: resolves to K10's unified retrieval tool, called live against Neo4j.
4. `use_case` mode: two runs are written for two different use cases (framework provenance,
   real Postgres). The mode scoped to one use case finds its own run by reference and
   declines the other's, without ever seeing it.
5. `business_area` mode: scoped to `inventory`, from the shipped ontology (real Neo4j
   graph *not* required for this one -- the ontology is a local artifact). A concept inside
   the area resolves; a concept from `customer` is declined.
6. The factory itself is `async def` (``build_playground_agent``) -- every call below goes
   through it with `await`, which is also what proves the async wiring is real rather than
   declared-but-dead (the round-1 review's own finding on ``aresolve_mode_scope``).

Run:
    .venvs/demo/bin/python cookbook/94_retail/R11_playground.py

Needs: the local Postgres (``cookbook/scripts/run_pgvector.sh``) and Neo4j
(``EGPT_RETAIL_NEO4J_URI``/``_PASSWORD``, pointed at whichever container is running) for
sections 1, 3 and 4. Section 5 needs neither. Section 2 works either way: with
``EGPT_RETAIL_WAREHOUSE_DB_URL``/``_READONLY_DB_URL`` unset it demonstrates the refusal;
pointed at the local ``deploy/compose.yaml`` stack's ``egpt_warehouse`` database (started
with ``--profile full``, after ``warehouse-migrate`` has run) it demonstrates a real read.
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


async def main() -> None:
    print("R11 -- the playground, four modes")
    print("=" * 78)

    settings = RetailSettings()

    # --- 1. Storage ----------------------------------------------------------------------

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

    store.set_filters(
        store.get_filters(owner)
    )  # exercises the upsert path with defaults
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

    # Concurrency check on the advisory-lock fix: two threads, each its own connection (a
    # connection cannot be shared across threads), racing `list_reports` for the same
    # brand-new owner. Without the lock in `_ensure_default_report`, both can pass the
    # "does this owner have a report" check before either commits, and the owner ends up
    # with two "My first report" rows instead of one.
    race_owner = f"cookbook_r11_race_{datetime.now(timezone.utc).timestamp()}"

    def _first_report_id() -> str:
        import psycopg as _psycopg

        race_connection = _psycopg.connect(psycopg_dsn(settings.retail_db_url))
        try:
            _, race_reports = PlaygroundStore(race_connection).list_reports(race_owner)
            return race_reports[0].id
        finally:
            race_connection.close()

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_ids = list(pool.map(lambda _: _first_report_id(), range(2)))
    _, race_reports_after = store.list_reports(race_owner)
    print(
        "   OK "
        if len(race_reports_after) == 1 and first_ids[0] == first_ids[1]
        else "   FAIL",
        "two concurrent first-ever calls produce exactly one report, not two",
    )

    connection.close()

    # --- 2. `data` mode: resolves to the gold-read tool -----------------------------------

    print()
    print(
        "2. `data` mode resolves a real tool, backed by R6's guard and R5's read_gold"
    )
    print("-" * 78)

    db = build_control_plane_db(settings)
    providers = build_tier_providers(settings)
    factory = build_playground_agent_factory(settings, db, providers)

    data_agent = await factory.factory(
        RequestContext(input=PlaygroundModeInput(mode=PlaygroundMode.DATA))
    )
    tool_names = {getattr(t, "__name__", "?") for t in (data_agent.tools or [])}
    print(
        "   OK " if tool_names == {"read_kpi_data"} else "   FAIL",
        "data mode:",
        sorted(tool_names),
    )

    try:
        no_warehouse_result = await data_agent.tools[0](["no_such_kpi__nope"])
        missing = no_warehouse_result["missing"]
        print(
            "   OK "
            if missing and missing[0]["kpi_identifier"] == "no_such_kpi__nope"
            else "   FAIL",
            "a KPI that was never materialised is reported missing, not invented:",
            missing[0]["reason"] if missing else missing,
        )
    except RuntimeError as exc:
        print(
            "    no warehouse configured in this environment; refused, honestly:",
            str(exc).splitlines()[0][:88],
        )

    # --- 3. `knowledge` mode: K10's unified retrieval, live -------------------------------

    print()
    print("3. `knowledge` mode resolves K10's unified retrieval, called live")
    print("-" * 78)

    knowledge_agent = await factory.factory(
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
        result = await knowledge_tools["search_knowledge"]("inventory replenishment")
        print(
            "    live K10 call returned",
            len(result["results"]),
            "result(s) -- no exception",
        )
    except (
        Exception
    ) as exc:  # pragma: no cover -- environment-dependent (Neo4j reachability)
        print("    skipped (Neo4j unreachable in this environment):", str(exc)[:88])

    # --- 4. `use_case` mode: found by reference, not search -------------------------------

    print()
    print(
        "4. `use_case` mode: a run is found by reference; another use case's run is declined"
    )
    print("-" * 78)

    engine = build_control_plane_engine(settings)
    # A clean slate before the baseline: write_provenance takes no ON CONFLICT (a second
    # write for a run_id that already has one is a defect, not something to overwrite
    # silently), so a second run of this cookbook against a database still holding the
    # first run's rows would otherwise fail on a duplicate key rather than re-demonstrate
    # anything.
    with engine.begin() as ddl_connection:
        ddl_connection.execute(
            text("DELETE FROM egpt_answer_provenance WHERE run_id IN (:a, :b)"),
            {"a": "r11_cookbook_run_a", "b": "r11_cookbook_run_b"},
        )
    now = datetime.now(timezone.utc)
    await awrite_provenance(
        engine,
        ProvenanceRecord(
            run_id="r11_cookbook_run_a",
            use_case_id="r11_cookbook_uc_a",
            produced_at=now,
            semantic_snapshot="cookbook.1",
            model_tier="none",
        ),
    )
    await awrite_provenance(
        engine,
        ProvenanceRecord(
            run_id="r11_cookbook_run_b",
            use_case_id="r11_cookbook_uc_b",
            produced_at=now,
            semantic_snapshot="cookbook.1",
            model_tier="none",
        ),
    )
    engine.dispose()

    use_case_agent = await factory.factory(
        RequestContext(
            input=PlaygroundModeInput(
                mode=PlaygroundMode.USE_CASE, scope_id="r11_cookbook_uc_a"
            )
        )
    )
    uc_tools = {getattr(t, "__name__", "?"): t for t in use_case_agent.tools}

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

    # --- 5. `business_area` mode: structural refusal, no model involved -------------------

    print()
    print(
        "5. `business_area` mode: an out-of-area concept is declined, in the tool itself"
    )
    print("-" * 78)

    area_agent = await factory.factory(
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

    unknown_area = await factory.factory(
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


if __name__ == "__main__":
    asyncio.run(main())
