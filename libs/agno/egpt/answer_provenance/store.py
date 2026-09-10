"""Reading and writing provenance records (R7, G4).

Plain SQL over a bare ``Engine``, matching ``egpt_retail/warehouse/loads.py``'s
open/close-run pattern: this module knows nothing about workflows, executors or the retail
product -- ``product/egpt_retail/executors/provenance.py`` is the seam that assembles a
:class:`ProvenanceRecord` from a running step and calls in here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from egpt.answer_provenance.ddl import PROVENANCE_TABLE
from egpt.answer_provenance.record import ProvenanceRecord

try:
    from sqlalchemy import text
    from sqlalchemy.engine import Engine
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Run `pip install sqlalchemy` or `pip install egpt[sql]`.")

__all__ = ["write_provenance", "awrite_provenance", "read_provenance", "aread_provenance"]

_INSERT = f"""
INSERT INTO {PROVENANCE_TABLE}
    (run_id, use_case_id, produced_at, semantic_snapshot, vocabulary_version, skill,
     model_tier, datasets_read, sql_executed, rows_sampled, freshness, retrieval_hits)
VALUES
    (:run_id, :use_case_id, :produced_at, :semantic_snapshot, :vocabulary_version,
     CAST(:skill AS jsonb), :model_tier, CAST(:datasets_read AS jsonb),
     CAST(:sql_executed AS jsonb), CAST(:rows_sampled AS jsonb), CAST(:freshness AS jsonb),
     CAST(:retrieval_hits AS jsonb))
"""

_SELECT = f"SELECT * FROM {PROVENANCE_TABLE} WHERE run_id = :run_id"


def _json_or_null(value: object) -> Optional[str]:
    return None if value is None else json.dumps(value, default=str)


def write_provenance(engine: Engine, record: ProvenanceRecord) -> None:
    """Write one provenance record. No ``ON CONFLICT``: a second write for a ``run_id`` that
    already has one is a defect ("every answer writes **one** record"), not something to
    silently overwrite -- the primary-key violation is the correct, loud outcome."""
    payload = record.to_dict()
    params = {
        "run_id": payload["run_id"],
        "use_case_id": payload["use_case_id"],
        "produced_at": record.produced_at,
        "semantic_snapshot": payload["semantic_snapshot"],
        "vocabulary_version": payload["vocabulary_version"],
        "skill": _json_or_null(payload["skill"]),
        "model_tier": payload["model_tier"],
        "datasets_read": json.dumps(payload["datasets_read"], default=str),
        "sql_executed": json.dumps(payload["sql_executed"], default=str),
        "rows_sampled": _json_or_null(payload["rows_sampled"]),
        "freshness": _json_or_null(payload["freshness"]),
        "retrieval_hits": _json_or_null(payload["retrieval_hits"]),
    }
    with engine.begin() as connection:
        connection.execute(text(_INSERT), params)


async def awrite_provenance(engine: Engine, record: ProvenanceRecord) -> None:
    """:func:`write_provenance` off the event loop."""
    await asyncio.to_thread(write_provenance, engine, record)


def read_provenance(engine: Engine, run_id: str) -> Optional[ProvenanceRecord]:
    """The record for one run, or ``None`` if it has none.

    ``None`` here is a real, honest answer at this layer -- the same "absence versus a defect"
    distinction the API contract's own ``GET /runs/{run_id}/provenance`` 404 makes belongs to
    whichever caller renders the 404, not to this plain read.
    """
    with engine.connect() as connection:
        row = connection.execute(text(_SELECT), {"run_id": run_id}).mappings().first()
    return ProvenanceRecord.from_row(dict(row)) if row is not None else None


async def aread_provenance(engine: Engine, run_id: str) -> Optional[ProvenanceRecord]:
    """:func:`read_provenance` off the event loop."""
    return await asyncio.to_thread(read_provenance, engine, run_id)
