"""Unit tests for R7's record_provenance seam (G4) -- the note_* helpers, run_id/model_tier
resolution, and the shape-parity check against the API contract. No database: the actual
write is exercised only by the cookbook (cookbook/94_retail/R07_executor_library.py).
"""

from __future__ import annotations

import asyncio
import logging

import pytest
import yaml
from egpt.answer_provenance import ProvenanceRecord
from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.executors import provenance as provenance_module
from egpt_retail.executors.provenance import (
    PROV_KEY,
    _bag,
    _model_tier,
    _run_id,
    note_dataset_read,
    note_freshness,
    note_retrieval_hit,
    note_row_sample,
    note_sql_execution,
    record_provenance,
)


class _FakeEngine:
    """A stand-in for the real ``Engine`` build_control_plane_engine returns -- disposed but
    never connected to, since these tests patch the write function itself."""

    def dispose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# note_* helpers populate additional_data["prov"]
# ---------------------------------------------------------------------------


def test_note_dataset_read_appends_with_default_schema():
    step_input = StepInput()
    note_dataset_read(step_input, table="crm_contact")
    assert step_input.additional_data[PROV_KEY]["datasets_read"] == [
        {"schema": "silver", "table": "crm_contact"}
    ]


def test_note_sql_execution_carries_the_guard_decision():
    step_input = StepInput()
    note_sql_execution(step_input, statement="SELECT 1", row_count=1, guard="allowed")
    entry = step_input.additional_data[PROV_KEY]["sql_executed"][0]
    assert entry == {"statement": "SELECT 1", "row_count": 1, "guard": "allowed"}


def test_note_row_sample_appends():
    step_input = StepInput()
    note_row_sample(step_input, table="crm_contact", rows=[{"id": 1}])
    assert step_input.additional_data[PROV_KEY]["rows_sampled"] == [
        {"table": "crm_contact", "rows": [{"id": 1}]}
    ]


def test_note_freshness_appends():
    step_input = StepInput()
    note_freshness(step_input, table="crm_contact", as_of="2026-09-09T00:00:00+00:00")
    assert step_input.additional_data[PROV_KEY]["freshness"] == [
        {"table": "crm_contact", "as_of": "2026-09-09T00:00:00+00:00"}
    ]


def test_note_retrieval_hit_appends():
    step_input = StepInput()
    note_retrieval_hit(
        step_input, source="memory", id="m1", score=0.9, origin="customer"
    )
    assert step_input.additional_data[PROV_KEY]["retrieval_hits"] == [
        {"source": "memory", "id": "m1", "score": 0.9, "origin": "customer"}
    ]


def test_multiple_notes_from_different_callers_share_one_bag():
    """The whole point of a public convention over a private bag: two unrelated calls
    contribute to the same run's record."""
    step_input = StepInput()
    note_dataset_read(step_input, table="crm_contact")
    note_sql_execution(step_input, statement="SELECT 1")
    bag = step_input.additional_data[PROV_KEY]
    assert len(bag["datasets_read"]) == 1
    assert len(bag["sql_executed"]) == 1


def test_notes_work_when_additional_data_starts_as_none():
    step_input = StepInput(additional_data=None)
    note_dataset_read(step_input, table="crm_contact")
    assert step_input.additional_data[PROV_KEY]["datasets_read"] == [
        {"schema": "silver", "table": "crm_contact"}
    ]


def test_bag_fills_in_missing_lists_rather_than_raising():
    """An executor that seeded additional_data["prov"] itself, in some partial shape, instead
    of through the note_* helpers, must not turn a later note_* call -- or record_provenance
    itself -- into a KeyError. That would contradict record_provenance's own "never refuses"
    docstring."""
    step_input = StepInput(additional_data={PROV_KEY: {"datasets_read": []}})
    note_sql_execution(step_input, statement="SELECT 1")
    bag = _bag(step_input)
    assert bag["sql_executed"] == [{"statement": "SELECT 1"}]
    assert bag["rows_sampled"] == []
    assert bag["freshness"] == []
    assert bag["retrieval_hits"] == []


# ---------------------------------------------------------------------------
# run_id resolution
# ---------------------------------------------------------------------------


def test_run_id_uses_explicit_override():
    step_input = StepInput()
    assert _run_id(step_input, {"run_id": "run_pinned"}) == "run_pinned"


def test_run_id_falls_back_to_a_fresh_uuid_and_warns(caplog):
    """StepInput has no live run id to fall back to (workflow_session is the session, not the
    run) -- a fresh id is generated, and this is logged, because it will not correlate with
    WorkflowRunOutput.run_id unless the caller also pins additional_data["run_id"]."""
    step_input = StepInput()
    with caplog.at_level(logging.WARNING):
        run_id = _run_id(step_input, {})
    assert run_id  # non-empty
    assert _run_id(step_input, {}) != run_id  # a second call generates a different one
    assert any("run_id" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# model_tier resolution
# ---------------------------------------------------------------------------


def test_model_tier_uses_explicit_override():
    step_input = StepInput()
    assert _model_tier(step_input, {"model_tier": "small"}) == "small"


def test_model_tier_rejects_an_invalid_override():
    step_input = StepInput()
    with pytest.raises(ValueError):
        _model_tier(step_input, {"model_tier": "huge"})


def test_model_tier_defaults_to_none_with_no_upstream_model_step():
    step_input = StepInput(previous_step_outputs={})
    assert _model_tier(step_input, {}) == "none"


def test_model_tier_warns_when_a_model_ran_with_no_tier_supplied(caplog):
    ran_a_model = StepOutput(content="explained", metrics=object())
    step_input = StepInput(previous_step_outputs={"explain": ran_a_model})
    with caplog.at_level(logging.WARNING):
        tier = _model_tier(step_input, {})
    assert tier == "none"
    assert any("model_tier" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# record_provenance itself -- the note_* bag is what actually reaches the written record
# ---------------------------------------------------------------------------


def test_record_provenance_writes_the_note_star_bag_not_a_bypass(monkeypatch):
    """A mutation that reads a raw additional_data key instead of the note_*-populated bag
    would pass every note_*-only test above -- this is the one that catches it, by asserting
    on what actually reaches awrite_provenance."""
    written = {}

    async def fake_awrite_provenance(engine, record):
        written["record"] = record

    monkeypatch.setattr(provenance_module, "awrite_provenance", fake_awrite_provenance)
    monkeypatch.setattr(
        provenance_module, "build_control_plane_engine", lambda settings: _FakeEngine()
    )

    step_input = StepInput(
        additional_data={"run_id": "run_x", "semantic_snapshot": "s.1"}
    )
    note_dataset_read(step_input, table="orders")
    note_sql_execution(step_input, statement="SELECT 1", guard="allowed")

    output = asyncio.run(record_provenance(step_input))

    record = written["record"]
    assert record.run_id == "run_x"
    assert record.datasets_read == ({"schema": "silver", "table": "orders"},)
    assert record.sql_executed == ({"statement": "SELECT 1", "guard": "allowed"},)
    assert output.content == {"provenance_ref": "run_x"}


def test_record_provenance_disposes_its_engine(monkeypatch):
    disposed = []

    async def fake_awrite_provenance(engine, record):
        pass

    class _TrackedEngine(_FakeEngine):
        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(provenance_module, "awrite_provenance", fake_awrite_provenance)
    monkeypatch.setattr(
        provenance_module,
        "build_control_plane_engine",
        lambda settings: _TrackedEngine(),
    )

    asyncio.run(record_provenance(StepInput(additional_data={"run_id": "run_y"})))

    assert disposed == [True]


# ---------------------------------------------------------------------------
# Shape parity: the framework's ProvenanceRecord matches common.yaml#Provenance
# ---------------------------------------------------------------------------


def test_provenance_record_fields_match_the_common_yaml_schema():
    """The framework dataclass is deliberately untyped-beyond-top-level relative to the
    generated pydantic model -- this pins that its field *names* still agree with the
    contract, so a schema change doesn't silently drift from what R7 actually writes."""
    import pathlib

    schema_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "egpt_retail"
        / "api"
        / "openapi"
        / "schemas"
        / "common.yaml"
    )
    schemas = yaml.safe_load(schema_path.read_text())
    contract_fields = set(schemas["Provenance"]["properties"].keys())

    record_fields = {f for f in ProvenanceRecord.__dataclass_fields__}

    assert contract_fields == record_fields
