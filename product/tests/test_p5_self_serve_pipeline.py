"""#309 -- the self-serve data-source pipeline as one thing, DB-free.

Phases 0-4 each proved their own piece (``test_p0_data_sources.py`` .. ``test_p3_source_scoping.py``,
the P4 browser check). What none of them pins is the *seams between* the pieces: that the
workflow the Connect button starts is the executor the CLI runs, that the four vocabularies
involved -- the contract enums, the wire enums, the store's CHECK constraints and the frontend's
``MODEL_STATES`` -- are one vocabulary, and that ``egpt-retail harvest --source-id`` dispatches
to the executor and turns its ``StepOutput`` into an exit status. A drift in any of these would
pass every per-phase suite and break the product end to end.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from egpt.workflow.types import StepOutput

from egpt_retail.api import models as wire
from egpt_retail.app.components import HARVEST_SOURCE_WORKFLOW_ID, build_workflows
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings
from egpt_retail.configure import store as store_module
from egpt_retail.contracts.data_source import (
    HARVESTABLE_SOURCE_TYPES,
    AuthMethod,
    DataSourceStatus,
    DataSourceType,
    HarvestState,
    PermissionLevel,
)
from egpt_retail.entrypoints import __main__ as cli
from egpt_retail.entrypoints import harvest as harvest_cli
from egpt_retail.executors import data_source_harvest as executor_module

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_MODEL_STATES = (
    REPO_ROOT / "frontend/src/components/configure/source-model-state.js"
)
FRONTEND_SOURCES_API = REPO_ROOT / "frontend/src/services/sources-api.js"


def _settings() -> RetailSettings:
    # An unreachable URL: build_workflows constructs the db object without connecting.
    return RetailSettings(
        retail_db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )


# --- the workflow the Connect button starts is the executor the CLI runs -------------------


class TestOneExecutorThreeEntryPoints:
    def test_the_harvest_source_workflow_is_registered_with_the_executor_as_its_only_step(
        self,
    ) -> None:
        settings = _settings()
        workflows = {
            w.id: w for w in build_workflows(settings, build_control_plane_db(settings))
        }
        workflow = workflows[HARVEST_SOURCE_WORKFLOW_ID]
        assert [step.name for step in workflow.steps] == ["harvest-source"]
        assert workflow.steps[0].executor is executor_module.harvest_source
        # Background runs (what the frontend asks for with background=true) require a db on
        # the workflow; without it the framework answers 400 and the card never leaves
        # "not modelled".
        assert workflow.db is not None

    def test_the_workflow_id_is_the_one_the_frontend_posts_to(self) -> None:
        if not FRONTEND_SOURCES_API.exists():
            pytest.skip("frontend not checked out alongside product")
        js = FRONTEND_SOURCES_API.read_text()
        match = re.search(r'HARVEST_SOURCE_WORKFLOW_ID\s*=\s*"([^"]+)"', js)
        assert match is not None, "sources-api.js no longer names the workflow id"
        assert match.group(1) == HARVEST_SOURCE_WORKFLOW_ID

    def test_cli_source_id_runs_the_executor_and_exits_0_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = {}

        async def fake_harvest_source(step_input):  # type: ignore[no-untyped-def]
            seen["additional_data"] = step_input.additional_data
            return StepOutput(content={"source_id": "ds_x", "ok": True, "tables": 2})

        monkeypatch.setattr(executor_module, "harvest_source", fake_harvest_source)
        assert harvest_cli.main(["--source-id", "ds_x"]) == 0
        assert seen["additional_data"] == {"source_id": "ds_x"}

    def test_cli_source_id_exits_1_when_the_executor_refuses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_harvest_source(step_input):  # type: ignore[no-untyped-def]
            return StepOutput(
                success=False, stop=True, error="source ds_x: refused: loopback"
            )

        monkeypatch.setattr(executor_module, "harvest_source", fake_harvest_source)
        assert harvest_cli.main(["--source-id", "ds_x"]) == 1

    @pytest.mark.parametrize(
        "extra", [["--suggest"], ["--version", "v1"], ["--schema", "gold"]]
    )
    def test_cli_source_id_refuses_the_warehouse_flags(self, extra: list) -> None:
        # The two harvests are different things; mixing their flags is a usage error, not
        # something to silently ignore.
        with pytest.raises(SystemExit) as exc:
            harvest_cli.main(["--source-id", "ds_x", *extra])
        assert exc.value.code == 2

    def test_cli_without_source_id_is_the_warehouse_harvest_as_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {}

        class _Report:
            def lines(self):  # type: ignore[no-untyped-def]
                return ["harvested silver"]

        def fake_refresh(*, schema, version):  # type: ignore[no-untyped-def]
            calls["args"] = (schema, version)
            return _Report()

        monkeypatch.setattr(harvest_cli, "refresh_semantic_layer", fake_refresh)
        assert harvest_cli.main(["--schema", "silver"]) == 0
        assert calls["args"] == ("silver", "")

    def test_egpt_retail_harvest_dispatches_source_id_to_the_harvest_entrypoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = {}

        def fake_main(argv):  # type: ignore[no-untyped-def]
            seen["argv"] = list(argv)
            return 0

        monkeypatch.setattr(harvest_cli, "main", fake_main)
        assert cli.main(["harvest", "--source-id", "ds_x"]) == 0
        assert seen["argv"] == ["--source-id", "ds_x"]


# --- four vocabularies, one set of words --------------------------------------------------


def _wire_values(enum_cls: type) -> set:
    return {member.value for member in enum_cls}


class TestVocabulariesAgree:
    def test_source_type(self) -> None:
        assert _wire_values(wire.SourceType) == _wire_values(DataSourceType)

    def test_auth_method(self) -> None:
        assert _wire_values(wire.AuthMethod) == _wire_values(AuthMethod)

    def test_permission_level(self) -> None:
        assert _wire_values(wire.PermissionLevel) == _wire_values(PermissionLevel)

    def test_harvest_state_contract_equals_wire(self) -> None:
        assert _wire_values(wire.HarvestState) == _wire_values(HarvestState)

    def test_harvest_state_and_status_are_what_the_table_checks(self) -> None:
        # The store's CHECK constraints are the last line; a new enum member that the table
        # refuses would fail at the first UPDATE, deep inside a workflow run.
        ddl = "\n".join(store_module._ddl("configure"))
        for member in HarvestState:
            assert f"'{member.value}'" in ddl, member
        for member in DataSourceStatus:
            assert f"'{member.value}'" in ddl, member

    def test_harvestable_types_are_registrable_types(self) -> None:
        assert HARVESTABLE_SOURCE_TYPES <= set(DataSourceType)

    def test_frontend_model_states_are_the_harvest_states(self) -> None:
        if not FRONTEND_MODEL_STATES.exists():
            pytest.skip("frontend not checked out alongside product")
        js = FRONTEND_MODEL_STATES.read_text()
        start = js.index("export const MODEL_STATES = {")
        end = js.index("\n};", start)
        keys = set(re.findall(r"^\s+(\w+):\s*\{", js[start:end], flags=re.MULTILINE))
        assert keys == _wire_values(HarvestState)

    def test_frontend_harvestable_set_matches_the_backend(self) -> None:
        if not FRONTEND_SOURCES_API.exists():
            pytest.skip("frontend not checked out alongside product")
        js = FRONTEND_SOURCES_API.read_text()
        match = re.search(r"HARVESTABLE_SOURCE_TYPES\s*=\s*new Set\(\[([^\]]*)\]\)", js)
        assert match is not None
        names = set(re.findall(r'"([^"]+)"', match.group(1)))
        assert names == {t.value for t in HARVESTABLE_SOURCE_TYPES}

    def test_frontend_registrable_types_match_the_wire_enum(self) -> None:
        if not FRONTEND_SOURCES_API.exists():
            pytest.skip("frontend not checked out alongside product")
        js = FRONTEND_SOURCES_API.read_text()
        match = re.search(r"API_SOURCE_TYPES\s*=\s*\[([^\]]*)\]", js)
        assert match is not None
        names = set(re.findall(r'"([^"]+)"', match.group(1)))
        assert names == _wire_values(wire.SourceType)
