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
    SSL_MODES,
    AuthMethod,
    DataSourceStatus,
    DataSourceType,
    HarvestState,
    PermissionLevel,
)
from egpt_retail.entrypoints import __main__ as cli
from egpt_retail.entrypoints import harvest as harvest_cli
from egpt_retail.executors import data_source_harvest as executor_module
from egpt_retail.executors import kpi_suggestions as kpi_suggestions_module

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_MODEL_STATES = (
    REPO_ROOT / "frontend/src/components/configure/source-model-state.js"
)
FRONTEND_SOURCES_API = REPO_ROOT / "frontend/src/services/sources-api.js"
FRONTEND_CATALOGS = REPO_ROOT / "frontend/src/domain/configure-catalogs.js"
CONFIGURE_DIR = REPO_ROOT / "frontend/src/components/configure"


def _settings() -> RetailSettings:
    # An unreachable URL: build_workflows constructs the db object without connecting.
    return RetailSettings(
        retail_db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )


# --- the workflow the Connect button starts is the executor the CLI runs -------------------


class TestOneExecutorThreeEntryPoints:
    def test_the_harvest_source_workflow_is_the_harvest_then_the_kpi_suggestions(
        self,
    ) -> None:
        settings = _settings()
        workflows = {
            w.id: w for w in build_workflows(settings, build_control_plane_db(settings))
        }
        workflow = workflows[HARVEST_SOURCE_WORKFLOW_ID]
        assert [step.name for step in workflow.steps] == [
            "harvest-source",
            "suggest-kpis",
        ]
        assert workflow.steps[0].executor is executor_module.harvest_source
        # After the harvest, never before it: a harvest that fails or is refused stops the
        # workflow, so a source with no tables never gets a suggestion run.
        assert workflow.steps[1].executor is kpi_suggestions_module.suggest_kpis
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
        # Through the real harvest parser, so this pins what the operator's console script
        # does, not the shape of the argv the dispatcher happens to build.
        seen = []

        def fake_harvest_registered_source(source_id):  # type: ignore[no-untyped-def]
            seen.append(source_id)
            return 0

        monkeypatch.setattr(
            harvest_cli, "_harvest_registered_source", fake_harvest_registered_source
        )
        assert cli.main(["harvest", "--source-id", "ds_x"]) == 0
        assert seen == ["ds_x"]

    @pytest.mark.parametrize(
        "extra", [["--suggest"], ["--version", "v1"], ["--schema", "gold"]]
    )
    def test_egpt_retail_harvest_source_id_refuses_the_warehouse_flags(
        self, extra: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``egpt-retail`` is ``cli.main``, not ``harvest_cli.main``: the usage error has to
        # survive the dispatcher, which once forwarded only ``--source-id`` and so ran the
        # harvest with the other flags silently dropped.
        def must_not_run(source_id):  # type: ignore[no-untyped-def]
            raise AssertionError(f"harvested {source_id} despite the conflicting flags")

        monkeypatch.setattr(harvest_cli, "_harvest_registered_source", must_not_run)
        with pytest.raises(SystemExit) as exc:
            cli.main(["harvest", "--source-id", "ds_x", *extra])
        assert exc.value.code == 2

    def test_egpt_retail_harvest_without_source_id_forwards_the_warehouse_flags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {}

        class _Report:
            def lines(self):  # type: ignore[no-untyped-def]
                return []

        def fake_refresh(*, schema, version):  # type: ignore[no-untyped-def]
            calls["args"] = (schema, version)
            return _Report()

        monkeypatch.setattr(harvest_cli, "refresh_semantic_layer", fake_refresh)
        assert cli.main(["harvest", "--schema", "gold", "--version", "v9"]) == 0
        assert calls["args"] == ("gold", "v9")


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

    @pytest.mark.parametrize(
        ("column", "enum_cls"),
        [
            ("source_type", DataSourceType),
            ("permission_level", PermissionLevel),
            ("auth_method", AuthMethod),
            ("status", DataSourceStatus),
            ("harvest_state", HarvestState),
        ],
    )
    def test_the_table_checks_exactly_the_enum(
        self, column: str, enum_cls: type
    ) -> None:
        # The store's CHECK constraints are the last line. Every one is generated from its
        # enum, and appears twice -- inline in CREATE TABLE for a fresh database, and in the
        # guarded refresh statement that brings an already-provisioned one up to date -- so
        # every occurrence is held to the enum. Equality, not membership: a member the table
        # refuses would fail at the first UPDATE, deep inside a workflow run, and a value the
        # table accepts but the enum lacks is a state the code cannot name.
        ddl = "\n".join(store_module._ddl("configure"))
        clauses = re.findall(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\s*\)", ddl)
        assert clauses, f"no CHECK on {column}"
        for clause in clauses:
            assert set(re.findall(r"'([^']+)'", clause)) == _wire_values(enum_cls)

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

    def test_frontend_ssl_modes_are_the_ones_the_store_accepts(self) -> None:
        """The form sends ``ssl_mode`` verbatim and the store refuses anything outside
        libpq's set -- the form once offered ``verify_ca``/``verify_full``, which the API
        would 422 (and libpq would reject at connect)."""
        if not FRONTEND_CATALOGS.exists():
            pytest.skip("frontend not checked out alongside product")
        js = FRONTEND_CATALOGS.read_text()
        match = re.search(r"SSL_MODES\s*=\s*\[(.*?)\n\];", js, flags=re.DOTALL)
        assert match is not None
        offered = set(re.findall(r'value:\s*"([^"]+)"', match.group(1)))
        assert offered == set(SSL_MODES)

    def test_frontend_registrable_types_match_the_wire_enum(self) -> None:
        if not FRONTEND_SOURCES_API.exists():
            pytest.skip("frontend not checked out alongside product")
        js = FRONTEND_SOURCES_API.read_text()
        match = re.search(r"API_SOURCE_TYPES\s*=\s*\[([^\]]*)\]", js)
        assert match is not None
        names = set(re.findall(r'"([^"]+)"', match.group(1)))
        assert names == _wire_values(wire.SourceType)


class TestNeedsReviewHasAWayOut:
    """A source stopped in ``needs_review`` (its schema is held by another source) used to have no
    way out in the UI: the card showed only "Review N items", the form re-ran the harvest only from
    ``not_started``/``error`` (so the message's own remedy, "point it at a distinct schema", did
    nothing), and the message said "then reconnect" -- a control that does not exist. The frontend
    has no test runner; like the other tests here these read the JS source, so a change that
    reopens the dead end fails in the ordinary unit run."""

    @staticmethod
    def _read(name: str) -> str:
        path = CONFIGURE_DIR / name
        if not path.exists():
            pytest.skip("frontend not checked out alongside product")
        return path.read_text()

    def test_saving_the_form_re_runs_the_harvest_from_needs_review(self) -> None:
        js = self._read("ConfigureInternalSourceForm.jsx")
        match = re.search(r"const wantsHarvest =(.*?);\n", js, flags=re.DOTALL)
        assert match is not None, "wantsHarvest is gone"
        states = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        assert {"not_started", "error", "needs_review"} <= states
        # a modelled source keeps its model on a rename: it must not be in the list
        assert "modelled" not in states

    def test_the_card_offers_retry_modelling_for_needs_review(self) -> None:
        js = self._read("ConfigureInternalSources.jsx")
        match = re.search(r"\{canHarvest && (\[.*?\])\.includes\(model\.state\)", js)
        assert match is not None, "the card's harvest button condition changed shape"
        assert {"error", "not_started", "needs_review"} == set(
            re.findall(r'"([a-z_]+)"', match.group(1))
        )
        assert '"Retry modelling"' in js

    def test_the_semantic_layer_screen_says_retry_not_build_for_needs_review(
        self,
    ) -> None:
        js = self._read("ConfigureSemanticLayer.jsx")
        assert re.search(
            r'model\.state === "needs_review"\s*\?\s*"Retry modelling"', js
        ) or re.search(
            r'model\.state === "error" \|\| model\.state === "needs_review"', js
        )

    def test_the_reason_points_at_controls_that_exist_and_never_says_reconnect(
        self,
    ) -> None:
        js = self._read("source-model-state.js")
        match = re.search(
            r"NEEDS_REVIEW_REASON =\s*\n?\s*\"(.*?)\";", js, flags=re.DOTALL
        )
        assert match is not None
        reason = match.group(1)
        assert "reconnect" not in reason.lower()
        # accurate for the real cases -- and it does not call an absent owner "another connected source"
        assert "another connected source or by the deployment's own warehouse" in reason
        assert "Edit the source" in reason and "retry modelling" in reason
        assert "since been removed" in reason  # the case where the owner was deleted

    def test_removing_a_modelled_source_says_what_goes_with_it(self) -> None:
        js = self._read("ConfigureInternalSources.jsx")
        assert "Its tables are removed from the semantic layer" in js
