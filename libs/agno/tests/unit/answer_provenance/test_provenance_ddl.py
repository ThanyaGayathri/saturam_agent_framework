"""Unit tests for R7's answer-provenance record and DDL (G4): egpt.answer_provenance.

No database -- the DDL text is checked for idempotence the same way
product/tests/test_r4_sql_assets.py checks the warehouse's, and ProvenanceRecord's
construction/round-trip is checked in isolation.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy", reason="egpt.answer_provenance writes through a bare Engine")

from egpt.answer_provenance.ddl import _DDL, PROVENANCE_TABLE  # noqa: E402
from egpt.answer_provenance.record import MODEL_TIERS, ProvenanceRecord  # noqa: E402

# ---------------------------------------------------------------------------
# DDL is idempotent by construction
# ---------------------------------------------------------------------------


def test_table_name_is_prefixed_egpt_answer_provenance():
    assert PROVENANCE_TABLE == "egpt_answer_provenance"


def test_ddl_uses_create_table_if_not_exists():
    assert "CREATE TABLE IF NOT EXISTS" in _DDL


def test_ddl_has_no_non_idempotent_create():
    """Mirrors test_r4_sql_assets.py's own check: no bare CREATE that would fail on a second
    run, other than the guarded CREATE TABLE IF NOT EXISTS."""
    bad = re.findall(r"CREATE\s+(?!TABLE\s+IF)([A-Z ]+?)\s", _DDL)
    assert not bad, bad


def test_ddl_declares_the_model_tier_check_constraint():
    assert "CHECK (model_tier IN ('none', 'small', 'frontier'))" in _DDL


# ---------------------------------------------------------------------------
# ProvenanceRecord construction and validation
# ---------------------------------------------------------------------------


def _record(**overrides) -> ProvenanceRecord:
    defaults = dict(
        run_id="run_0001",
        produced_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        semantic_snapshot="r3-contract.1",
        model_tier="none",
    )
    defaults.update(overrides)
    return ProvenanceRecord(**defaults)


def test_minimal_record_defaults_datasets_and_sql_to_empty():
    record = _record()
    assert record.datasets_read == ()
    assert record.sql_executed == ()
    assert record.rows_sampled is None


def test_model_tier_none_is_the_no_model_evidence_shape():
    """R7's own "the workflow completes without a model" evidence is this exact value."""
    assert "none" in MODEL_TIERS
    assert _record(model_tier="none").model_tier == "none"


@pytest.mark.parametrize("bad_tier", ["small-ish", "", "None", "SMALL"])
def test_invalid_model_tier_rejected_at_construction(bad_tier):
    with pytest.raises(ValueError):
        _record(model_tier=bad_tier)


def test_empty_run_id_rejected_at_construction():
    with pytest.raises(ValueError):
        _record(run_id="")


def test_to_dict_matches_the_common_yaml_provenance_field_set():
    """One field per common.yaml#Provenance property -- a name drifting from the contract
    would leave an extra or missing key here."""
    record = _record(
        use_case_id="uc_0001",
        datasets_read=({"schema": "silver", "table": "orders"},),
        sql_executed=({"statement": "SELECT 1", "row_count": 1},),
        rows_sampled=({"table": "orders", "rows": [{"id": 1}]},),
        freshness=({"table": "orders", "as_of": "2026-09-09T00:00:00+00:00"},),
        retrieval_hits=({"source": "memory", "id": "m1", "score": 0.9},),
    )
    payload = record.to_dict()
    assert set(payload.keys()) == {
        "run_id",
        "use_case_id",
        "produced_at",
        "semantic_snapshot",
        "vocabulary_version",
        "skill",
        "model_tier",
        "datasets_read",
        "sql_executed",
        "rows_sampled",
        "freshness",
        "retrieval_hits",
    }
    assert payload["datasets_read"] == [{"schema": "silver", "table": "orders"}]


def test_from_row_round_trips_to_dict():
    record = _record(use_case_id="uc_0001", datasets_read=({"table": "orders"},))
    row = {
        "run_id": record.run_id,
        "use_case_id": record.use_case_id,
        "produced_at": record.produced_at,
        "semantic_snapshot": record.semantic_snapshot,
        "vocabulary_version": None,
        "skill": None,
        "model_tier": record.model_tier,
        "datasets_read": [{"table": "orders"}],
        "sql_executed": [],
        "rows_sampled": None,
        "freshness": None,
        "retrieval_hits": None,
    }
    rebuilt = ProvenanceRecord.from_row(row)
    assert rebuilt.to_dict() == record.to_dict()
