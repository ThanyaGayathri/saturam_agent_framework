"""#307 -- ``GET /semantic/tables?source_id=`` and ``build_source_read_guard``, DB-free.

The graph-side property they depend on -- that a source's tables and the operator's never
appear in each other's reads, including when they share a bare table name -- is proved
against a real Neo4j by ``test_semantic_live.py::TestSourceScoping``. This file covers the
router's own branching (default path untouched, 404 for an unknown source, the source's own
snapshot) and the guard's allow-list, with the reader faked.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from egpt.exceptions import SQLGuardRefusal
from egpt_retail.api.routers import semantic as semantic_module
from egpt_retail.app.sql_guard import build_source_read_guard
from egpt_retail.contracts.semantic import Snapshot, TableRef
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeReader:
    """A reader that lists whatever it was scoped to. ``scope`` records the ``source_id`` it
    was opened with, so a test can assert the router asked for the right one."""

    def __init__(self, tables: Dict[str, List[TableRef]], scope: str) -> None:
        self._tables = tables
        self.scope = scope

    def list_tables(self, *, include_proposed: bool = False):
        return tuple(self._tables.get(self.scope, ()))

    def snapshot(self) -> Snapshot:
        return Snapshot(
            version="op.1", harvested_at="2026-09-01T00:00:00Z", source="warehouse"
        )

    def source_snapshot_version(self) -> Optional[str]:
        return "src.7" if self._tables.get(self.scope) else None


_TABLES = {
    "": [TableRef(name="orders", schema="silver", row_count=10)],
    "ds_1": [TableRef(name="orders", schema="public", row_count=3)],
}


def _client(monkeypatch, *, registered: Dict[str, Any]) -> tuple:
    opened: List[str] = []

    @contextmanager
    def _open(settings, driver=None, *, source_id=""):
        opened.append(source_id)
        yield _FakeReader(_TABLES, source_id)

    class _Store:
        def __init__(self, connection):
            pass

        def get(self, source_id):
            return registered.get(source_id)

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(semantic_module, "open_semantic_reader", _open)
    monkeypatch.setattr(semantic_module, "DataSourceStore", _Store)
    monkeypatch.setattr(
        semantic_module, "pooled_control_plane_connection", lambda s: _Conn()
    )

    app = FastAPI()
    app.include_router(semantic_module.router)

    @app.middleware("http")
    async def _auth(request, call_next):
        request.state.authorization_enabled = True
        request.state.scopes = ["semantic:read"]
        request.state.admin_scope = None
        return await call_next(request)

    return TestClient(app), opened


def _source(**overrides: Any) -> Any:
    fields = dict(
        name="Customer warehouse",
        last_harvest_at=datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestListSemanticTablesSourceId:
    def test_without_source_id_it_is_the_operators_listing_and_snapshot(
        self, monkeypatch
    ):
        client, opened = _client(monkeypatch, registered={"ds_1": _source()})
        body = client.get("/semantic/tables").json()
        assert opened == [""]  # the operator scope, exactly as before #307
        assert [t["schema"] for t in body["tables"]] == ["silver"]
        assert body["snapshot"]["version"] == "op.1"

    def test_with_source_id_it_is_that_sources_listing_and_snapshot(self, monkeypatch):
        client, opened = _client(monkeypatch, registered={"ds_1": _source()})
        body = client.get("/semantic/tables", params={"source_id": "ds_1"}).json()
        assert opened == ["ds_1"]
        assert [t["schema"] for t in body["tables"]] == ["public"]
        assert body["snapshot"] == {
            "version": "src.7",
            "harvested_at": "2026-09-20T08:00:00+00:00",
            "source": "Customer warehouse",
        }

    def test_an_unknown_source_is_404_not_an_empty_list(self, monkeypatch):
        client, opened = _client(monkeypatch, registered={})
        response = client.get("/semantic/tables", params={"source_id": "ds_missing"})
        assert response.status_code == 404
        assert "ds_missing" in response.json()["detail"]
        assert opened == []  # never reached the graph

    def test_a_registered_but_unharvested_source_is_an_empty_answer(self, monkeypatch):
        client, _ = _client(
            monkeypatch,
            registered={"ds_new": _source(last_harvest_at=None)},
        )
        body = client.get("/semantic/tables", params={"source_id": "ds_new"}).json()
        assert body["tables"] == []
        assert body["snapshot"]["version"] == "unharvested"
        assert body["snapshot"]["harvested_at"] == "never"

    def test_a_harvested_source_whose_tables_are_gone_does_not_contradict_itself(
        self, monkeypatch
    ):
        """``version`` is read off the tables held now and ``harvested_at`` off the
        registration, so a harvested source that was since pruned to nothing has a real
        timestamp and no version. It must not say "unharvested" beside a real time."""
        client, _ = _client(monkeypatch, registered={"ds_gone": _source()})
        body = client.get("/semantic/tables", params={"source_id": "ds_gone"}).json()
        assert body["tables"] == []
        assert body["snapshot"]["version"] == "empty"
        assert body["snapshot"]["harvested_at"] == "2026-09-20T08:00:00+00:00"

    def test_an_empty_source_id_is_an_unknown_source_not_the_operators_listing(
        self, monkeypatch
    ):
        """An omitted parameter is the operator's listing; ``source_id=`` is not the same
        thing. It names no registered source, so it is a 404 -- not the operator's tables
        by way of the operator's own source id also being the empty string."""
        client, opened = _client(monkeypatch, registered={"ds_1": _source()})
        response = client.get("/semantic/tables", params={"source_id": ""})
        assert response.status_code == 404
        assert opened == []  # never reached the graph, and not the operator scope


class TestBuildSourceReadGuard:
    @staticmethod
    def _guard(schema: str = "public"):
        return build_source_read_guard(_FakeReader(_TABLES, "ds_1"), schema=schema)

    def test_a_statement_over_the_sources_tables_passes(self):
        self._guard().check("SELECT id FROM public.orders")

    def test_a_table_the_source_does_not_hold_is_refused(self):
        with pytest.raises(SQLGuardRefusal):
            self._guard().check("SELECT * FROM public.secrets")

    def test_another_schema_is_refused_even_for_an_allowed_bare_name(self):
        """The allow-list is bare-name-only -- ``orders`` is allowed -- so it is the named
        schema that stops ``silver.orders`` (the operator's) running on a customer's
        connection."""
        with pytest.raises(SQLGuardRefusal):
            self._guard().check("SELECT * FROM silver.orders")

    def test_a_write_is_refused(self):
        with pytest.raises(SQLGuardRefusal):
            self._guard().check("DELETE FROM public.orders")
