"""load_knowledge: a later step's failure must not erase an earlier step's report (round-2
review finding on ``entrypoints/load_knowledge.py:53``).

Dependency-free: ``_load_ontology`` and ``_load_prose`` are monkeypatched directly rather than
driven through a real Neo4j/pgvector store, matching this package's own convention (see
``test_r4_schema_map.py``'s docstring elsewhere in this suite) that anything needing real
infrastructure is a cookbook scenario, not a unit test.
"""

from __future__ import annotations

import logging

import pytest
from egpt_retail.app.settings import RetailSettings
from egpt_retail.entrypoints import load_knowledge as lk


def test_ontology_step_report_survives_a_prose_step_failure(monkeypatch, caplog):
    monkeypatch.setattr(lk, "resolve_knowledge_embedder", lambda mode: "stub-embedder")
    monkeypatch.setattr(
        lk,
        "_load_ontology",
        lambda *args, **kwargs: ["ontology: 24 concepts at version 1"],
    )

    def _boom(*args, **kwargs):
        raise SystemExit("prose step failed")

    monkeypatch.setattr(lk, "_load_prose", _boom)

    with caplog.at_level(logging.INFO, logger="egpt_retail.load_knowledge"):
        with pytest.raises(SystemExit, match="prose step failed"):
            lk.load_knowledge(RetailSettings(), driver=object(), knowledge=object())

    # The ontology step's own line reached the log before the prose step raised -- not only
    # in the return value, which this SystemExit never delivers to any caller.
    assert any(
        "ontology: 24 concepts at version 1" in record.message
        for record in caplog.records
    )
