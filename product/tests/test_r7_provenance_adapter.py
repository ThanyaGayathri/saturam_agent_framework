"""Unit tests for R7's record_provenance seam (G4) -- the note_* helpers, run_id/model_tier
resolution, and the shape-parity check against the API contract. No database: the actual
write is exercised only by the cookbook (cookbook/94_retail/R07_executor_library.py).
"""

from __future__ import annotations

import logging

import pytest
import yaml
from egpt.answer_provenance import ProvenanceRecord
from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.executors.provenance import (
    PROV_KEY,
    _model_tier,
    _run_id,
    note_dataset_read,
    note_freshness,
    note_retrieval_hit,
    note_row_sample,
    note_sql_execution,
)

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


# ---------------------------------------------------------------------------
# run_id resolution
# ---------------------------------------------------------------------------


def test_run_id_uses_explicit_override():
    step_input = StepInput()
    assert _run_id(step_input, {"run_id": "run_pinned"}) == "run_pinned"


def test_run_id_falls_back_to_a_fresh_uuid():
    step_input = StepInput()
    run_id = _run_id(step_input, {})
    assert run_id  # non-empty
    assert _run_id(step_input, {}) != run_id  # a second call generates a different one


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
